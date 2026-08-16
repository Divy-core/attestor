"""The Orchestrator's three judgements, including every failure branch.

These run with a fake model client. The point is not to test that Gemini can follow an
instruction -- it is to prove that a malformed, empty, blocked, or exploding judgement
call still produces a safe decision, and that the turn ceiling actually stops a loop.
"""

from __future__ import annotations

from typing import Any

import pytest

from attestor_core.domain import (
    Answer,
    AnswerStatus,
    Citation,
    Confidence,
    ContradictionVerdict,
    Department,
    Question,
)
from attestor_core.errors import BudgetExceeded
from attestor_fleet.callbacks.audit import NullAuditSink
from attestor_fleet.callbacks.budget import BudgetLedger
from attestor_fleet.orchestrator import (
    FOLLOW_UP_ROUND,
    FULL_REVIEW,
    MAX_RETRY_WAVES,
    ArtifactBrief,
    Orchestrator,
    RunSession,
    _run_shape,
    _widen,
)
from attestor_fleet.pipeline import QuestionOutcome, ReviewPipeline, RunReport

REVIEW_ID = "rev-test"
RUN_ID = "run-test"


# ---------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------


class _Usage:
    prompt_token_count = 100
    candidates_token_count = 20


class _Feedback:
    def __init__(self, reason: str) -> None:
        self.block_reason = reason
        self.block_reason_message = "Blocked by Model Armor Floor Setting"


class _Response:
    def __init__(self, text: str, *, blocked: bool = False) -> None:
        self.text = text
        self.usage_metadata = _Usage()
        self.prompt_feedback = _Feedback("MODEL_ARMOR") if blocked else None


class _Models:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    def generate_content(self, *, model: str, contents: str) -> _Response:
        del model
        self.prompts.append(contents)
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, _Response):
            return reply
        return _Response(str(reply))


class _Client:
    def __init__(self, *replies: Any) -> None:
        self.models = _Models(list(replies))


def _question(text: str, department: Department = Department.SECURITY) -> Question:
    return Question.from_text(text, department=department)


def _answer(question: Question, status: AnswerStatus = AnswerStatus.FLAGGED_NO_EVIDENCE) -> Answer:
    """A minimal answer. Citations are mandatory unless the status permits their absence."""
    cited = status not in {AnswerStatus.FLAGGED_NO_EVIDENCE, AnswerStatus.QUARANTINED}
    return Answer(
        question_id=question.question_id,
        round_id=RUN_ID,
        text="AES-256-GCM." if cited else "No supporting evidence was found in the corpus.",
        citations=(
            [
                Citation(
                    document_uri="gs://corpus/security/encryption-standard.txt",
                    document_title="encryption-standard",
                    snippet="All customer data at rest is encrypted using AES-256-GCM.",
                    retrieval_score=0.9,
                )
            ]
            if cited
            else []
        ),
        confidence=Confidence.HIGH if cited else Confidence.LOW,
        status=status,
        authored_by="SecurityAgent" if cited else "EvidenceAgent",
    )


def _pipeline(monkeypatch: pytest.MonkeyPatch) -> ReviewPipeline:
    """A pipeline whose model client is never constructed -- no cloud in a unit test."""
    monkeypatch.setattr(
        "attestor_fleet.pipeline.genai_client", lambda *a, **k: _Client(), raising=True
    )
    return ReviewPipeline(
        review_id=REVIEW_ID, run_id=RUN_ID, ledger=BudgetLedger(review_id=REVIEW_ID)
    )


def _report(outcomes: list[QuestionOutcome]) -> RunReport:
    return RunReport(review_id=REVIEW_ID, run_id=RUN_ID, outcomes=outcomes)


# ---------------------------------------------------------------------------------------
# Decision 1: the plan
# ---------------------------------------------------------------------------------------


