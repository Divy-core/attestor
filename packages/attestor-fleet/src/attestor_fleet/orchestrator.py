"""The Orchestrator: judgement, and nothing the pipeline already does.

`pipeline.py` is a workflow because the sequence is known in advance (`ADR-0002`). What
is *not* known in advance is judgement, and that is what lives here:

* **which pipeline** suits a submitted artifact — a 312-question first round, or a
  40-question follow-up that has to be checked against what we committed to in July;
* **when to retry** a question that failed, and when retrying is just spending the
  budget twice on the same permanent failure;
* **when to escalate** beyond the per-answer rules `policy.requires_human` already
  applied — a run can look acceptable answer-by-answer and still be wrong in aggregate;
* **when to stop.**

Three properties this module is built around:

**Every judgement costs a turn, and turns are capped.** `BudgetLedger.record_turn()`
raises past `MAX_TURNS`, so a confused orchestrator cannot loop. That is the only
realistic way an agent burns a credit balance.

**Every judgement fails closed.** If the model is unavailable, blocked by the floor
setting, or returns something unparseable, a deterministic fallback takes the cautious
branch and the decision records `decided_by="fallback:<why>"`. A judgement layer whose
failure mode is "carry on as if it succeeded" is decoration.

**It never re-derives what the pipeline measured.** It reads `RunReport` and decides;
it does not re-triage, re-retrieve, or re-score. Duplicating the pipeline's work here
would double the cost and give two answers to every question.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from attestor_core.domain import AnswerStatus, ContradictionVerdict, Question
from attestor_fleet.callbacks.audit import (
    PLAN_SELECTED,
    RETRY_DECIDED,
    RUN_COMPLETED,
    AuditSink,
    NullAuditSink,
)
from attestor_fleet.pipeline import QuestionOutcome, ReviewPipeline, RunReport
from attestor_fleet.prompts.orchestration import finalise_prompt, plan_prompt, retry_prompt
from attestor_platform.config import REASONING_MODEL, genai_client

logger = logging.getLogger(__name__)

#: The two pipelines the orchestrator may select between.
FULL_REVIEW = "full_review"
FOLLOW_UP_ROUND = "follow_up_round"
_PIPELINES = frozenset({FULL_REVIEW, FOLLOW_UP_ROUND})

#: Hard ceiling on retry waves regardless of what the model asks for. The model proposes;
#: this disposes. A judgement layer that can grant itself unlimited retries is not capped.
MAX_RETRY_WAVES = 2
#: Most questions re-attempted in one wave. A larger transient outage is a run-level
#: problem for a human, not something to grind through one question at a time.
MAX_RETRIES_PER_WAVE = 25

#: A run this unevidenced is a corpus problem, not an answering problem, and a human
#: should see it before the customer does.
_SYSTEMIC_FLAG_RATE = 0.60

_LINE = re.compile(r"^\s*([A-Z_]+)\s*\|\s*(.+?)\s*$", re.MULTILINE)
_RETRY_LINE = re.compile(r"^\s*(\d+)\s*\|\s*(RETRY|GIVE_UP)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class ArtifactBrief:
    """What the orchestrator knows about a submitted artifact before any work starts.

    Deliberately small. The plan decision is made from the artifact's shape and the
    review's history, not from its contents — reading 312 question cells to decide which
    pipeline to run would cost more than running the pipeline.
    """

    filename: str
    question_count: int
    prior_round_count: int = 0
    prior_commitment_count: int = 0


@dataclass(frozen=True)
class RunPlan:
    """The orchestrator's answer to "how should this artifact be processed?"."""

    pipeline: str
    check_consistency: bool
    retry_waves: int
    reason: str
    #: "model" or "fallback:<why>". Recorded so a run can be read back and the judgement
    #: attributed, rather than looking model-made when it was not.
    decided_by: str


@dataclass(frozen=True)
class FinalDecision:
    """The orchestrator's answer to "can this run be handed back?"."""

    release: bool
    widen: str
    reason: str
    decided_by: str
    #: Questions escalated by the orchestrator on top of the per-answer rules.
    widened_question_ids: tuple[str, ...] = ()


@dataclass
class OrchestrationResult:
    """One orchestrated review, start to finish."""

    plan: RunPlan
    report: RunReport
    decision: FinalDecision
    retried_question_ids: list[str] = field(default_factory=list)
    recovered_question_ids: list[str] = field(default_factory=list)
    turns: int = 0


