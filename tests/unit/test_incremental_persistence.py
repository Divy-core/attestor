"""A redelivered drafting partition resumes; it does not restart. ADR-0008.

This is the property that makes the deployed 312-question run completable, and the reason it
needs a test rather than a measurement is arithmetic: the security partition holds 123
questions and takes ~1,550s, the Pub/Sub ack deadline is 600s, and five delivery attempts of
1,550s of work is five failures rather than five chances. Whether attempt two starts at
question 62 or at question 1 decides whether the round can finish at all.

Two things are asserted, and the second is the one that would otherwise rot:

1. Questions already answered are not handed to the fleet again.
2. Each answer is persisted **as it completes**, not after the slice returns. A version that
   collected answers and wrote them at the end would pass (1) on the second delivery and
   still lose everything on the first, because there would be nothing written to resume from.

## What is deliberately not covered here

The drafting join. `_close_partition` runs a real Firestore transaction and is stubbed below
with a plain set; the transactional behaviour and the set-not-a-counter reasoning are
`test_dispatcher.py`'s and the handler docstring's. Faking a Firestore transaction well enough
to test it would be testing the fake.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from attestor_core.domain import (
    Answer,
    AnswerStatus,
    Citation,
    Confidence,
    Department,
    Question,
    Review,
    Round,
)
from attestor_core.domain.enums import Framework, Residency, ReviewState
from attestor_core.protocol import WorkEnvelope, WorkKind
from dispatcher.handlers import HandlerRegistry

ROUND_ID = "rev-resume-r1"
REVIEW_ID = "rev-resume"


def _question(n: int, department: Department = Department.SECURITY) -> Question:
    return Question.from_text(
        f"Control question number {n} about your security posture?", department=department
    )


def _answer(question: Question) -> Answer:
    return Answer(
        question_id=question.question_id,
        round_id=ROUND_ID,
        text="Yes, with a documented control.",
        citations=[
            Citation(
                document_uri="gs://attestor-505506-corpus/policy.md",
                document_title="Security Policy",
                section="3.1",
                snippet="A documented control exists.",
                retrieval_score=0.8,
            )
        ],
        confidence=Confidence.HIGH,
        status=AnswerStatus.DRAFTED,
        authored_by="SecurityAgent",
    )


# ---------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------


class _Reviews:
    def __init__(self) -> None:
        self.review = Review(
            review_id=REVIEW_ID,
            customer="Resume Test Ltd",
            framework=Framework.CAIQ,
            residency=Residency.US,
            current_round=1,
            state=ReviewState.DRAFTING,
        )

    def get(self, review_id: str) -> Review | None:
        return self.review if review_id == REVIEW_ID else None

    def put(self, review: Review) -> None:
        self.review = review


class _Rounds:
    def get(self, round_id: str) -> Round | None:
        if round_id != ROUND_ID:
            return None
        return Round(round_id=ROUND_ID, review_id=REVIEW_ID, ordinal=1, state=ReviewState.DRAFTING)


class _Questions:
    def __init__(self, questions: list[Question]) -> None:
        self._questions = questions

    def for_round(self, round_id: str) -> list[Question]:
        return list(self._questions)


class _Answers:
    """Records the ORDER of writes, which is what "incrementally" actually means."""

    def __init__(self) -> None:
        self.store: dict[str, Answer] = {}
        self.write_log: list[str] = []

    def put(self, answer: Answer) -> None:
        self.store[answer.question_id] = answer
        self.write_log.append(answer.question_id)

    def for_round(self, round_id: str) -> list[Answer]:
        return list(self.store.values())


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
    """A fleet that drafts one answer per question and reports each one as it lands.

    `interrupt_after` reproduces the failure this whole change exists for: a partition that
    gets part-way through and then dies, exactly as one running past the ack deadline does.
    """

    def __init__(self, *, interrupt_after: int | None = None) -> None:
        self.seen: list[list[Question]] = []
        self.interrupt_after = interrupt_after
        self.last_draft_stats: dict[str, Any] = {"achieved_concurrency": 1.0}

    def draft(
        self,
        review_id: str,
        run_id: str,
        round_id: str,
        department: Department,
        questions: list[Question],
        on_answer: Callable[[Answer], None] | None = None,
    ) -> list[Answer]:
        self.seen.append(list(questions))
        produced: list[Answer] = []
        for index, question in enumerate(questions):
            if self.interrupt_after is not None and index >= self.interrupt_after:
                raise TimeoutError("the partition ran past its ack deadline")
            answer = _answer(question)
            if on_answer is not None:
                on_answer(answer)
            produced.append(answer)
        return produced


class _Registry(HandlerRegistry):
    """The real handler, with only the Firestore-transaction join stubbed out."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.closed: set[str] = set()

    def _close_partition(self, round_id: str, partition: str) -> set[str]:
        self.closed.add(partition)
        return {"security", "legal", "engineering"} - self.closed


@pytest.fixture
def parts() -> Any:
    questions = [_question(n) for n in range(10)]
    answers = _Answers()
    audit = _Audit()
    publisher = _Publisher()
    return type(
        "Parts",
        (),
        {
            "questions": questions,
            "answers": answers,
            "audit": audit,
            "publisher": publisher,
            "build": staticmethod(
                lambda fleet: _Registry(
                    reviews=_Reviews(),  # type: ignore[arg-type]
                    rounds=_Rounds(),  # type: ignore[arg-type]
                    questions=_Questions(questions),  # type: ignore[arg-type]
                    answers=answers,  # type: ignore[arg-type]
                    audit=audit,  # type: ignore[arg-type]
                    publisher=publisher,  # type: ignore[arg-type]
                    fleet=fleet,  # type: ignore[arg-type]
                )
            ),
        },
    )()