class TestPlan:
    def test_parses_a_well_formed_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _Client(
            "PIPELINE|full_review\nCONSISTENCY|no\nRETRY_WAVES|2\nREASON|first round, no history"
        )
        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=client)

        plan = orch.plan(ArtifactBrief(filename="r1.xlsx", question_count=312))

        assert plan.pipeline == FULL_REVIEW
        assert plan.check_consistency is False
        assert plan.retry_waves == 2
        assert plan.decided_by == "model"

    def test_consistency_is_forced_on_when_commitments_exist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one combination that cannot be permitted, whatever the model says."""
        client = _Client("PIPELINE|full_review\nCONSISTENCY|no\nRETRY_WAVES|1\nREASON|whatever")
        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=client)

        plan = orch.plan(
            ArtifactBrief(
                filename="r2.xlsx",
                question_count=40,
                prior_round_count=1,
                prior_commitment_count=3,
            )
        )

        assert plan.check_consistency is True

    @pytest.mark.parametrize(
        "reply",
        [
            "",
            "sure! I think we should do a full review :)",
            "PIPELINE|whatever\nCONSISTENCY|yes\nRETRY_WAVES|1\nREASON|x",
            _Response("", blocked=True),
            RuntimeError("503 Service Unavailable"),
        ],
    )
    def test_unusable_judgement_falls_back_cautiously(
        self, monkeypatch: pytest.MonkeyPatch, reply: Any
    ) -> None:
        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=_Client(reply))

        plan = orch.plan(
            ArtifactBrief(filename="r2.xlsx", question_count=40, prior_commitment_count=2)
        )

        assert plan.pipeline == FOLLOW_UP_ROUND
        assert plan.check_consistency is True
        assert plan.decided_by.startswith("fallback:")

    def test_retry_waves_are_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _Client("PIPELINE|full_review\nCONSISTENCY|yes\nRETRY_WAVES|99\nREASON|x")
        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=client)

        assert orch.plan(ArtifactBrief("r.xlsx", 10)).retry_waves == MAX_RETRY_WAVES

    def test_plan_is_audited_with_its_provenance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        audit = NullAuditSink()
        orch = Orchestrator(_pipeline(monkeypatch), audit=audit, client=_Client(""))

        orch.plan(ArtifactBrief("r.xlsx", 10))

        event = next(e for e in audit.events if e["kind"] == "plan_selected")
        assert event["detail"]["decided_by"] == "fallback:empty_reply"


# ---------------------------------------------------------------------------------------
# Decision 2: retries
# ---------------------------------------------------------------------------------------


class TestRetries:
    def test_only_errored_outcomes_are_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A guardrail block and an honest refusal are answers, not failures."""
        errored = QuestionOutcome(question=_question("Timed out question"), error="504 deadline")
        blocked = QuestionOutcome(question=_question("Injected question"), blocked=True, error="x")
        denied = QuestionOutcome(question=_question("Legal question"), denied=True, error="x")
        flagged = QuestionOutcome(question=_question("Unevidenced question"))
        flagged.answer = _answer(flagged.question)

        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=_Client())
        candidates = orch._retry_candidates(_report([errored, blocked, denied, flagged]))

        assert [c.question.text for c in candidates] == ["Timed out question"]

    def test_model_chooses_which_failures_to_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        first = QuestionOutcome(question=_question("A"), error="504 deadline exceeded")
        second = QuestionOutcome(question=_question("B"), error="403 permission denied")
        orch = Orchestrator(
            _pipeline(monkeypatch),
            audit=NullAuditSink(),
            client=_Client("0|RETRY\n1|GIVE_UP"),
        )

        chosen = orch.decide_retries([first, second])

        assert [c.question.text for c in chosen] == ["A"]

    def test_unusable_judgement_retries_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        audit = NullAuditSink()
        outcome = QuestionOutcome(question=_question("A"), error="504")
        orch = Orchestrator(_pipeline(monkeypatch), audit=audit, client=_Client(""))

        assert orch.decide_retries([outcome]) == []
        event = next(e for e in audit.events if e["kind"] == "retry_decided")
        assert event["detail"]["retrying"] == 0

    def test_no_candidates_costs_no_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        orch = Orchestrator(_pipeline(monkeypatch), audit=NullAuditSink(), client=_Client())

        assert orch.decide_retries([]) == []
        assert orch.turns == 0


# ---------------------------------------------------------------------------------------
# Decision 3: release or hold
# ---------------------------------------------------------------------------------------


