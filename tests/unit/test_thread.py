"""The Review Thread, and the four ways a projection over an audit trail can lie.

The thread is the primary surface of the product and every sentence on it is a claim about
what the fleet did. So these tests are not about rendering — they are about the honesty
properties the projection has to hold:

1. **A check that did not run must not render as a check that passed.** No verification
   event, no verification post. This is `SupportVerdict.UNKNOWN` at the presentation layer,
   and it is the same failure this codebase has now found nine times in Python.
2. **Counts come from the answers, not from event arithmetic.** An audit write is non-fatal
   by contract, so events under-count. A post that says "42 held" must still say 41 after
   one is approved, even though the `human_required` events for all 42 are still on the
   trail forever.
3. **Truncation is never silent.** A block showing eight of forty-three says so.
4. **Order is derived, never assumed.** `for_review` applies no ordering, and Firestore
   returns auto-id documents in arbitrary order.

Plus the one behaviour that made the composer untrustworthy the first time it was used:
"how many are held?" resolving, confidently and completely wrongly, to a single question.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from attestor_core.domain import Answer, Citation, Question, Review, Round, SourceRef
from attestor_core.domain.enums import (
    AnswerStatus,
    Confidence,
    Department,
    Framework,
    Residency,
    ReviewState,
    SupportVerdict,
)
from attestor_platform.thread import answer_from_trail, build_thread

START = datetime(2026, 8, 17, 17, 50, tzinfo=UTC)


def at(seconds: int) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


def event(kind: str, seconds: int, **fields: Any) -> dict[str, Any]:
    detail = fields.pop("detail", {})
    return {
        "kind": kind,
        "review_id": "rev-test",
        "run_id": "run-test",
        "occurred_at": at(seconds),
        "detail": detail,
        **fields,
    }


def review(state: ReviewState = ReviewState.AWAITING_HUMAN) -> Review:
    return Review(
        review_id="rev-test",
        customer="Northwind Traders",
        framework=Framework.CAIQ,
        residency=Residency.US,
        created_at=START,
        current_round=1,
        state=state,
    )


def rounds() -> list[Round]:
    return [
        Round(
            round_id="rev-test-r1",
            review_id="rev-test",
            ordinal=1,
            received_at=START,
            state=ReviewState.DRAFTING,
        )
    ]


def questions(count: int = 3, department: Department = Department.SECURITY) -> list[Question]:
    return [
        Question(
            question_id=f"{index:016x}",
            text=f"Question number {index} about encryption at rest",
            raw_text=f"Question number {index}",
            department=department,
            source_ref=SourceRef(sheet="CAIQ", row=index + 100, cell=f"C{index + 100}"),
        )
        for index in range(1, count + 1)
    ]


def answer(
    question: Question,
    *,
    status: AnswerStatus = AnswerStatus.DRAFTED,
    citations: int = 1,
    support: SupportVerdict = SupportVerdict.UNKNOWN,
    verified_by: str = "",
) -> Answer:
    return Answer(
        question_id=question.question_id,
        round_id="rev-test-r1",
        text=f"Drafted answer for {question.question_id}",
        citations=[
            Citation(
                document_uri=f"gs://corpus/doc-{index}.md",
                document_title=f"Document {index}",
                section="4.2",
                snippet="Customer data is encrypted at rest.",
                retrieval_score=0.9,
            )
            for index in range(citations)
        ],
        confidence=Confidence.HIGH,
        status=status,
        authored_by="SecurityAgent",
        verified_by=verified_by,
        support=support,
        created_at=START,
    )


def thread_for(
    events: list[dict[str, Any]],
    *,
    qs: list[Question] | None = None,
    answers: list[Answer] | None = None,
    state: ReviewState = ReviewState.AWAITING_HUMAN,
    truncated: bool = False,
) -> Any:
    qs = questions() if qs is None else qs
    return build_thread(
        review=review(state),
        rounds=rounds(),
        questions=qs,
        answers=[] if answers is None else answers,
        events=events,
        truncated=truncated,
    )


def summaries(thread: Any) -> list[str]:
    return [post.summary for post in thread.posts]


def by_actor(thread: Any, actor: str) -> list[Any]:
    return [post for post in thread.posts if post.actor == actor]


class TestAnUnperformedCheckIsNotAPassedOne:
    """The property that matters most, because its failure mode looks like success."""

    def test_no_verification_event_means_no_verification_post(self) -> None:
        qs = questions()
        thread = thread_for(
            [event("answer_drafted", 10, question_id=q.question_id) for q in qs],
            qs=qs,
            answers=[answer(q) for q in qs],
        )
        assert by_actor(thread, "VerifierAgent") == []
        assert not any("supported" in summary for summary in summaries(thread))

    def test_unknown_verdicts_are_counted_and_named_rather_than_dropped(self) -> None:
        """`0 unsupported of 200` and `0 of 200, of which 180 were never checked` differ."""
        qs = questions(3)
        events = [
            event(
                "answer_verified",
                20 + index,
                question_id=q.question_id,
                actor="VerifierAgent",
                detail={"verdict": verdict, "drafted_by": "SecurityAgent"},
            )
            for index, (q, verdict) in enumerate(
                zip(qs, ["supported", "unknown", "unknown"], strict=True)
            )
        ]
        thread = thread_for(events, qs=qs, answers=[answer(q) for q in qs])
        post = by_actor(thread, "VerifierAgent")[0]
        assert "1 supported" in post.summary
        assert "2 could not be checked" in post.summary
        assert any("unperformed check is not a passed one" in line for line in post.lines)


class TestCountsComeFromAnswersNotEvents:
    def test_approving_one_reduces_the_held_count_though_the_events_remain(self) -> None:
        qs = questions(3)
        events = [
            event(
                "human_required",
                30 + index,
                question_id=q.question_id,
                actor="AssemblerAgent",
                detail={"reason": "low_confidence"},
            )
            for index, q in enumerate(qs)
        ]
        # All three escalated. Two are still held; one has since been approved, and its
        # `human_required` event is still on the trail because the trail is append-only.
        answers = [
            answer(qs[0], status=AnswerStatus.NEEDS_HUMAN),
            answer(qs[1], status=AnswerStatus.NEEDS_HUMAN),
            answer(qs[2], status=AnswerStatus.APPROVED),
        ]
        post = by_actor(thread_for(events, qs=qs, answers=answers), "AssemblerAgent")[0]
        assert "2 answers I will not release without you" in post.summary
        assert post.actions[0].kind == "approve"
        assert post.actions[0].count == 2

    def test_drafting_counters_come_from_the_answers_collection(self) -> None:
        qs = questions(4)
        # Only two drafting events survived; four answers exist. The counter must say four.
        events = [event("answer_drafted", 10 + i, question_id=qs[i].question_id) for i in range(2)]
        thread = thread_for(events, qs=qs, answers=[answer(q) for q in qs])
        post = by_actor(thread, "SecurityAgent")[0]
        assert post.progress[0].done == 4
        assert post.progress[0].total == 4


class TestNothingIsTruncatedSilently:
    def test_a_sampled_block_says_how_many_it_left_out(self) -> None:
        qs = questions(20)
        events = [
            event(
                "human_required",
                30 + index,
                question_id=q.question_id,
                actor="AssemblerAgent",
                detail={"reason": "low_confidence"},
            )
            for index, q in enumerate(qs)
        ]
        answers = [answer(q, status=AnswerStatus.NEEDS_HUMAN) for q in qs]
        post = by_actor(thread_for(events, qs=qs, answers=answers), "AssemblerAgent")[0]
        waiting = next(block for block in post.details if block.heading == "Waiting on you")
        assert len(waiting.rows) == 8
        assert "12 further answers not listed here" in waiting.note

    def test_a_truncated_audit_read_is_declared_on_the_thread(self) -> None:
        assert thread_for([], truncated=True).truncated is True
        assert thread_for([]).truncated is False


class TestOrderIsDerivedNotAssumed:
    def test_posts_sort_by_time_however_the_events_arrive(self) -> None:
        qs = questions(1)
        shuffled = [
            event("run_completed", 900, actor="Orchestrator", detail={"answered": 1}),
            event("question_triaged", 20, actor="TriageAgent", detail={"model": "flash"}),
            event(
                "stage_completed",
                5,
                actor="Dispatcher",
                detail={"stage": "intake_document", "questions": 1},
            ),
        ]
        thread = thread_for(shuffled, qs=qs)
        assert [post.kind for post in thread.posts] == ["arrival", "triage", "closed"]

    def test_the_assembly_post_lands_after_drafting_not_at_the_first_escalation(self) -> None:
        """`human_required` fires per answer during drafting, minutes before assembly."""
        qs = questions(2)
        events = [
            event(
                "human_required",
                40,
                question_id=qs[0].question_id,
                actor="AssemblerAgent",
                detail={"reason": "low_confidence"},
            ),
            event("answer_drafted", 300, question_id=qs[1].question_id, actor="SecurityAgent"),
            event(
                "stage_completed",
                600,
                actor="Dispatcher",
                detail={"stage": "assemble_round", "awaiting_human": 1, "answers": 2},
            ),
        ]
        answers = [answer(qs[0], status=AnswerStatus.NEEDS_HUMAN), answer(qs[1])]
        thread = thread_for(events, qs=qs, answers=answers)
        kinds = [post.kind for post in thread.posts]
        assert kinds.index("assembly") > kinds.index("drafting")


class TestTheThreadDescribesOnlyWhatHappened:
    def test_a_review_with_no_events_says_so_rather_than_rendering_empty(self) -> None:
        thread = thread_for([], qs=[])
        assert len(thread.posts) == 1
        assert thread.posts[0].kind == "pending"
        assert "No stage has reported yet" in thread.posts[0].summary

    def test_an_emailed_review_is_posted_by_the_inbox_agent_with_its_classification(self) -> None:
        thread = thread_for(
            [
                event(
                    "review_started_by_email",
                    0,
                    actor="InboxAgent",
                    detail={
                        "sender": "procurement@northwind.com",
                        "subject": "Vendor security review",
                        "attachments": ["caiq-v4.xlsx"],
                        "framework": "caiq",
                        "is_security_review": True,
                        "decided_by": "model",
                        "reason": "A CAIQ workbook is attached.",
                        "deadline": "2026-09-03",
                    },
                )
            ]
        )
        post = thread.posts[0]
        assert post.actor == "InboxAgent"
        assert "procurement@northwind.com" in post.summary
        assert "2026-09-03" in post.summary
        assert any("classified" in block.heading for block in post.details)

    def test_a_review_started_here_is_not_dressed_as_one_that_arrived_by_email(self) -> None:
        thread = thread_for(
            [
                event(
                    "stage_completed",
                    5,
                    actor="Dispatcher",
                    detail={"stage": "intake_document", "questions": 3, "round_id": "rev-test-r1"},
                )
            ]
        )
        assert thread.posts[0].actor == "Orchestrator"
        assert "@" not in thread.posts[0].summary

    def test_an_empty_detail_block_is_dropped_rather_than_shown_with_no_rows(self) -> None:
        qs = questions(1)
        thread = thread_for(
            [event("answer_drafted", 10, question_id=qs[0].question_id, actor="SecurityAgent")],
            qs=qs,
            answers=[answer(qs[0])],
        )
        for post in thread.posts:
            for block in post.details:
                assert block.rows or block.note


class TestTheRunIdComesOffTheTrail:
    def test_the_thread_carries_the_run_to_watch_and_how_the_work_arrived(self) -> None:
        thread = thread_for(
            [
                event("review_started_by_email", 0, actor="InboxAgent", detail={"sender": "a@b.c"}),
                event("question_triaged", 20, actor="TriageAgent", detail={"model": "flash"}),
            ]
        )
        assert thread.run_id == "run-test"
        assert thread.arrived_by_email is True
        assert thread_for([]).arrived_by_email is False


class TestAskingTheThread:
    """The composer. Its failure mode is fluency, so these pin the refusals."""

    def test_a_question_about_the_round_is_not_answered_about_one_row(self) -> None:
        """The measured defect: "how many are held?" reduces to the single word *held*,
        which appears in some answer somewhere, scoring 1.0 against it."""
        qs = questions(3)
        answers = [
            answer(qs[0], status=AnswerStatus.NEEDS_HUMAN),
            answer(qs[1], status=AnswerStatus.NEEDS_HUMAN),
            answer(qs[2]),
        ]
        composed = answer_from_trail(
            "how many are held?",
            review=review(),
            questions=qs,
            answers=answers,
            events=[
                event(
                    "human_required",
                    30,
                    question_id=qs[0].question_id,
                    detail={"reason": "low_confidence"},
                )
            ],
        )
        assert composed.question_id is None
        assert composed.answer.startswith("2 of 3 answers are held")

    def test_a_question_named_by_number_resolves_to_that_question(self) -> None:
        qs = questions(3)
        composed = answer_from_trail(
            "Why is Q2 held?",
            review=review(),
            questions=qs,
            answers=[answer(qs[1], status=AnswerStatus.NEEDS_HUMAN)],
            events=[
                event(
                    "human_required",
                    30,
                    question_id=qs[1].question_id,
                    detail={"reason": "low_confidence"},
                )
            ],
        )
        assert composed.question_id == qs[1].question_id
        assert composed.answer.startswith("Q2 is held for a person because low confidence")

    def test_a_number_past_the_end_of_the_round_resolves_to_nothing(self) -> None:
        qs = questions(3)
        composed = answer_from_trail(
            "Why is Q900 held?", review=review(), questions=qs, answers=[], events=[]
        )
        assert composed.question_id is None

    def test_an_unanswerable_question_says_so_and_lists_what_the_record_holds(self) -> None:
        qs = questions(3)
        composed = answer_from_trail(
            "what did this run cost in dollars",
            review=review(),
            questions=qs,
            answers=[],
            events=[event("question_triaged", 20, detail={"model": "flash"})],
        )
        assert composed.answer == "That is not something this review's record answers."
        assert any("record holds" in block.heading for block in composed.details)

    def test_the_reply_carries_the_audit_rows_it_was_read_out_of(self) -> None:
        qs = questions(2)
        events = [
            event(
                "question_triaged",
                20,
                question_id=qs[0].question_id,
                actor="TriageAgent",
                detail={"department": "security", "model": "gemini-3.5-flash-lite"},
            ),
            event(
                "answer_verified",
                40,
                question_id=qs[0].question_id,
                actor="VerifierAgent",
                detail={
                    "verdict": "partially_supported",
                    "unsupported_claims": ["customer-managed keys"],
                    "drafted_by": "SecurityAgent",
                },
            ),
        ]
        composed = answer_from_trail(
            "Q1",
            review=review(),
            questions=qs,
            answers=[
                answer(
                    qs[0], support=SupportVerdict.PARTIALLY_SUPPORTED, verified_by="VerifierAgent"
                )
            ],
            events=events,
        )
        headings = [block.heading for block in composed.details]
        assert "Every audit event for this question, in order" in headings
        assert any("customer-managed keys" in line for line in composed.lines)

    def test_the_reply_serialises_whole_so_it_reads_back_unchanged(self) -> None:
        qs = questions(2)
        composed = answer_from_trail("Q1", review=review(), questions=qs, answers=[], events=[])
        stored = composed.as_detail()
        assert stored["answer"] == composed.answer
        assert stored["question_id"] == qs[0].question_id
        assert isinstance(stored["details"], list)
        assert all("heading" in block and "rows" in block for block in stored["details"])
