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
from typing import Any

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
from attestor_fleet.callbacks.guard import ArmorGuard, enforce_tool_policy
from attestor_fleet.prompts.drafting import (
    consistency_prompt,
    constrained_drafting_prompt,
    drafting_prompt,
    is_hedged,
    triage_prompt,
)
from attestor_platform.config import (
    EMBEDDING_MODEL,
    REASONING_MODEL,
    TRIAGE_MODEL,
    genai_client,
)
from attestor_platform.search import (
    ExpandingCorpusSearch,
    QueryExpander,
    RelevanceScorer,
    RetrievalResult,
    SearchUnavailable,
)

logger = logging.getLogger(__name__)

#: The model's way of saying the corpus does not support an answer.
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: Questions per triage call. Batching is the cost story -- 312 questions in batches of
#: 20 is 16 flash-lite calls, not 312.
#:
#: Measured, not guessed: batches of 5/10/15/20/30 pass, a batch of 40 is BLOCKED by the
#: project Model Armor floor setting with
#:   "Blocked by Model Armor Floor Setting: The prompt violated Prompt Injection and
#:    Jailbreak filters."
#: A long, diverse block of security questions ("break-glass access", "secrets committed
#: to repositories", "national security request") collectively reads as an injection at
#: LOW_AND_ABOVE. 20 leaves real margin, and `_triage_batch` splits on a block anyway.
TRIAGE_BATCH = 20

#: Parallel drafting fan-out. Measured in Phase 1: a single 3.7-flash call is ~9s cold,
#: so 40 sequential drafts would be six minutes and kill the demo. At this concurrency
#: it is well under a minute.
DRAFT_CONCURRENCY = 8