class TestFinalise:
    def test_release_leaves_per_answer_decisions_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcome = QuestionOutcome(question=_question("A"))
        outcome.answer = _answer(outcome.question, AnswerStatus.DRAFTED)
        orch = Orchestrator(
            _pipeline(monkeypatch),
            audit=NullAuditSink(),
            client=_Client("DECISION|release\nWIDEN|none\nREASON|ordinary run"),
        )
        orch.pipeline.run = lambda questions: _report([outcome])  # type: ignore[method-assign]

        decision = orch.finalise(_report([outcome]))

        assert decision.release is True
        assert decision.widened_question_ids == ()
        assert outcome.needs_human is False

    def test_widening_to_commitments_escalates_contradictions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        contradicting = QuestionOutcome(
            question=_question("Do you offer on-premises deployment?"),
            contradiction=ContradictionVerdict.CONTRADICTION,
        )
        clean = QuestionOutcome(question=_question("Do you encrypt at rest?"))
        orch = Orchestrator(
            _pipeline(monkeypatch),
            audit=NullAuditSink(),
            client=_Client("DECISION|escalate_review\nWIDEN|commitments\nREASON|contradiction"),
        )

        decision = orch.finalise(_report([contradicting, clean]))

        assert decision.release is False
        assert decision.widened_question_ids == (contradicting.question.question_id,)
        assert contradicting.needs_human is True
        assert clean.needs_human is False

    def test_unparsed_finalise_holds_when_a_contradiction_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed: the model said nothing usable and a contradiction was found."""
        contradicting = QuestionOutcome(
            question=_question("On-premises?"), contradiction=ContradictionVerdict.CONTRADICTION
        )
        orch = Orchestrator(
            _pipeline(monkeypatch), audit=NullAuditSink(), client=_Client("nonsense")
        )

        decision = orch.finalise(_report([contradicting]))

        assert decision.release is False
        assert decision.widen == "commitments"
        assert decision.decided_by == "fallback:unparsed_finalise"
        assert contradicting.needs_human is True

    def test_unparsed_finalise_releases_an_ordinary_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed is not fails paranoid: nothing wrong means nothing to hold."""
        outcome = QuestionOutcome(question=_question("A"))
        orch = Orchestrator(
            _pipeline(monkeypatch), audit=NullAuditSink(), client=_Client("nonsense")
        )

        assert orch.finalise(_report([outcome])).release is True


# ---------------------------------------------------------------------------------------
# The turn ceiling
# ---------------------------------------------------------------------------------------


class TestTurnCeiling:
    def test_judgement_calls_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _pipeline(monkeypatch)
        pipeline.ledger.max_turns = 2
        orch = Orchestrator(
            pipeline, audit=NullAuditSink(), client=_Client("", "", "", "", "", "", "")
        )

        orch.plan(ArtifactBrief("r.xlsx", 10))
        orch.plan(ArtifactBrief("r.xlsx", 10))
        with pytest.raises(BudgetExceeded):
            orch.plan(ArtifactBrief("r.xlsx", 10))


# ---------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------


class TestHelpers:
    def test_run_shape_counts_what_the_judgement_reads(self) -> None:
        cited = QuestionOutcome(question=_question("A"))
        cited.answer = _answer(cited.question, AnswerStatus.DRAFTED)
        flagged = QuestionOutcome(question=_question("B"))
        flagged.answer = _answer(flagged.question)
        blocked = QuestionOutcome(question=_question("C"), blocked=True)

        shape = _run_shape(_report([cited, flagged, blocked]))

        assert shape["questions"] == 3
        assert shape["answered"] == 2
        assert shape["flagged_no_evidence"] == 1
        assert shape["armor_blocked"] == 1

    def test_widen_all_flagged_skips_already_escalated(self) -> None:
        already = QuestionOutcome(question=_question("A"), needs_human=True)
        already.answer = _answer(already.question)
        pending = QuestionOutcome(question=_question("B"))
        pending.answer = _answer(pending.question)

        widened = _widen(_report([already, pending]), "all_flagged")

        assert [w.question.text for w in widened] == ["B"]


# ---------------------------------------------------------------------------------------
# The ADK surface
# ---------------------------------------------------------------------------------------


class TestRootAgent:
    def test_tools_are_bound_and_the_turn_cap_is_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from attestor_fleet.orchestrator import build_root_agent

        pipeline = _pipeline(monkeypatch)
        pipeline.ledger.max_turns = 1
        session = RunSession(pipeline, [_question("Do you encrypt customer data at rest?")])

        agent = build_root_agent(session)

        assert agent.name == "orchestrator"
        assert {t.__name__ for t in agent.tools} == {
            "execute_pipeline",
            "retry_questions",
            "finalise_run",
        }
        # The cap is enforced where the model is called, not merely configured.
        agent.before_model_callback(None, None)
        with pytest.raises(BudgetExceeded):
            agent.before_model_callback(None, None)

    def test_finalising_before_executing_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = RunSession(_pipeline(monkeypatch), [_question("A")])

        with pytest.raises(ValueError, match="execute_pipeline must run"):
            session.finalise_run(release=True, widen="none", reason="x")

    def test_unknown_pipeline_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = RunSession(_pipeline(monkeypatch), [_question("A")])

        with pytest.raises(ValueError, match="unknown pipeline"):
            session.execute_pipeline("do_whatever", check_consistency=True)