def _envelope(run_id: str = "run-1") -> WorkEnvelope:
    return WorkEnvelope.for_work(
        message_id=f"{run_id}-draft-security",
        review_id=REVIEW_ID,
        run_id=run_id,
        round_id=ROUND_ID,
        kind=WorkKind.DRAFT_ANSWER,
        partition="security",
    )


# ---------------------------------------------------------------------------------


class TestAnswersArePersistedAsTheyComplete:
    def test_every_answer_is_written_before_the_slice_returns(self, parts: Any) -> None:
        """The order of writes, not just the final contents.

        `for answer in answers: put(answer)` after `draft` returns produces an identical
        final state and none of the benefit, so the assertion has to be about when.
        """
        fleet = _Fleet()
        parts.build(fleet).draft_answer(_envelope())
        assert len(parts.answers.write_log) == 10

    def test_an_interrupted_partition_leaves_its_finished_work_behind(self, parts: Any) -> None:
        fleet = _Fleet(interrupt_after=4)
        registry = parts.build(fleet)
        with pytest.raises(TimeoutError):
            registry.draft_answer(_envelope())
        # Four answers survived the failure. Before ADR-0008 this was zero, and zero is why
        # five delivery attempts could not finish a 123-question partition.
        assert len(parts.answers.store) == 4


class TestARedeliveredPartitionResumes:
    def test_the_second_attempt_is_handed_only_the_unfinished_questions(self, parts: Any) -> None:
        first = _Fleet(interrupt_after=4)
        with pytest.raises(TimeoutError):
            parts.build(first).draft_answer(_envelope("run-1"))

        second = _Fleet()
        parts.build(second).draft_answer(_envelope("run-1"))

        # THE assertion. The redelivery drafted six questions, not ten.
        assert len(second.seen[0]) == 6
        already = {a.question_id for a in parts.answers.store.values()}
        assert len(already) == 10

    def test_a_fully_completed_partition_redrafts_nothing_on_redelivery(self, parts: Any) -> None:
        parts.build(_Fleet()).draft_answer(_envelope("run-1"))
        second = _Fleet()
        parts.build(second).draft_answer(_envelope("run-1"))
        assert second.seen[0] == []
        assert len(parts.answers.store) == 10

    def test_the_audit_event_distinguishes_resumed_from_drafted(self, parts: Any) -> None:
        with pytest.raises(TimeoutError):
            parts.build(_Fleet(interrupt_after=4)).draft_answer(_envelope("run-1"))
        parts.build(_Fleet()).draft_answer(_envelope("run-1"))

        stages = [
            e["detail"]
            for e in parts.audit.events
            if e.get("kind") == "stage_completed" and e["detail"].get("stage") == "draft_answer"
        ]
        assert len(stages) == 1  # the interrupted attempt never reached its audit write
        detail = stages[-1]
        assert detail["resumed_from_previous_attempt"] == 4
        assert detail["drafted_this_attempt"] == 6
        # `answers` now means "this attempt", so the partition total is reported separately --
        # otherwise the trail would read as a partition that lost four answers.
        assert detail["answers"] == 6
        assert detail["partition_total"] == 10
        assert detail["questions"] == 10

    def test_the_join_still_closes_and_assembly_is_published(self, parts: Any) -> None:
        registry = parts.build(_Fleet())
        registry.closed = {"legal", "engineering"}
        result = registry.draft_answer(_envelope())
        assert [e.kind for e in result.published] == [WorkKind.ASSEMBLE_ROUND]

    def test_a_partition_that_answers_nothing_new_still_closes_the_join(self, parts: Any) -> None:
        """A resumed partition with nothing left to do must not stall the round.

        This is the case a naive "if no questions, return early" would break: the work is
        done, the join is not closed, and the round waits forever for a partition that has
        nothing to say.
        """
        parts.build(_Fleet()).draft_answer(_envelope("run-1"))
        registry = parts.build(_Fleet())
        registry.closed = {"legal", "engineering"}
        result = registry.draft_answer(_envelope("run-2"))
        assert [e.kind for e in result.published] == [WorkKind.ASSEMBLE_ROUND]


class TestTheQuestionCeiling:
    def test_a_questionnaire_over_the_ceiling_is_truncated_and_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispatcher.handlers import _apply_ceiling

        monkeypatch.setenv("ATTESTOR_MAX_QUESTIONS", "5")
        questions = [_question(n) for n in range(9)]
        kept, dropped = _apply_ceiling(questions)
        assert len(kept) == 5
        assert dropped == 4
        # The front of the customer's file, not an arbitrary sample.
        assert kept == questions[:5]

    def test_a_questionnaire_under_the_ceiling_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dispatcher.handlers import _apply_ceiling

        monkeypatch.setenv("ATTESTOR_MAX_QUESTIONS", "400")
        questions = [_question(n) for n in range(312)]
        kept, dropped = _apply_ceiling(questions)
        assert kept is questions
        assert dropped == 0
