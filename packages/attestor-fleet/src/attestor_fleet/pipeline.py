"""The review pipeline: intake → triage → parallel drafting → assemble.

**Why this is a workflow, not an LLM deciding what to do next.** The sequence is known
in advance: you cannot draft before you triage, and you cannot assemble before you draft.
Handing that to an LLM router would add latency, cost, and non-determinism in exchange
for nothing. Drafting across departments, by contrast, is embarrassingly parallel — no
question's answer depends on another's — so it fans out. LLM judgement is reserved for
the Orchestrator's genuine decisions (see `orchestrator.py`).

That split is a scored architectural point; it is written up in
`docs/decisions/ADR-0002-deterministic-pipeline.md`.

Phase 3 runs this locally. Phase 4 drives the same stages from Pub/Sub, and Phase 5
deploys them; nothing here assumes an execution environment.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from attestor_core.domain import (
    Answer,
    AnswerStatus,
    Confidence,
    ContradictionVerdict,
    Department,
    Evidence,
    Question,
)
from attestor_core.errors import PolicyViolation
from attestor_core.policy import (
    AnswerFacts,
    ConfidenceSignals,
    compute_confidence,
    requires_human,
)
from attestor_fleet.callbacks.audit import (
    ANSWER_ASSEMBLED,
    ANSWER_DRAFTED,
    ARMOR_BLOCKED,
    CONSISTENCY_CHECKED,
    EVIDENCE_RETRIEVED,
    HUMAN_REQUIRED,
    QUESTION_TRIAGED,
    TOOL_DENIED,
    AuditSink,
    NullAuditSink,
)
from attestor_fleet.callbacks.budget import BudgetLedger
from attestor_fleet.callbacks.guard import ArmorGuard
from attestor_fleet.prompts.drafting import (
    consistency_prompt,
    drafting_prompt,
    is_hedged,
    triage_prompt,
)
from attestor_platform.config import REASONING_MODEL, TRIAGE_MODEL, genai_client
from attestor_platform.search import ExpandingCorpusSearch, QueryExpander, SearchUnavailable

logger = logging.getLogger(__name__)

#: The model's way of saying the corpus does not support an answer.
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: Questions per triage call. Batching is the whole cost story: ~312 questions in
#: batches of 40 is 8 flash-lite calls, not 312.
TRIAGE_BATCH = 40

#: Parallel drafting fan-out. Measured in Phase 1: a single 3.7-flash call is ~9s cold,
#: so 40 sequential drafts would be six minutes and kill the demo. At this concurrency
#: it is well under a minute.
DRAFT_CONCURRENCY = 8

_TRIAGE_LINE = re.compile(r"^\s*(\d+)\s*\|\s*([a-z_]+)\s*$", re.MULTILINE)

_DEPARTMENTS = {d.value: d for d in Department}


@dataclass
class QuestionOutcome:
    """Everything that happened to one question."""

    question: Question
    answer: Answer | None = None
    evidence: list[Evidence] = field(default_factory=list)
    blocked: bool = False
    denied: bool = False
    needs_human: bool = False
    contradiction: ContradictionVerdict = ContradictionVerdict.NO_CONTRADICTION
    constrained: bool = False
    error: str | None = None
    draft_seconds: float = 0.0


@dataclass
class RunReport:
    """Measured outcome of one pipeline run. These are the demo numbers."""

    review_id: str
    run_id: str
    outcomes: list[QuestionOutcome] = field(default_factory=list)
    triage_seconds: float = 0.0
    draft_seconds: float = 0.0
    total_seconds: float = 0.0
    budget: dict[str, object] = field(default_factory=dict)
    draft_latencies: list[float] = field(default_factory=list)

    @property
    def answered(self) -> list[QuestionOutcome]:
        return [o for o in self.outcomes if o.answer is not None]

    @property
    def cited(self) -> list[QuestionOutcome]:
        return [o for o in self.answered if o.answer and o.answer.citations]

    @property
    def flagged_no_evidence(self) -> list[QuestionOutcome]:
        return [
            o
            for o in self.answered
            if o.answer and o.answer.status is AnswerStatus.FLAGGED_NO_EVIDENCE
        ]

    @property
    def blocked(self) -> list[QuestionOutcome]:
        return [o for o in self.outcomes if o.blocked]

    @property
    def needs_human(self) -> list[QuestionOutcome]:
        return [o for o in self.outcomes if o.needs_human]

    def latency_percentile(self, percentile: float) -> float:
        """p50/p95 drafting latency, for the demo-readiness decision."""
        if not self.draft_latencies:
            return 0.0
        ordered = sorted(self.draft_latencies)
        index = min(len(ordered) - 1, round(percentile / 100 * (len(ordered) - 1)))
        return ordered[index]


class ReviewPipeline:
    """Runs a questionnaire end to end.

    Composition is explicit rather than magic: the caller supplies the guard, the audit
    sink, and the ledger, which is what lets `adk web`, the tests, and Phase 4's
    dispatcher all drive the same code with different wiring.
    """

    def __init__(
        self,
        review_id: str,
        run_id: str,
        *,
        guard: ArmorGuard | None = None,
        audit: AuditSink | None = None,
        ledger: BudgetLedger | None = None,
        expander: QueryExpander | None = None,
        prior_commitments: Sequence[tuple[str, str]] = (),
        screen_ingress: bool = True,
        screen_tool_output: bool = True,
    ) -> None:
        self.review_id = review_id
        self.run_id = run_id
        self.guard = guard
        self.audit: AuditSink = audit if audit is not None else NullAuditSink()
        self.ledger = ledger if ledger is not None else BudgetLedger(review_id=review_id)
        self._expander = expander if expander is not None else QueryExpander()
        #: (question_id, statement) pairs from earlier rounds.
        self.prior_commitments = list(prior_commitments)
        self.screen_ingress = screen_ingress
        self.screen_tool_output = screen_tool_output
        self._searches: dict[Department, ExpandingCorpusSearch] = {}
        self._client = genai_client()

    # -- retrieval ------------------------------------------------------------------

    def _search_for(self, department: Department) -> ExpandingCorpusSearch:
        """One search object per department, bound at construction.

        The binding IS the access boundary: a drafter is handed the search for its own
        department and has no handle on any other.
        """
        if department not in self._searches:
            self._searches[department] = ExpandingCorpusSearch(department, expander=self._expander)
        return self._searches[department]

    # -- model plumbing -------------------------------------------------------------

    def _generate(self, model: str, prompt: str) -> str:
        response = self._client.models.generate_content(model=model, contents=prompt)
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self.ledger.record_usage(
                model,
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        return (response.text or "").strip()

    # -- stage 1: triage ------------------------------------------------------------

    def triage(self, questions: list[Question]) -> list[Question]:
        """Classify every question by department, in batches, on the cheap tier.

        This is the cost argument made real: ~312 classifications for the price of 8
        flash-lite calls, against ~40 expensive drafting calls.
        """
        started = time.perf_counter()
        assigned: list[Question] = []

        for offset in range(0, len(questions), TRIAGE_BATCH):
            batch = questions[offset : offset + TRIAGE_BATCH]
            prompt = triage_prompt([(i, q.text) for i, q in enumerate(batch)])
            try:
                raw = self._generate(TRIAGE_MODEL, prompt)
            except Exception as exc:
                logger.warning("triage batch failed, defaulting to unassigned: %s", exc)
                raw = ""

            parsed: dict[int, Department] = {}
            for index_text, dept_text in _TRIAGE_LINE.findall(raw):
                department = _DEPARTMENTS.get(dept_text.strip())
                if department is not None:
                    parsed[int(index_text)] = department

            for index, question in enumerate(batch):
                department = parsed.get(index, Department.UNASSIGNED)
                assigned.append(question.model_copy(update={"department": department}))
                self.audit.write(
                    kind=QUESTION_TRIAGED,
                    review_id=self.review_id,
                    run_id=self.run_id,
                    question_id=question.question_id,
                    actor="TriageAgent",
                    detail={"department": department.value, "model": TRIAGE_MODEL},
                )

        logger.info("triaged %d questions in %.1fs", len(assigned), time.perf_counter() - started)
        return assigned

    # -- stage 2: draft one question -------------------------------------------------

    def draft(self, question: Question) -> QuestionOutcome:
        """Retrieve, screen, draft, check consistency, score confidence."""
        outcome = QuestionOutcome(question=question)
        started = time.perf_counter()

        # --- ingress screening: the raw cell, before anything reads it ---------------
        if self.guard is not None and self.screen_ingress:
            screen = self.guard.screen_prompt(question.raw_text)
            if screen.blocked:
                outcome.blocked = True
                self.audit.write(
                    kind=ARMOR_BLOCKED,
                    review_id=self.review_id,
                    run_id=self.run_id,
                    question_id=question.question_id,
                    actor="ArmorGuard",
                    detail={
                        "surface": screen.surface,
                        "decision": screen.decision.value,
                        "matched_filters": list(screen.matched_filters),
                        "chunk_index": screen.chunk_index,
                        "excerpt": screen.excerpt,
                    },
                )
                outcome.answer = Answer(
                    question_id=question.question_id,
                    round_id=self.run_id,
                    text="Quarantined: Model Armor blocked this question.",
                    citations=[],
                    confidence=Confidence.LOW,
                    status=AnswerStatus.QUARANTINED,
                    authored_by="ArmorGuard",
                )
                outcome.needs_human = True
                outcome.draft_seconds = time.perf_counter() - started
                return outcome

        department = (
            question.department
            if question.department is not Department.UNASSIGNED
            else Department.SECURITY
        )

        # --- retrieval, through expansion ------------------------------------------
        try:
            result = self._search_for(department).retrieve(
                question.text, question_id=question.question_id
            )
            evidence = result.evidence
        except PolicyViolation as exc:
            outcome.denied = True
            outcome.error = str(exc)
            self.audit.write(
                kind=TOOL_DENIED,
                review_id=self.review_id,
                run_id=self.run_id,
                question_id=question.question_id,
                actor=f"{department.value}Agent",
                detail={"reason": str(exc)},
            )
            outcome.draft_seconds = time.perf_counter() - started
            return outcome
        except SearchUnavailable as exc:
            # NOT "no evidence" -- a retrieval outage must never masquerade as an
            # answer of "we have no policy on this".
            outcome.error = str(exc)
            outcome.needs_human = True
            outcome.draft_seconds = time.perf_counter() - started
            return outcome

        # --- egress screening on retrieved content: the tool-poisoning defence -------
        if self.guard is not None and self.screen_tool_output and evidence:
            joined = "\n\n".join(e.content for e in evidence)
            screen = self.guard.screen_tool_output(joined)
            if screen.blocked:
                outcome.blocked = True
                self.audit.write(
                    kind=ARMOR_BLOCKED,
                    review_id=self.review_id,
                    run_id=self.run_id,
                    question_id=question.question_id,
                    actor="ArmorGuard",
                    detail={
                        "surface": screen.surface,
                        "decision": screen.decision.value,
                        "matched_filters": list(screen.matched_filters),
                        "chunk_index": screen.chunk_index,
                        "excerpt": screen.excerpt,
                    },
                )
                # Drop the poisoned evidence rather than the question: the corpus is
                # compromised, not the questionnaire.
                evidence = []

        outcome.evidence = evidence
        self.audit.write(
            kind=EVIDENCE_RETRIEVED,
            review_id=self.review_id,
            run_id=self.run_id,
            question_id=question.question_id,
            actor=f"{department.value}Agent",
            detail={
                "count": len(evidence),
                "documents": sorted({e.document_title for e in evidence}),
                "queries": list(result.queries_run) if evidence else [],
            },
        )

        # --- draft -------------------------------------------------------------------
        if not evidence:
            outcome.answer = self._no_evidence_answer(question)
            outcome.needs_human = True
            outcome.draft_seconds = time.perf_counter() - started
            self._audit_answer(outcome)
            return outcome

        try:
            text = self._generate(
                REASONING_MODEL, drafting_prompt(department, question.text, evidence)
            )
        except Exception as exc:
            logger.warning("draft failed for %s: %s", question.question_id, exc)
            outcome.error = str(exc)
            outcome.answer = self._no_evidence_answer(question)
            outcome.needs_human = True
            outcome.draft_seconds = time.perf_counter() - started
            return outcome

        if not text or INSUFFICIENT in text:
            outcome.answer = self._no_evidence_answer(question)
            outcome.needs_human = True
            outcome.draft_seconds = time.perf_counter() - started
            self._audit_answer(outcome)
            return outcome

        # --- consistency against prior-round commitments -----------------------------
        verdict, constrained = self._check_consistency(question, text)
        outcome.contradiction = verdict
        outcome.constrained = constrained

        # --- confidence, computed never generated -------------------------------------
        citations = [e.to_citation(e.content[:400]) for e in evidence]
        scores = [c.retrieval_score for c in citations]
        signals = ConfidenceSignals(
            citation_count=len(citations),
            max_retrieval_score=max(scores),
            mean_retrieval_score=sum(scores) / len(scores),
            agent_hedged=is_hedged(text),
            contradiction=verdict,
            cross_departmental=question.department is Department.UNASSIGNED,
        )
        confidence = compute_confidence(signals)

        facts = AnswerFacts(
            confidence=confidence,
            citation_count=len(citations),
            contradiction=verdict,
            touches_prior_commitment=bool(self._commitments_for(question)),
        )
        escalate = requires_human(facts, prior_commitments=len(self.prior_commitments))

        outcome.answer = Answer(
            question_id=question.question_id,
            round_id=self.run_id,
            text=text,
            citations=citations,
            confidence=confidence,
            status=AnswerStatus.NEEDS_HUMAN if escalate else AnswerStatus.DRAFTED,
            authored_by=f"{department.value.capitalize()}Agent",
        )
        outcome.needs_human = escalate
        outcome.draft_seconds = time.perf_counter() - started
        self._audit_answer(outcome)
        return outcome

    def _no_evidence_answer(self, question: Question) -> Answer:
        """The system saying it does not know. Zero citations is legal ONLY here."""
        return Answer(
            question_id=question.question_id,
            round_id=self.run_id,
            text="No supporting evidence was found in the corpus for this question.",
            citations=[],
            confidence=Confidence.LOW,
            status=AnswerStatus.FLAGGED_NO_EVIDENCE,
            authored_by="EvidenceAgent",
        )

    def _commitments_for(self, question: Question) -> list[str]:
        """Prior-round commitments matching this question by content-derived id."""
        return [
            statement
            for question_id, statement in self.prior_commitments
            if question_id == question.question_id
        ]

    def _check_consistency(
        self, question: Question, draft: str
    ) -> tuple[ContradictionVerdict, bool]:
        """Obtain a `ContradictionVerdict` -- the implementing side of the core port.

        `core.policy` defines the verdict and decides given one; getting it requires a
        model call, which belongs here.
        """
        commitments = self._commitments_for(question)
        if not commitments:
            return ContradictionVerdict.NO_CONTRADICTION, False

        try:
            raw = self._generate(REASONING_MODEL, consistency_prompt(draft, commitments))
        except Exception as exc:
            logger.warning("consistency check failed for %s: %s", question.question_id, exc)
            # Fails closed: UNKNOWN caps confidence at LOW and forces a human look.
            return ContradictionVerdict.UNKNOWN, False

        head = raw.splitlines()[0].strip().upper() if raw else ""
        verdict = {
            "CONTRADICTION": ContradictionVerdict.CONTRADICTION,
            "POSSIBLE_CONTRADICTION": ContradictionVerdict.POSSIBLE_CONTRADICTION,
            "NO_CONTRADICTION": ContradictionVerdict.NO_CONTRADICTION,
        }.get(head, ContradictionVerdict.UNKNOWN)

        # `constrained` is the field the demo turns on: not "we checked" but "we checked
        # and it changed the answer".
        constrained = verdict in {
            ContradictionVerdict.CONTRADICTION,
            ContradictionVerdict.POSSIBLE_CONTRADICTION,
        }

        self.audit.write(
            kind=CONSISTENCY_CHECKED,
            review_id=self.review_id,
            run_id=self.run_id,
            question_id=question.question_id,
            actor="AssemblerAgent",
            detail={
                "verdict": verdict.value,
                "constrained": constrained,
                "prior_statements": commitments,
                "justification": raw.splitlines()[1].strip() if len(raw.splitlines()) > 1 else "",
            },
        )
        return verdict, constrained

    def _audit_answer(self, outcome: QuestionOutcome) -> None:
        answer = outcome.answer
        if answer is None:
            return
        self.audit.write(
            kind=ANSWER_DRAFTED,
            review_id=self.review_id,
            run_id=self.run_id,
            question_id=answer.question_id,
            actor=answer.authored_by,
            detail={
                "status": answer.status.value,
                "confidence": answer.confidence.value,
                "citation_count": len(answer.citations),
            },
        )
        if outcome.needs_human:
            self.audit.write(
                kind=HUMAN_REQUIRED,
                review_id=self.review_id,
                run_id=self.run_id,
                question_id=answer.question_id,
                actor="AssemblerAgent",
                detail={"reason": answer.status.value, "confidence": answer.confidence.value},
            )

    # -- stage 3: run -----------------------------------------------------------------

    def run(self, questions: list[Question]) -> RunReport:
        """Triage, then draft in parallel, then assemble the report."""
        started = time.perf_counter()
        report = RunReport(review_id=self.review_id, run_id=self.run_id)

        triage_started = time.perf_counter()
        triaged = self.triage(questions)
        report.triage_seconds = time.perf_counter() - triage_started

        draft_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=DRAFT_CONCURRENCY) as pool:
            report.outcomes = list(pool.map(self.draft, triaged))
        report.draft_seconds = time.perf_counter() - draft_started

        report.draft_latencies = [o.draft_seconds for o in report.outcomes if o.draft_seconds]
        report.total_seconds = time.perf_counter() - started
        report.budget = self.ledger.summary()

        self.audit.write(
            kind=ANSWER_ASSEMBLED,
            review_id=self.review_id,
            run_id=self.run_id,
            actor="AssemblerAgent",
            detail={
                "questions": len(report.outcomes),
                "answered": len(report.answered),
                "cited": len(report.cited),
                "flagged_no_evidence": len(report.flagged_no_evidence),
                "blocked": len(report.blocked),
                "needs_human": len(report.needs_human),
            },
        )
        return report