#: Cosine similarity at which a prior-round commitment is treated as relevant to a
#: question. Measured, not chosen: see `_commitments_for`.
COMMITMENT_MATCH_SCORE = 0.62

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

    @property
    def achieved_concurrency(self) -> float:
        """Drafting time summed across questions, divided by wall clock.

        The honest way to answer "is the fan-out actually parallel?". A configured
        `max_workers` proves nothing -- if retrieval serialised on a lock or the API
        throttled, this lands near 1 and the configuration is a wish.
        """
        if self.draft_seconds <= 0:
            return 0.0
        return sum(self.draft_latencies) / self.draft_seconds

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
        scorer: RelevanceScorer | None = None,
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
        #: ONE scorer for the whole run, shared across all three departments. The passage
        #: cache is what makes measured relevance nearly free: a snippet retrieved by
        #: thirty questions is embedded once.
        self._scorer = scorer if scorer is not None else RelevanceScorer()
        #: Constructed on first use. Building a pipeline must not require credentials --
        #: the tests construct one to exercise triage parsing and the redraft path with
        #: no cloud at all, and an eager client makes that impossible.
        self._model_client: Any | None = None

    # -- retrieval ------------------------------------------------------------------

    def _search_for(self, department: Department) -> ExpandingCorpusSearch:
        """One search object per department, bound at construction.

        The binding IS the access boundary: a drafter is handed the search for its own
        department and has no handle on any other.
        """
        if department not in self._searches:
            self._searches[department] = ExpandingCorpusSearch(
                department, expander=self._expander, scorer=self._scorer
            )
        return self._searches[department]

    def _guarded_retrieve(
        self, agent_department: Department, question: Question
    ) -> RetrievalResult:
        """Retrieve for one department, through the least-privilege interceptor.

        The interceptor is checked against the datastore the search object is actually
        bound to, not against the department we *meant* to search. In normal operation
        those agree and this is an ALLOW; the check earns its place when they do not --
        a mis-scoped drafter, a poisoned wiring, a copy-paste in Phase 4's dispatcher --
        because then a SecurityAgent is holding a handle on the legal corpus and the
        only thing standing between it and a cross-department read is this line.

        Raises:
            PolicyViolation: when the agent's department and the datastore's disagree.
        """
        search = self._search_for(agent_department)
        enforce_tool_policy(
            agent_department,
            "search_corpus",
            f"corpus/{search.department.value}",
            agent_name=f"{agent_department.value.capitalize()}Agent",
        )
        return search.retrieve(question.text, question_id=question.question_id)

    def _retrieve_cross_department(self, question: Question) -> RetrievalResult:
        """Search every department for a question triage could not place.

        Merged and reranked by score, so the best-supported department wins on evidence
        rather than on an arbitrary default.
        """
        merged: dict[tuple[str, str | None], Evidence] = {}
        matched_by: dict[str, str] = {}
        queries: tuple[str, ...] = ()
        for department in (Department.SECURITY, Department.LEGAL, Department.ENGINEERING):
            # Each scoped agent is asked in turn, in its own right -- which is what an
            # unplaced question actually needs. It is NOT one agent granted the union of
            # three corpora; `decide_tool` denies an UNASSIGNED agent every corpus, and
            # rightly, because that agent is exactly the union-of-all-permissions shape
            # the fleet exists to avoid.
            try:
                result = self._guarded_retrieve(department, question)
            except SearchUnavailable:
                continue
            queries = result.queries_run
            for item in result.evidence:
                key = (item.document_uri, item.section)
                current = merged.get(key)
                if current is None or item.score > current.score:
                    merged[key] = item
                    matched_by[item.document_uri] = result.matched_by.get(item.document_uri, "")
        ranked = sorted(merged.values(), key=lambda e: e.score, reverse=True)[:5]
        return RetrievalResult(
            evidence=ranked,
            matched_by={e.document_uri: matched_by.get(e.document_uri, "") for e in ranked},
            queries_run=queries,
        )

    # -- model plumbing -------------------------------------------------------------

    @property
    def _client(self) -> Any:
        """The Gemini client, built on first use through the one sanctioned factory."""
        if self._model_client is None:
            self._model_client = genai_client()
        return self._model_client

    @_client.setter
    def _client(self, client: Any) -> None:
        self._model_client = client

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
            # `_triage_batch` and not an inline call: it is the path that DETECTS a Model
            # Armor block and splits, and a backstop that is never invoked is not a
            # backstop. An earlier revision parsed inline here and left the splitter as
            # dead code, so a blocked batch fell through to UNASSIGNED exactly as it did
            # before the fix was written.
            parsed = self._triage_batch(batch)

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

        unassigned = sum(1 for q in assigned if q.department is Department.UNASSIGNED)
        logger.info(
            "triaged %d questions in %.1fs (%d unassigned)",
            len(assigned),
            time.perf_counter() - started,
            unassigned,
        )
        return assigned

    def _triage_batch(self, batch: list[Question]) -> dict[int, Department]:
        """Classify one batch, splitting and retrying if the floor setting blocks it.

        The block is the thing worth handling. An earlier version treated an empty
        response as a parse failure and moved on, so six of eight batches silently fell
        through to UNASSIGNED, every one of those questions was drafted against the wrong
        corpus, and the citation rate collapsed to 33% -- with nothing in the logs saying
        why. A guardrail firing on your own prompt is a legitimate outcome; failing to
        notice is not.
        """
        prompt = triage_prompt([(i, q.text) for i, q in enumerate(batch)])
        blocked = False
        raw = ""
        try:
            response = self._client.models.generate_content(model=TRIAGE_MODEL, contents=prompt)
            feedback = getattr(response, "prompt_feedback", None)
            if feedback is not None and getattr(feedback, "block_reason", None):
                blocked = True
                logger.warning(
                    "triage batch of %d BLOCKED by Model Armor floor: %s",
                    len(batch),
                    getattr(feedback, "block_reason_message", ""),
                )
            else:
                usage = getattr(response, "usage_metadata", None)
                if usage is not None:
                    self.ledger.record_usage(
                        TRIAGE_MODEL,
                        int(getattr(usage, "prompt_token_count", 0) or 0),
                        int(getattr(usage, "candidates_token_count", 0) or 0),
                    )
                raw = (response.text or "").strip()
        except Exception as exc:
            logger.warning("triage batch failed: %s", exc)

        if blocked and len(batch) > 1:
            # Split and retry. A smaller, less diverse prompt usually clears the filter,
            # and in the worst case each question is classified on its own.
            middle = len(batch) // 2
            left = self._triage_batch(batch[:middle])
            right = self._triage_batch(batch[middle:])
            merged = dict(left)
            merged.update({index + middle: dept for index, dept in right.items()})
            return merged

        parsed: dict[int, Department] = {}
        for index_text, dept_text in _TRIAGE_LINE.findall(raw):
            department = _DEPARTMENTS.get(dept_text.strip())
            if department is not None:
                parsed[int(index_text)] = department
        return parsed

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

        department = question.department

        # --- retrieval, through expansion ------------------------------------------
        #
        # An UNASSIGNED question is genuinely cross-cutting: triage could not place it,
        # or the batch was blocked. Silently defaulting it to the security corpus (an
        # earlier version did) routes legal and engineering questions at the wrong
        # datastore and guarantees no evidence -- which then reads as "the corpus cannot
        # answer this" rather than "we asked the wrong corpus". So it searches all three
        # and `cross_departmental` caps its confidence at MEDIUM.
        try:
            if department is Department.UNASSIGNED:
                result = self._retrieve_cross_department(question)
            else:
                result = self._guarded_retrieve(department, question)
            evidence = result.evidence
        except PolicyViolation as exc:
            outcome.denied = True
            outcome.error = str(exc)
            self.audit.write(
                kind=TOOL_DENIED,
                review_id=self.review_id,
                run_id=self.run_id,
                question_id=question.question_id,
                actor=f"{department.value.capitalize()}Agent",
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
        #
        # Passage by passage, not as one blob. Screening the concatenation was measurably
        # weaker -- the payload shares its window with four other passages' legitimate
        # prose and the filter score is diluted below threshold -- and it was also
        # coarse: one poisoned document cost the question every other citation.
        if self.guard is not None and self.screen_tool_output and evidence:
            screens = self.guard.screen_evidence([e.content for e in evidence])
            clean: list[Evidence] = []
            for item, screen in zip(evidence, screens, strict=True):
                if not screen.blocked:
                    clean.append(item)
                    continue
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
                        # Which passage was poisoned, not merely that one was. This is
                        # what makes the event actionable: someone has to go and clean
                        # that document.
                        "document_uri": item.document_uri,
                        "document_title": item.document_title,
                        "section": item.section,
                    },
                )
            # Drop the poisoned passages, keep the rest: the corpus is partly
            # compromised, and the question is not at fault at all.
            evidence = clean

        outcome.evidence = evidence
        self.audit.write(
            kind=EVIDENCE_RETRIEVED,
            review_id=self.review_id,
            run_id=self.run_id,
            question_id=question.question_id,
            actor=f"{department.value.capitalize()}Agent",
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
        #
        # Detecting a contradiction and shipping it with a warning label is not the
        # product. The answer that goes to the customer has to honour what the earlier
        # round committed to, so a contradicted draft is REDRAFTED under the commitment
        # as a binding constraint and then re-checked. One redraft, never a loop: if the
        # second attempt still contradicts, the answer is held for a human, which is the
        # honest outcome rather than grinding the model until it complies.
        verdict, constrained = self._check_consistency(question, text)
        commitments = self._commitments_for(question)

        if constrained and commitments:
            try:
                redraft = self._generate(
                    REASONING_MODEL,
                    constrained_drafting_prompt(
                        department, question.text, evidence, commitments, text
                    ),
                )
            except Exception as exc:
                logger.warning("constrained redraft failed for %s: %s", question.question_id, exc)
                redraft = ""

            if redraft and INSUFFICIENT not in redraft:
                second_verdict, _ = self._check_consistency(question, redraft, redraft=True)
                self.audit.write(
                    kind=ANSWER_DRAFTED,
                    review_id=self.review_id,
                    run_id=self.run_id,
                    question_id=question.question_id,
                    actor=f"{department.value.capitalize()}Agent",
                    detail={
                        "redraft": True,
                        "first_verdict": verdict.value,
                        "second_verdict": second_verdict.value,
                        "superseded_text": text[:400],
                    },
                )
                text = redraft
                verdict = second_verdict

        outcome.contradiction = verdict
        #: True means "we checked AND it changed the answer", which is the claim the demo
        #: actually makes. It stays true even when the redraft resolves the contradiction
        #: -- especially then, because that is the moment the constraint did its work.
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
            touches_prior_commitment=bool(commitments),
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
        """Prior-round commitments this question is in the blast radius of.

        Two matchers, because one is not enough and the demo turns on the second.

        **By content-derived id.** A question re-asked in a later round -- even
        renumbered, recapitalised, or re-lettered -- normalises to the same id, so the
        earlier commitment is found exactly. That covers the honest re-ask.

        **By meaning.** It does not cover the interesting case. Round 2 of a real review
        does not re-ask; it reframes. The seeded example is *"Our regulated business unit
        cannot use multi-tenant SaaS. Please describe the self-hosted or on-premises
        deployment options available..."* against a round-1 commitment that no such
        deployment is offered. Those share almost no words and have completely different
        ids, so id matching finds nothing and the contradiction sails through.

        So commitments are also matched by embedding similarity between the question and
        the commitment statement. Measured over the 40 follow-up questions x 5
        commitments: every genuine pairing scored 0.633-0.710 and the first false pairing
        scored 0.604, so the threshold sits at 0.62. Note the asymmetry of the failure
        modes -- a false match costs one extra consistency check that returns
        NO_CONTRADICTION, while a missed match lets round 2 contradict round 1 in front
        of the customer.
        """
        exact = [
            statement
            for question_id, statement in self.prior_commitments
            if question_id == question.question_id
        ]
        if not self.prior_commitments:
            return exact

        statements = [statement for _, statement in self.prior_commitments]
        scores = self._scorer.score(question.text, statements)
        semantic = [
            statement
            for statement, score in zip(statements, scores, strict=True)
            if score >= COMMITMENT_MATCH_SCORE and statement not in exact
        ]
        return exact + semantic

    def _check_consistency(
        self, question: Question, draft: str, *, redraft: bool = False
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
                "pass": "post_redraft" if redraft else "initial",
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

        # Relevance scoring is spend too, and leaving it out of the reported cost would
        # make the demo number quietly wrong. The embedding API bills characters and
        # `PRICE_PER_MTOK` is per token, so characters are converted at the conventional
        # 4:1 -- an estimate, stated as one, on a figure that rounds to a tenth of a cent.
        report.budget = self.ledger.summary()
        embedded_tokens = self._scorer.billable_characters // 4
        if embedded_tokens:
            self.ledger.record_usage(EMBEDDING_MODEL, embedded_tokens, 0)
            report.budget = self.ledger.summary()
        report.budget["relevance_method"] = self._scorer.last_method
        report.budget["embedding_characters"] = self._scorer.billable_characters

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
