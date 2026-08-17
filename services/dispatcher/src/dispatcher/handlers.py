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

The fleet itself. Handlers call a `FleetRunner`, which in Phase 4 was the in-process
`ReviewPipeline` from Phase 3. Phase 5 swapped it for the deployed department engines —
and this module did not change to allow it, which is the seam doing its job.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from google.cloud import firestore

from attestor_core.domain import Department, Question, Review, Round
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
from attestor_platform.config import max_questions_per_round
from attestor_platform.firestore import (
    AnswerRepository,
    AuditEventRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
)
from attestor_platform.pubsub import WorkPublisher
from dispatcher.runner import FleetRunner, build_fleet_runner

logger = logging.getLogger(__name__)

#: Drafting partitions. Every department that can own a question, so the join is complete
#: exactly when all of them have reported.
DRAFT_PARTITIONS: tuple[Department, ...] = (
    Department.SECURITY,
    Department.LEGAL,
    Department.ENGINEERING,
)

#: Where the drafting join is recorded. A SEPARATE collection from `rounds` on purpose,
#: and the first end-to-end run is what taught that: writing `drafted_partitions` onto the
#: round document made `RoundRepository.get` fail with
#:   "Extra inputs are not permitted [type=extra_forbidden]"
#: because `Round` is a strict model. All three drafting partitions failed, retried, and
#: failed again. The join is dispatcher bookkeeping rather than domain state -- putting it
#: on the domain model would also have leaked infrastructure counters into `generated.ts`
#: and the UI.
ROUND_PROGRESS = "round_progress"

#: Questions the triage stage could not place. They are drafted by the cross-department
#: path, which the security partition owns -- arbitrary but fixed, so the same question
#: never lands in two partitions.
UNASSIGNED_OWNER = Department.SECURITY


def _apply_ceiling(questions: list[Question]) -> tuple[list[Question], int]:
    """Cap a round at this deployment's question ceiling.

    The browser can start a review now, and the control plane cannot know how many questions
    a spreadsheet contains until it has been parsed -- which happens here. So the ceiling is
    enforced at the only point that knows the number.

    Truncation rather than refusal, and the questions kept are the first ones in the file, so
    the result is the front of the customer's questionnaire rather than an arbitrary sample.
    An `intake_truncated` audit event and a `dropped_over_ceiling` figure on the stage make
    the omission explicit everywhere it could matter; nothing about this is silent.
    """
    ceiling = max_questions_per_round()
    if len(questions) <= ceiling:
        return questions, 0
    return questions[:ceiling], len(questions) - ceiling


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
            self._fleet = build_fleet_runner()
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

        parsed = self.fleet.parse(payload.gcs_uri)
        questions, dropped = _apply_ceiling(parsed)
        written = self.questions.put_many(round_.round_id, questions)

        if dropped:
            # Recorded as its own event, not folded into the stage detail, because a
            # truncated questionnaire is a fact about the deliverable rather than about the
            # stage: the customer asked 4,000 questions and will get answers to 400. The
            # export and the UI both read this, so the omission is stated in the artefact
            # rather than discovered by counting rows.
            self.audit.append_safe(
                {
                    "kind": "intake_truncated",
                    "review_id": envelope.review_id,
                    "run_id": envelope.run_id,
                    "actor": "Dispatcher",
                    "detail": {
                        "round_id": round_.round_id,
                        "parsed": len(parsed),
                        "accepted": len(questions),
                        "dropped": dropped,
                        "ceiling": max_questions_per_round(),
                        "reason": (
                            "This deployment caps questions per round to bound demo spend. "
                            "It is a limit on this deployment, not on the architecture."
                        ),
                    },
                }
            )
            logger.warning(
                "intake truncated: %d parsed, %d accepted, ceiling %d",
                len(parsed),
                len(questions),
                max_questions_per_round(),
            )

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
        detail = {
            "questions": written,
            "gcs_uri": payload.gcs_uri,
            # The round this file belongs to. Recorded because the export reconstructs the
            # source questionnaire from this event for reviews started by the tools, which
            # never touch the control plane and so never wrote a `round_sources` record.
            "round_id": round_.round_id,
            "dropped_over_ceiling": dropped,
        }
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

        # ADR-0008. A redelivered partition skips what a previous attempt already finished,
        # and each answer is persisted the moment it is drafted rather than after the whole
        # slice returns.
        #
        # Why this is the difference between a partition that completes and one that never
        # can: the 312-question deployed run put 123 questions in one partition, which takes
        # ~1,550s, and the Pub/Sub ack deadline is 600s. Five delivery attempts of the same
        # 1,550s of work is five failures, not five chances -- the attempt count was never
        # the binding constraint. With progress persisted, attempt two starts at question 62
        # and finishes inside its own deadline.
        already = {a.question_id for a in self.answers.for_round(round_.round_id)}
        resumed = [q for q in mine if q.question_id in already]
        outstanding = [q for q in mine if q.question_id not in already]
        if resumed:
            logger.info(
                "partition %s resuming: %d of %d questions already answered",
                envelope.partition,
                len(resumed),
                len(mine),
            )

        answers = self.fleet.draft(
            envelope.review_id,
            envelope.run_id,
            round_.round_id,
            department,
            outstanding,
            self.answers.put,
        )

        remaining = self._close_partition(round_.round_id, envelope.partition)
        cited = sum(1 for a in answers if a.citations)
        detail = {
            "department": department.value,
            "questions": len(mine),
            # Named for what they now measure. Since the resume skip landed, `answers` is
            # what THIS attempt drafted, not what the partition holds -- on a redelivery the
            # two differ, and an audit event that said "answers: 61" for a partition of 123
            # would read as a partition that lost half its work.
            "answers": len(answers),
            "resumed_from_previous_attempt": len(resumed),
            "drafted_this_attempt": len(outstanding),
            "partition_total": len(resumed) + len(answers),
            "cited": cited,
            "needs_human": sum(1 for a in answers if a.status.value == "needs_human"),
            "flagged_no_evidence": sum(
                1 for a in answers if a.status.value == "flagged_no_evidence"
            ),
            "partitions_outstanding": sorted(remaining),
            # The concurrency figure, recorded per partition. Reported here so the
            # deployed run's number is in the immutable audit trail next to the work it
            # describes, rather than reconstructed afterwards from timestamps.
            **getattr(self.fleet, "last_draft_stats", {}),
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
        self.db.collection(ROUND_PROGRESS).document(round_id).set(
            {"drafted_partitions": [], "round_id": round_id}, merge=True
        )

    def _close_partition(self, round_id: str, partition: str) -> set[str]:
        """Mark one partition done; return the partitions still outstanding.

        Transactional, and a **set** rather than a counter: a redelivered partition adds
        a name already present and changes nothing, whereas a counter would reach three
        with one department having run twice and another never at all.
        """
        expected = {d.value for d in DRAFT_PARTITIONS}
        ref = self.db.collection(ROUND_PROGRESS).document(round_id)

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