def _parse_fields(raw: str) -> dict[str, str]:
    """Parse the `KEY|value` reply shape. Unknown keys are ignored, not guessed at."""
    return {key.upper(): value.strip() for key, value in _LINE.findall(raw)}


class Orchestrator:
    """Root judgement over one review run.

    Composition is explicit: the caller supplies the pipeline, the audit sink, and
    therefore the budget ledger the pipeline already owns. The orchestrator shares that
    ledger rather than keeping its own, so orchestration turns and drafting tokens are
    counted against one budget — two ledgers would mean neither ceiling was real.
    """

    def __init__(
        self,
        pipeline: ReviewPipeline,
        *,
        audit: AuditSink | None = None,
        client: Any | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.audit: AuditSink = audit if audit is not None else NullAuditSink()
        self._client = client if client is not None else genai_client()
        self.turns = 0

    # -- the model plumbing, with the failure branch made explicit ---------------------

    def _judge(self, prompt: str) -> tuple[str, str]:
        """Make one judgement call. Returns `(text, decided_by)`.

        `decided_by` is `"model"` on success and `"fallback:<why>"` otherwise, so the
        caller can record which decisions were actually judged and which were defaulted.
        Every call through here counts a turn, and `record_turn` raises past the ceiling.
        """
        self.pipeline.ledger.record_turn()
        self.turns += 1
        try:
            response = self._client.models.generate_content(model=REASONING_MODEL, contents=prompt)
        except Exception as exc:
            logger.warning("orchestrator judgement call failed: %s", exc)
            return "", "fallback:model_error"

        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            # The floor setting intercepting our own prompt is a legitimate outcome and
            # must be visible as itself, not as an empty parse.
            logger.warning(
                "orchestrator judgement BLOCKED by Model Armor floor: %s",
                getattr(feedback, "block_reason_message", ""),
            )
            return "", "fallback:armor_blocked"

        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self.pipeline.ledger.record_usage(
                REASONING_MODEL,
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        text = (response.text or "").strip()
        if not text:
            return "", "fallback:empty_reply"
        return text, "model"

    # -- decision 1: which pipeline ----------------------------------------------------

    def plan(self, brief: ArtifactBrief) -> RunPlan:
        """Decide how this artifact should be processed."""
        raw, decided_by = self._judge(
            plan_prompt(
                brief.filename,
                brief.question_count,
                brief.prior_round_count,
                brief.prior_commitment_count,
            )
        )
        fields = _parse_fields(raw) if raw else {}
        pipeline = fields.get("PIPELINE", "").lower()

        if pipeline not in _PIPELINES:
            plan = self._fallback_plan(
                brief, decided_by if decided_by != "model" else "fallback:unparsed_plan"
            )
        else:
            consistency = fields.get("CONSISTENCY", "yes").lower() != "no"
            # A follow-up without the consistency check is the one combination that
            # cannot be allowed: it is the round where contradicting July actually
            # happens. Overridden rather than trusted.
            if brief.prior_commitment_count > 0:
                consistency = True
            waves = _clamp_waves(fields.get("RETRY_WAVES", "1"))
            plan = RunPlan(
                pipeline=pipeline,
                check_consistency=consistency,
                retry_waves=waves,
                reason=fields.get("REASON", "").strip() or "(no reason given)",
                decided_by=decided_by,
            )

        self.audit.write(
            kind=PLAN_SELECTED,
            review_id=self.pipeline.review_id,
            run_id=self.pipeline.run_id,
            actor="Orchestrator",
            detail={
                "artifact": brief.filename,
                "questions": brief.question_count,
                "pipeline": plan.pipeline,
                "check_consistency": plan.check_consistency,
                "retry_waves": plan.retry_waves,
                "reason": plan.reason,
                "decided_by": plan.decided_by,
            },
        )
        return plan

    @staticmethod
    def _fallback_plan(brief: ArtifactBrief, why: str) -> RunPlan:
        """The cautious plan, taken when the judgement call could not be used.

        Cautious means: if there is anything on file to contradict, treat this as a
        follow-up and check consistency. Guessing "first round" when commitments exist
        is the expensive mistake.
        """
        follow_up = brief.prior_commitment_count > 0 or brief.prior_round_count > 0
        return RunPlan(
            pipeline=FOLLOW_UP_ROUND if follow_up else FULL_REVIEW,
            check_consistency=follow_up,
            retry_waves=1,
            reason="orchestrator judgement unavailable; defaulted on prior-round history",
            decided_by=why,
        )

    # -- decision 2: what to retry -----------------------------------------------------

    def _retry_candidates(self, report: RunReport) -> list[QuestionOutcome]:
        """Outcomes that failed with an error, which is the only retryable shape.

        A guardrail block, a policy denial, and an honest `FLAGGED_NO_EVIDENCE` are all
        *answers*. Only a question whose drafting raised is a candidate, and even then
        the model still decides whether the error was transient.
        """
        return [
            outcome
            for outcome in report.outcomes
            if outcome.error and not outcome.blocked and not outcome.denied
        ][:MAX_RETRIES_PER_WAVE]

    def decide_retries(self, candidates: Sequence[QuestionOutcome]) -> list[QuestionOutcome]:
        """Judge which failures are transient enough to re-attempt."""
        if not candidates:
            return []

        raw, decided_by = self._judge(
            retry_prompt(
                [
                    (index, outcome.question.text, outcome.error or "")
                    for index, outcome in enumerate(candidates)
                ]
            )
        )
        if decided_by != "model":
            # Fails closed: no retries. An un-retried question is already flagged for a
            # human, which is safe; retrying blind is how a transient blip becomes a
            # loop.
            self.audit.write(
                kind=RETRY_DECIDED,
                review_id=self.pipeline.review_id,
                run_id=self.pipeline.run_id,
                actor="Orchestrator",
                detail={"candidates": len(candidates), "retrying": 0, "decided_by": decided_by},
            )
            return []

        chosen: list[QuestionOutcome] = []
        for index_text, verdict in _RETRY_LINE.findall(raw):
            index = int(index_text)
            if verdict.upper() == "RETRY" and 0 <= index < len(candidates):
                chosen.append(candidates[index])

        self.audit.write(
            kind=RETRY_DECIDED,
            review_id=self.pipeline.review_id,
            run_id=self.pipeline.run_id,
            actor="Orchestrator",
            detail={
                "candidates": len(candidates),
                "retrying": len(chosen),
                "question_ids": [o.question.question_id for o in chosen],
                "decided_by": decided_by,
            },
        )
        return chosen

    # -- decision 3: release or hold ---------------------------------------------------

    def finalise(self, report: RunReport) -> FinalDecision:
        """Decide whether the run can be released, and whether to widen escalation."""
        counters = _run_shape(report)
        notes = _run_notes(report)
        raw, decided_by = self._judge(finalise_prompt(counters, notes))
        fields = _parse_fields(raw) if raw else {}

        decision = fields.get("DECISION", "").lower()
        widen = fields.get("WIDEN", "").lower()
        if decision not in {"release", "escalate_review"} or widen not in {
            "none",
            "commitments",
            "all_flagged",
        }:
            # Fails closed on the safe side: hold the run, and widen to commitments if a
            # contradiction was actually detected.
            contradicted = counters["contradictions"] > 0
            decision = "escalate_review" if contradicted or counters["armor_blocked"] else "release"
            widen = "commitments" if contradicted else "none"
            if decided_by == "model":
                decided_by = "fallback:unparsed_finalise"

        widened = _widen(report, widen)
        for outcome in widened:
            outcome.needs_human = True

        final = FinalDecision(
            release=decision == "release",
            widen=widen,
            reason=fields.get("REASON", "").strip() or "orchestrator judgement unavailable",
            decided_by=decided_by,
            widened_question_ids=tuple(o.question.question_id for o in widened),
        )

        self.audit.write(
            kind=RUN_COMPLETED,
            review_id=self.pipeline.review_id,
            run_id=self.pipeline.run_id,
            actor="Orchestrator",
            detail={
                "release": final.release,
                "widen": final.widen,
                "widened": len(final.widened_question_ids),
                "reason": final.reason,
                "decided_by": final.decided_by,
                "turns": self.turns,
                **counters,
            },
        )
        return final

    # -- the whole thing ----------------------------------------------------------------

    def run(self, questions: list[Question], brief: ArtifactBrief) -> OrchestrationResult:
        """Plan, execute, retry what is worth retrying, then decide whether to release."""
        plan = self.plan(brief)
        if not plan.check_consistency:
            # The plan is allowed to switch the check off only when nothing is on file to
            # contradict; `plan()` has already forced it on when commitments exist.
            self.pipeline.prior_commitments = []

        report = self.pipeline.run(questions)

        retried: list[str] = []
        recovered: list[str] = []
        for _ in range(min(plan.retry_waves, MAX_RETRY_WAVES)):
            candidates = self._retry_candidates(report)
            if not candidates:
                break
            chosen = self.decide_retries(candidates)
            if not chosen:
                break
            for outcome in chosen:
                retried.append(outcome.question.question_id)
                fresh = self.pipeline.draft(outcome.question)
                _replace(report, fresh)
                if fresh.error is None:
                    recovered.append(fresh.question.question_id)

        decision = self.finalise(report)
        return OrchestrationResult(
            plan=plan,
            report=report,
            decision=decision,
            retried_question_ids=retried,
            recovered_question_ids=recovered,
            turns=self.turns,
        )


# ---------------------------------------------------------------------------------------
# Pure helpers — deliberately free functions so they are testable without a client
# ---------------------------------------------------------------------------------------


def _clamp_waves(raw: str) -> int:
    try:
        return max(0, min(MAX_RETRY_WAVES, int(raw.strip())))
    except ValueError:
        return 1


def _run_shape(report: RunReport) -> dict[str, int]:
    """The counters the finalise judgement is made from. Measured, never estimated."""
    total = len(report.outcomes)
    return {
        "questions": total,
        "answered": len(report.answered),
        "with_citation": len(report.cited),
        "flagged_no_evidence": len(report.flagged_no_evidence),
        "armor_blocked": len(report.blocked),
        "needs_human": len(report.needs_human),
        "contradictions": sum(
            1
            for outcome in report.outcomes
            if outcome.contradiction is ContradictionVerdict.CONTRADICTION
        ),
        "errors": sum(1 for outcome in report.outcomes if outcome.error),
    }


def _run_notes(report: RunReport) -> list[str]:
    """Short observations about the run's shape, for the finalise prompt."""
    notes: list[str] = []
    total = len(report.outcomes) or 1
    if len(report.flagged_no_evidence) / total >= _SYSTEMIC_FLAG_RATE:
        notes.append("most answers could not be evidenced from the corpus")
    if report.blocked:
        notes.append("a guardrail blocked at least one question")
    if any(o.contradiction is ContradictionVerdict.CONTRADICTION for o in report.outcomes):
        notes.append("an answer contradicts a commitment made in an earlier round")
    if any(o.denied for o in report.outcomes):
        notes.append("a cross-department access attempt was denied")
    return notes


def _widen(report: RunReport, widen: str) -> list[QuestionOutcome]:
    """Which outcomes the orchestrator escalates on top of the per-answer rules."""
    if widen == "commitments":
        return [
            outcome
            for outcome in report.outcomes
            if not outcome.needs_human
            and outcome.contradiction is not ContradictionVerdict.NO_CONTRADICTION
        ]
    if widen == "all_flagged":
        return [
            outcome
            for outcome in report.outcomes
            if not outcome.needs_human
            and outcome.answer is not None
            and outcome.answer.status is AnswerStatus.FLAGGED_NO_EVIDENCE
        ]
    return []


def _replace(report: RunReport, fresh: QuestionOutcome) -> None:
    """Swap a re-attempted outcome in place, preserving order."""
    for index, outcome in enumerate(report.outcomes):
        if outcome.question.question_id == fresh.question.question_id:
            report.outcomes[index] = fresh
            return


# ---------------------------------------------------------------------------------------
# The ADK surface
# ---------------------------------------------------------------------------------------

#: The root agent's instruction is assembled from the same three prompt statics the
#: `Orchestrator` uses, so the policy text is single-sourced. Two copies of "when to
#: escalate" that drift apart is precisely the failure this avoids.
ROOT_AGENT_INSTRUCTION = "\n\n".join(
    (
        "You are the Orchestrator of the Attestor vendor security review fleet.",
        "You make judgement calls and call tools to act on them. You never answer a "
        "questionnaire question yourself, never retrieve evidence yourself, and never "
        "re-do work a tool has already done — the specialist agents and the review "
        "pipeline own that, and duplicating it costs the run twice.",
        (
            "Deciding which pipeline to run:\n"
            "- full_review for a first-round questionnaire.\n"
            "- follow_up_round when the review already has answered rounds on file; "
            "every answer is then additionally checked against prior commitments.\n"
            "- Prior commitments on file are the deciding signal, not the question count."
        ),
        "Deciding what to retry: re-attempt only failures that are transient (timeout, "
        "429, 503, connection reset). A permissions error, an invalid request, a policy "
        "denial or a guardrail block will recur, so give up on it — the question is "
        "already held for a human, which is safe.",
        "Deciding whether to release: the per-answer escalation rules have already run. "
        "Escalate the whole review only for a systemic pattern — a repeated guardrail "
        "hit, a contradiction against a prior commitment, or a large share of answers "
        "that could not be evidenced. Escalating everything is not caution; it hands the "
        "work back to the human this system exists to spare.",
        "Call finalise_run exactly once, last.",
    )
)


class RunSession:
    """Binds one pipeline to the tools the root agent calls.

    The tools execute; the agent judges. That split is the whole point: an LLM tool that
    itself calls an LLM to decide would mean two judgements per decision, and neither
    would be the one recorded.
    """

    def __init__(self, pipeline: ReviewPipeline, questions: list[Question]) -> None:
        self.pipeline = pipeline
        self.questions = questions
        self.report: RunReport | None = None
        self.decision: FinalDecision | None = None

    def execute_pipeline(self, pipeline: str, check_consistency: bool) -> dict[str, int]:
        """Run the review pipeline over the submitted questions.

        Args:
            pipeline: `full_review` or `follow_up_round`.
            check_consistency: Whether answers are checked against prior commitments.

        Returns:
            The measured shape of the run: counts of answered, cited, flagged, blocked.
        """
        if pipeline not in _PIPELINES:
            raise ValueError(f"unknown pipeline {pipeline!r}")
        if not check_consistency:
            self.pipeline.prior_commitments = []
        self.report = self.pipeline.run(self.questions)
        return _run_shape(self.report)

    def retry_questions(self, question_ids: list[str]) -> dict[str, int]:
        """Re-attempt specific questions that failed transiently.

        Args:
            question_ids: Content-derived ids of the questions to re-draft.

        Returns:
            How many were re-attempted and how many came back without an error.
        """
        if self.report is None:
            raise ValueError("execute_pipeline must run before retry_questions")
        wanted = set(question_ids[:MAX_RETRIES_PER_WAVE])
        recovered = 0
        attempted = 0
        for outcome in list(self.report.outcomes):
            if outcome.question.question_id not in wanted:
                continue
            attempted += 1
            fresh = self.pipeline.draft(outcome.question)
            _replace(self.report, fresh)
            if fresh.error is None:
                recovered += 1
        return {"attempted": attempted, "recovered": recovered}

    def finalise_run(self, release: bool, widen: str, reason: str) -> dict[str, object]:
        """Close the run, optionally widening escalation beyond the per-answer rules.

        Args:
            release: True to hand the run back, False to hold the whole review.
            widen: `none`, `commitments`, or `all_flagged`.
            reason: One short sentence recorded in the audit trail.

        Returns:
            What was decided and how many answers the widening escalated.
        """
        if self.report is None:
            raise ValueError("execute_pipeline must run before finalise_run")
        widened = _widen(self.report, widen)
        for outcome in widened:
            outcome.needs_human = True
        self.decision = FinalDecision(
            release=release,
            widen=widen,
            reason=reason,
            decided_by="root_agent",
            widened_question_ids=tuple(o.question.question_id for o in widened),
        )
        return {"release": release, "widen": widen, "widened": len(widened)}


def build_root_agent(session: RunSession) -> Any:
    """Build the root `LlmAgent` bound to one run session.

    This is the deployable shape: Phase 5 hands this agent to Agent Runtime, where the
    turn cap below is what keeps a confused model from looping on someone else's money.
    Phase 3 drives reviews through `Orchestrator`, which makes the same three judgements
    against the same prompt text without needing an ADK session — the local runner, the
    tests, and the harnesses all use that path.
    """
    from google.adk.agents import LlmAgent

    from attestor_platform.config import gemini_model

    ledger = session.pipeline.ledger

    def _count_turn(callback_context: Any, llm_request: Any) -> None:
        """Turn cap, enforced where the model is actually called.

        `record_turn` raises `BudgetExceeded` past the ceiling. Raising rather than
        returning a polite refusal is deliberate: a ceiling the agent can talk its way
        past is not a ceiling.
        """
        del callback_context, llm_request
        ledger.record_turn()

    return LlmAgent(
        name="orchestrator",
        model=gemini_model(REASONING_MODEL),
        description=(
            "Root judgement for a vendor security review: which pipeline to run, what to "
            "retry, when to escalate to a human, and when to stop."
        ),
        instruction=ROOT_AGENT_INSTRUCTION,
        tools=[session.execute_pipeline, session.retry_questions, session.finalise_run],
        before_model_callback=_count_turn,
    )
