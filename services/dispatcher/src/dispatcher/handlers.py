"""Stage handlers — one per resumable transition of the review state machine.

Each handler does three things and nothing else: read the persisted state, do its slice of
the work, and publish what comes next. None of them knows it is running under Pub/Sub, and
none of them holds a request open longer than its own work takes. That is what makes the
whole thing resumable — a handler is a pure function of persisted state plus one envelope.

## The join

Drafting is three messages, one per department (ADR-0005). `assemble_round` must not run
until all three finish, and "the last one to finish publishes it" is a race: two
partitions completing concurrently both read "two done" and neither publishes, or both do.

So completion is recorded as a **set of partition names on the round document, updated in
a Firestore transaction**. A set rather than a counter because a redelivered partition
adds a name it already contains, which changes nothing — whereas a counter would reach
three with one department having run twice and another never at all.

## What is deliberately not here

The fleet itself. Handlers call a `FleetRunner`, which in Phase 4 is the in-process
`ReviewPipeline` from Phase 3. Phase 5 swaps it for a resumed Agent Runtime session
without touching this module.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.cloud import firestore

from attestor_core.domain import Department, Review, Round
from attestor_core.domain.enums import ReviewState
from attestor_core.errors import ContractViolation
from attestor_core.protocol import (
    IntakeDocumentPayload,
    OpenFollowUpPayload,
    ResumeAfterHumanPayload,
    WorkEnvelope,
    WorkKind,
    parse_payload,
)
from attestor_core.state import transition
from attestor_platform.firestore import (
    AnswerRepository,
    AuditEventRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
)
from attestor_platform.pubsub import WorkPublisher
from dispatcher.runner import FleetRunner, PipelineFleetRunner

logger = logging.getLogger(__name__)

#: Drafting partitions. Every department that can own a question, so the join is complete
#: exactly when all of them have reported.
DRAFT_PARTITIONS: tuple[Department, ...] = (
    Department.SECURITY,
    Department.LEGAL,
    Department.ENGINEERING,
)

#: Questions the triage stage could not place. They are drafted by the cross-department
#: path, which the security partition owns -- arbitrary but fixed, so the same question
#: never lands in two partitions.
UNASSIGNED_OWNER = Department.SECURITY


@dataclass
class HandlerResult:
    """What one handler did, for the ack decision and the log line."""

    state: ReviewState | None = None
    published: list[WorkEnvelope] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


class HandlerRegistry:
    """Maps a `WorkKind` to the function that performs it.

    Dependencies are injected rather than imported at call sites, so the end-to-end test
    can drive real handlers against fakes and the local harness can drive real handlers
    against the real project.
    """

    def __init__(
        self,
        *,
        reviews: ReviewRepository | None = None,
        rounds: RoundRepository | None = None,
        questions: QuestionRepository | None = None,
        answers: AnswerRepository | None = None,
        audit: AuditEventRepository | None = None,
        publisher: WorkPublisher | None = None,
        fleet: FleetRunner | None = None,
        db: firestore.Client | None = None,
    ) -> None:
        self._reviews = reviews
        self._rounds = rounds
        self._questions = questions
        self._answers = answers
        self._audit = audit
        self._publisher = publisher
        self._fleet = fleet
        self._db = db
        self._table: dict[WorkKind, Callable[[WorkEnvelope], HandlerResult]] = {
            WorkKind.INTAKE_DOCUMENT: self.intake_document,
            WorkKind.TRIAGE_QUESTIONS: self.triage_questions,
            WorkKind.DRAFT_ANSWER: self.draft_answer,
            WorkKind.ASSEMBLE_ROUND: self.assemble_round,
            WorkKind.CLOSE_ROUND: self.close_round,
            WorkKind.OPEN_FOLLOW_UP: self.open_follow_up,
            WorkKind.RESUME_AFTER_HUMAN: self.resume_after_human,
        }

    # -- lazy dependencies -------------------------------------------------------------
    # Every one is built on first use so that importing this module needs no credentials.

    @property
    def reviews(self) -> ReviewRepository:
        if self._reviews is None:
            self._reviews = ReviewRepository()
        return self._reviews

    @property
    def rounds(self) -> RoundRepository:
        if self._rounds is None:
            self._rounds = RoundRepository()
        return self._rounds

    @property
    def questions(self) -> QuestionRepository:
        if self._questions is None:
            self._questions = QuestionRepository()
        return self._questions

    @property
    def answers(self) -> AnswerRepository:
        if self._answers is None:
            self._answers = AnswerRepository()
        return self._answers

    @property
    def audit(self) -> AuditEventRepository:
        if self._audit is None:
            self._audit = AuditEventRepository()
        return self._audit

    @property
    def publisher(self) -> WorkPublisher:
        if self._publisher is None:
            self._publisher = WorkPublisher()
        return self._publisher

    @property
    def fleet(self) -> FleetRunner:
        if self._fleet is None:
            self._fleet = PipelineFleetRunner()
        return self._fleet

    @property
    def db(self) -> firestore.Client:
        if self._db is None:
            self._db = firestore.Client(project=os.environ.get("PROJECT_ID") or None)
        return self._db

    # -- dispatch ----------------------------------------------------------------------

    def run(self, envelope: WorkEnvelope) -> HandlerResult:
        """Execute the handler for this envelope's kind.

        Raises:
            ContractViolation: If no handler exists for the kind. Permanent by
                construction -- a kind this build does not implement will not start
                working on a retry.
        """
        handler = self._table.get(envelope.kind)
        if handler is None:
            raise ContractViolation(
                f"no handler for kind {envelope.kind.value!r}",
                review_id=envelope.review_id,
                run_id=envelope.run_id,
            )
        # Validates the payload at the edge rather than inside the handler.
        parse_payload(envelope)
        return handler(envelope)

    # -- helpers -----------------------------------------------------------------------

    def _require_review(self, review_id: str) -> Review:
        review = self.reviews.get(review_id)
        if review is None:
            raise ContractViolation(f"review {review_id!r} does not exist", review_id=review_id)
        return review

    def _require_round(self, envelope: WorkEnvelope) -> Round:
        if not envelope.round_id:
            raise ContractViolation(
                f"{envelope.kind.value} requires a round_id", review_id=envelope.review_id
            )
        round_ = self.rounds.get(envelope.round_id)
        if round_ is None:
            raise ContractViolation(
                f"round {envelope.round_id!r} does not exist", review_id=envelope.review_id
            )
        return round_

    def _move(self, review: Review, target: ReviewState) -> ReviewState:
        """Transition and persist. Raises `IllegalTransition`, which is dead-lettered.

        An illegal transition means the message does not apply to the review's current
        state -- usually a message that outlived its round. Permanent, not transient.
        """
        new_state = transition(review.state, target, review_id=review.review_id)
        self.reviews.put(review.model_copy(update={"state": new_state}))
        return new_state

    def _emit(self, envelope: WorkEnvelope) -> WorkEnvelope:
        self.publisher.publish(envelope)
        return envelope

    def _audit_stage(self, envelope: WorkEnvelope, detail: dict[str, Any]) -> None:
        self.audit.append_safe(
            {
                "kind": "stage_completed",
                "review_id": envelope.review_id,
                "run_id": envelope.run_id,
                "question_id": envelope.question_id,
                "actor": "Dispatcher",
                "detail": {
                    "stage": envelope.kind.value,
                    "partition": envelope.partition,
                    "dedup_key": envelope.dedup_key,
                    **detail,
                },
            }
        )

    # -- stages ------------------------------------------------------------------------

    def intake_document(self, envelope: WorkEnvelope) -> HandlerResult:
        """Parse the uploaded questionnaire into persisted questions."""
        payload = parse_payload(envelope)
        assert isinstance(payload, IntakeDocumentPayload)  # noqa: S101 - narrowed by kind

        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)

        questions = self.fleet.parse(payload.gcs_uri)
        written = self.questions.put_many(round_.round_id, questions)

        state = self._move(review, ReviewState.TRIAGING)
        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-triage",
                    review_id=envelope.review_id,
                    run_id=envelope.run_id,
                    round_id=round_.round_id,
                    kind=WorkKind.TRIAGE_QUESTIONS,
                )
            )
        ]
        detail = {"questions": written, "gcs_uri": payload.gcs_uri}
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, published=published, detail=detail)

    def triage_questions(self, envelope: WorkEnvelope) -> HandlerResult:
        """Classify every question, then fan out one message per department."""
        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)

        questions = self.questions.for_round(round_.round_id)
        if not questions:
            raise ContractViolation(
                f"round {round_.round_id!r} has no questions to triage",
                review_id=envelope.review_id,
            )

        triaged = self.fleet.triage(envelope.review_id, envelope.run_id, questions)
        self.questions.put_many(round_.round_id, triaged)

        state = self._move(review, ReviewState.DRAFTING)

        # Reset the join before publishing, so a re-run of triage cannot inherit a
        # half-finished set from a previous attempt.
        self._reset_join(round_.round_id)

        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-draft-{department.value}",
                    review_id=envelope.review_id,
                    run_id=envelope.run_id,
                    round_id=round_.round_id,
                    kind=WorkKind.DRAFT_ANSWER,
                    partition=department.value,
                )
            )
            for department in DRAFT_PARTITIONS
        ]

        counts: dict[str, int] = {}
        for question in triaged:
            counts[question.department.value] = counts.get(question.department.value, 0) + 1
        detail = {"triaged": len(triaged), "by_department": counts}
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, published=published, detail=detail)

    def draft_answer(self, envelope: WorkEnvelope) -> HandlerResult:
        """Draft one department's slice, then close the join if it is the last one."""
        if not envelope.partition:
            raise ContractViolation(
                "draft_answer requires a partition -- see ADR-0005",
                review_id=envelope.review_id,
            )
        try:
            department = Department(envelope.partition)
        except ValueError as exc:
            raise ContractViolation(
                f"unknown drafting partition {envelope.partition!r}",
                review_id=envelope.review_id,
            ) from exc

        self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)

        mine = [
            q
            for q in self.questions.for_round(round_.round_id)
            if q.department is department
            or (q.department is Department.UNASSIGNED and department is UNASSIGNED_OWNER)
        ]

        answers = self.fleet.draft(envelope.review_id, envelope.run_id, department, mine)
        for answer in answers:
            self.answers.put(answer)

        remaining = self._close_partition(round_.round_id, envelope.partition)
        detail = {
            "department": department.value,
            "questions": len(mine),
            "answers": len(answers),
            "partitions_outstanding": sorted(remaining),
        }
        self._audit_stage(envelope, detail)

        published: list[WorkEnvelope] = []
        if not remaining:
            published.append(
                self._emit(
                    WorkEnvelope.for_work(
                        message_id=f"{envelope.run_id}-assemble",
                        review_id=envelope.review_id,
                        run_id=envelope.run_id,
                        round_id=round_.round_id,
                        kind=WorkKind.ASSEMBLE_ROUND,
                    )
                )
            )
        return HandlerResult(published=published, detail=detail)

    def assemble_round(self, envelope: WorkEnvelope) -> HandlerResult:
        """Compose the round. Pause for a human if any answer needs one."""
        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)

        answers = self.answers.for_round(round_.round_id)
        pending = [a for a in answers if a.status.value == "needs_human"]

        if pending:
            state = self._move(review, ReviewState.AWAITING_HUMAN)
            detail = {
                "answers": len(answers),
                "awaiting_human": len(pending),
                "first_question_id": pending[0].question_id,
            }
            self._audit_stage(envelope, detail)
            # No publish. The run genuinely stops here until a human acts -- that is the
            # point of a durable pause rather than a poll loop.
            return HandlerResult(state=state, published=[], detail=detail)

        state = self._move(review, ReviewState.ASSEMBLING)
        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-close",
                    review_id=envelope.review_id,
                    run_id=envelope.run_id,
                    round_id=round_.round_id,
                    kind=WorkKind.CLOSE_ROUND,
                )
            )
        ]
        detail = {"answers": len(answers), "awaiting_human": 0}
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, published=published, detail=detail)

    def close_round(self, envelope: WorkEnvelope) -> HandlerResult:
        """Record commitments to Memory Bank and deliver the round."""
        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)

        answers = self.answers.for_round(round_.round_id)
        recorded = self.fleet.record_commitments(envelope.review_id, round_.round_id, answers)

        state = self._move(review, ReviewState.DELIVERED)
        detail = {"commitments_recorded": recorded, "answers": len(answers)}
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, detail=detail)

    def open_follow_up(self, envelope: WorkEnvelope) -> HandlerResult:
        """Round N>1 arrives. Load prior commitments and start triage again."""
        payload = parse_payload(envelope)
        assert isinstance(payload, OpenFollowUpPayload)  # noqa: S101 - narrowed by kind

        review = self._require_review(envelope.review_id)
        self._move(review, ReviewState.FOLLOW_UP)

        round_id = envelope.round_id or f"{envelope.review_id}-r{payload.round_ordinal}"
        questions = self.fleet.parse(payload.gcs_uri)
        self.rounds.put(
            Round(
                round_id=round_id,
                review_id=envelope.review_id,
                ordinal=payload.round_ordinal,
                state=ReviewState.TRIAGING,
            )
        )
        self.questions.put_many(round_id, questions)

        # Read the commitments now, on the round that will use them, so an unreachable
        # Memory Bank fails HERE -- loudly, at the start of the round -- rather than
        # silently disabling the consistency check for every question in it.
        commitments = self.fleet.load_commitments(envelope.review_id)

        state = self._move(self._require_review(envelope.review_id), ReviewState.TRIAGING)
        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-triage-r{payload.round_ordinal}",
                    review_id=envelope.review_id,
                    run_id=envelope.run_id,
                    round_id=round_id,
                    kind=WorkKind.TRIAGE_QUESTIONS,
                )
            )
        ]
        detail = {
            "round_id": round_id,
            "ordinal": payload.round_ordinal,
            "questions": len(questions),
            "prior_commitments": len(commitments),
        }
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, published=published, detail=detail)

    def resume_after_human(self, envelope: WorkEnvelope) -> HandlerResult:
        """Apply an approval decision and continue, or keep waiting for the rest."""
        payload = parse_payload(envelope)
        assert isinstance(payload, ResumeAfterHumanPayload)  # noqa: S101 - narrowed by kind

        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)
        if not envelope.question_id:
            raise ContractViolation(
                "resume_after_human requires the question_id being resolved",
                review_id=envelope.review_id,
            )

        resolved = self.fleet.apply_decision(
            round_.round_id,
            envelope.question_id,
            approved=payload.approved,
            resolved_by=payload.resolved_by,
            edited_text=payload.edited_text,
        )

        still_pending = [
            a for a in self.answers.for_round(round_.round_id) if a.status.value == "needs_human"
        ]
        detail: dict[str, Any] = {
            "question_id": envelope.question_id,
            "approved": payload.approved,
            "resolved_by": payload.resolved_by,
            "resolved": resolved,
            "still_pending": len(still_pending),
        }

        if still_pending:
            # Deliberately no transition: the review is already AWAITING_HUMAN and stays
            # there. Approving one of seventy answers does not resume the round.
            self._audit_stage(envelope, detail)
            return HandlerResult(state=review.state, published=[], detail=detail)

        state = self._move(review, ReviewState.ASSEMBLING)
        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-close-after-human",
                    review_id=envelope.review_id,
                    run_id=envelope.run_id,
                    round_id=round_.round_id,
                    kind=WorkKind.CLOSE_ROUND,
                )
            )
        ]
        self._audit_stage(envelope, detail)
        return HandlerResult(state=state, published=published, detail=detail)

    # -- the join ----------------------------------------------------------------------

    def _reset_join(self, round_id: str) -> None:
        self.db.collection("rounds").document(round_id).set({"drafted_partitions": []}, merge=True)

    def _close_partition(self, round_id: str, partition: str) -> set[str]:
        """Mark one partition done; return the partitions still outstanding.

        Transactional, and a **set** rather than a counter: a redelivered partition adds
        a name already present and changes nothing, whereas a counter would reach three
        with one department having run twice and another never at all.
        """
        expected = {d.value for d in DRAFT_PARTITIONS}
        ref = self.db.collection("rounds").document(round_id)

        @firestore.transactional
        def _commit(txn: firestore.Transaction) -> set[str]:
            snapshot = ref.get(transaction=txn)
            data = snapshot.to_dict() or {}
            done = set(data.get("drafted_partitions") or [])
            done.add(partition)
            txn.set(ref, {"drafted_partitions": sorted(done)}, merge=True)
            return expected - done

        result: set[str] = _commit(self.db.transaction())
        return result
