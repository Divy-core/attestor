"""Auto-send releases a round without a person, and says so.

The switch was asked for and built. What these tests protect is the one property that keeps
it from being a lie: **the audit trail must never carry a person's name against a decision
they did not make.**

An auto-approved answer is approved by `auto-send`. The name of whoever enabled the switch
is in the detail, under `enabled_by`, where it is true -- they authorised the automation,
they did not read the answer. So "who approved Q47" answers "nobody; auto-send was on, and
<name> turned it on", which is a useful and correct answer, where a person's name would be
a plausible-looking wrong one.

The other property is that nothing changes when the switch is off, because a durable pause
that a new feature quietly weakened would be the worst possible regression in this system.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from attestor_core.domain import Answer, AnswerStatus, Citation, Confidence, Review, Round
from attestor_core.domain.enums import Framework, Residency, ReviewState
from attestor_core.protocol import WorkEnvelope, WorkKind
from dispatcher.handlers import HandlerRegistry

REVIEW_ID = "rev-auto"
ROUND_ID = "rev-auto-r1"


def _answer(index: int, status: AnswerStatus) -> Answer:
    return Answer(
        question_id=f"{index:016x}",
        round_id=ROUND_ID,
        text=f"Answer {index}.",
        citations=[
            Citation(
                document_uri="gs://corpus/policy.md",
                document_title="Policy",
                snippet="A control exists.",
                retrieval_score=0.6,
            )
        ],
        confidence=Confidence.LOW,
        status=status,
        authored_by="SecurityAgent",
    )


class _Reviews:
    def __init__(self, review: Review) -> None:
        self.review = review

    def get(self, review_id: str) -> Review | None:
        return self.review if review_id == REVIEW_ID else None

    def put(self, review: Review) -> None:
        self.review = review


class _Rounds:
    def get(self, round_id: str) -> Round | None:
        if round_id != ROUND_ID:
            return None
        return Round(round_id=ROUND_ID, review_id=REVIEW_ID, ordinal=1, state=ReviewState.DRAFTING)


class _Answers:
    def __init__(self, answers: list[Answer]) -> None:
        self.store = {a.question_id: a for a in answers}

    def for_round(self, round_id: str) -> list[Answer]:
        return list(self.store.values())

    def put(self, answer: Answer) -> None:
        self.store[answer.question_id] = answer


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_safe(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _Publisher:
    def __init__(self) -> None:
        self.published: list[WorkEnvelope] = []

    def publish(self, envelope: WorkEnvelope) -> None:
        self.published.append(envelope)


class _Fleet:
    """Applies a decision the way the real one does: the status moves, nothing else."""

    def __init__(self, answers: _Answers) -> None:
        self.answers = answers
        self.decisions: list[tuple[str, str]] = []

    def apply_decision(
        self,
        round_id: str,
        question_id: str,
        *,
        approved: bool,
        resolved_by: str,
        edited_text: str | None,
    ) -> bool:
        self.decisions.append((question_id, resolved_by))
        held = self.answers.store[question_id]
        self.answers.put(held.model_copy(update={"status": AnswerStatus.APPROVED}))
        return True


def _registry(*, auto_send: bool, held: int) -> tuple[HandlerRegistry, _Audit, _Fleet]:
    review = Review(
        review_id=REVIEW_ID,
        customer="Halden Ridge Capital",
        framework=Framework.CAIQ,
        residency=Residency.US,
        state=ReviewState.DRAFTING,
        auto_send=auto_send,
        auto_send_enabled_by="Dana Whitfield" if auto_send else "",
        auto_send_enabled_at=datetime.now(UTC).isoformat() if auto_send else "",
    )
    answers = _Answers(
        [_answer(index, AnswerStatus.NEEDS_HUMAN) for index in range(held)]
        + [_answer(100, AnswerStatus.DRAFTED)]
    )
    audit = _Audit()
    fleet = _Fleet(answers)
    registry = HandlerRegistry(
        reviews=_Reviews(review),  # type: ignore[arg-type]
        rounds=_Rounds(),  # type: ignore[arg-type]
        questions=None,  # type: ignore[arg-type]
        answers=answers,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        publisher=_Publisher(),  # type: ignore[arg-type]
        fleet=fleet,  # type: ignore[arg-type]
    )
    return registry, audit, fleet


def _envelope() -> WorkEnvelope:
    return WorkEnvelope.for_work(
        message_id="run-1-assemble",
        review_id=REVIEW_ID,
        run_id="run-1",
        round_id=ROUND_ID,
        kind=WorkKind.ASSEMBLE_ROUND,
    )


class TestTheGateStillHoldsWhenTheSwitchIsOff:
    def test_a_round_with_held_answers_pauses(self) -> None:
        """The durable pause, unchanged. A new feature must not quietly weaken it."""
        registry, audit, fleet = _registry(auto_send=False, held=3)
        result = registry.assemble_round(_envelope())

        assert result.state is ReviewState.AWAITING_HUMAN
        assert result.published == []
        assert fleet.decisions == []
        assert [e for e in audit.events if e["kind"] == "human_decision"] == []


class TestAutoSendApprovesWithoutClaimingAPersonDid:
    def test_every_held_answer_is_resolved_and_the_round_continues(self) -> None:
        registry, _audit, fleet = _registry(auto_send=True, held=3)
        result = registry.assemble_round(_envelope())

        assert len(fleet.decisions) == 3
        assert result.state is ReviewState.ASSEMBLING
        # It closes rather than pausing, which is the whole point of the switch.
        assert [e.kind for e in result.published] == [WorkKind.CLOSE_ROUND]

    def test_the_actor_is_the_automation_and_never_the_person(self) -> None:
        """The property this feature lives or dies on.

        `Dana Whitfield` authorised the automation. She did not read these three answers,
        and the trail must not say she approved them.
        """
        registry, audit, fleet = _registry(auto_send=True, held=3)
        registry.assemble_round(_envelope())

        decisions = [e for e in audit.events if e["kind"] == "human_decision"]
        assert len(decisions) == 3
        assert {e["actor"] for e in decisions} == {"auto-send"}
        assert "Dana Whitfield" not in {e["actor"] for e in decisions}
        assert {r for _, r in fleet.decisions} == {"auto-send"}

    def test_who_enabled_it_is_recorded_where_it_is_true(self) -> None:
        registry, audit, _fleet = _registry(auto_send=True, held=2)
        registry.assemble_round(_envelope())

        decisions = [e for e in audit.events if e["kind"] == "human_decision"]
        assert all(e["detail"]["automated"] is True for e in decisions)
        assert {e["detail"]["enabled_by"] for e in decisions} == {"Dana Whitfield"}

    def test_a_round_holding_nothing_is_unaffected(self) -> None:
        registry, audit, fleet = _registry(auto_send=True, held=0)
        result = registry.assemble_round(_envelope())

        assert fleet.decisions == []
        assert [e for e in audit.events if e["kind"] == "human_decision"] == []
        assert result.state is ReviewState.ASSEMBLING


@pytest.mark.parametrize("held", [1, 5, 40])
def test_the_decision_count_always_matches_what_was_held(held: int) -> None:
    """One record per answer at every size. A batch that wrote one event would make the
    count of approvals disagree with the count of things approved."""
    registry, audit, _fleet = _registry(auto_send=True, held=held)
    registry.assemble_round(_envelope())
    assert len([e for e in audit.events if e["kind"] == "human_decision"]) == held
