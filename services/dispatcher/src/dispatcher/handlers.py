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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from google.cloud import firestore

from attestor_core.domain import Department, Question, Review, Round
from attestor_core.domain.enums import Residency, ReviewState
from attestor_core.errors import ContractViolation
from attestor_core.protocol import (
    DeliverPackPayload,
    InboxMessagePayload,
    IntakeDocumentPayload,
    OpenFollowUpPayload,
    ResumeAfterHumanPayload,
    WorkEnvelope,
    WorkKind,
    parse_payload,
)
from attestor_core.state import transition
from attestor_platform.config import max_active_reviews, max_questions_per_round
from attestor_platform.firestore import (
    AnswerRepository,
    ArtifactRepository,
    AuditEventRepository,
    InboxStateRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
    RoundSourceRepository,
)
from attestor_platform.gmail import (
    GmailClient,
    InboundMessage,
    stage_attachment,
    stage_body_questions,
)
from attestor_platform.pubsub import WorkPublisher
from dispatcher.runner import FleetRunner, build_fleet_runner

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _console_url() -> str:
    """Where a person goes to act on this. Configured, because the dispatcher has no way to
    know the web service's URL -- it is a different Cloud Run service and nothing injects it.
    An unset value produces a relative link rather than a broken absolute one."""
    return os.environ.get("ATTESTOR_CONSOLE_URL", "").rstrip("/")


def _origin() -> str:
    """Which deployment produced a file. Built from Cloud Run's own variables, so it cannot
    claim to be a revision it is not."""
    service = os.environ.get("K_SERVICE")
    if not service:
        return "attestor (local)"
    return (
        f"{service} {os.environ.get('K_REVISION', 'unknown-revision')} · "
        f"{os.environ.get('PROJECT_ID', 'unknown-project')} · "
        f"{os.environ.get('REGION', 'unknown-region')}"
    )


def _covering_note(review: Review, bundle: Any, note: str) -> str:
    """What the customer reads before opening the attachment.

    States what was answered, what was held, and what is outstanding -- in that order and in
    numbers, because a covering note that says "please find attached" makes the recipient
    open a 312-row spreadsheet to discover that 43 rows need a conversation.
    """
    held = len(bundle.rows) - bundle.sendable
    lines = [
        f"Attached is our completed response for {review.customer}.",
        "",
        f"  {bundle.sendable} of {len(bundle.rows)} questions are answered and sendable.",
        f"  {bundle.human_approved} were reviewed and approved by a named person.",
    ]
    if held:
        lines.append(
            f"  {held} are not included as answers: they are held for review, unsupported by "
            "our evidence, or blocked by our guardrail. Each one says which, in the workbook."
        )
    lines += [
        "",
        "Every answer carries the documents and sections it is based on, with a relevance "
        "score, in the evidence pack. Nothing is asserted without them.",
    ]
    if note.strip():
        lines += ["", note.strip()]
    return "\n".join(lines)


#: Gmail labels the fleet applies, so the mailbox itself shows what happened to a thread.
LABEL_STARTED = "Attestor/Review started"
LABEL_FOLLOW_UP = "Attestor/Follow-up"
LABEL_IGNORED = "Attestor/Not a review"
LABEL_HELD = "Attestor/Held"

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


def split_partition(raw: str) -> tuple[Department, int]:
    """Read a drafting partition as a department and an attempt sequence.

        "security"    -> (SECURITY, 1)
        "security@3"  -> (SECURITY, 3)

    The suffix exists because a bounded attempt has to publish a continuation, and a
    continuation needs a **different dedup key** — which `WorkEnvelope.for_work` derives from
    `(review, round, question, partition, kind)` and not from `message_id` (ADR-0005). The
    partition string is the only component of that tuple which can honestly vary between one
    attempt at a department's slice and the next.

    Not a protocol change: `partition` is `str | None` and stays `str | None`, so nothing in
    `generated.ts` or the frozen envelope moves. What changes is that the dispatcher reads
    structure in a string it was already the only consumer of.

    The **department** is what closes the join. `_close_partition` compares against
    `{d.value for d in DRAFT_PARTITIONS}`, so passing `security@3` there would leave the join
    permanently one short and no round would ever assemble.

    Raises:
        ValueError: on an unknown department or a non-numeric sequence, which the caller turns
            into a `ContractViolation` -- permanent, because a malformed partition will not
            become well-formed on a retry.
    """
    name, _, sequence = raw.partition("@")
    return Department(name), int(sequence) if sequence else 1


#: How long one `draft_answer` attempt may spend starting new questions.
#:
#: The number this exists to stay under is the Pub/Sub ack deadline, 600s, which is the
#: platform maximum. A partition of 121 questions at ~45s each cannot finish inside it however
#: it is scheduled, and until this budget existed the handler simply tried: the attempt ran
#: past 600s, Pub/Sub redelivered, the live lease refused the redelivery with a 409 -- and
#: **that refusal consumed a delivery attempt**. Five attempts could be spent on refusals of a
#: single long attempt rather than on work.
#:
#: The measured shape of that, on the first deployed 312-question run with incremental
#: persistence: engineering completed on attempt 5 having resumed 70 of 93; security reached
#: 118 of 121 and legal 98 of 98, and both were dead-lettered with the round holding 309 of 312
#: answers and no assembly. The resume was working. The attempt budget was the missing half.
#:
#: 420s leaves 180s of margin for the questions already in flight when the budget expires,
#: plus the join write. Configurable because the right value depends on per-question latency,
#: which depends on the engine.
DRAFT_BUDGET_SECONDS = float(os.environ.get("ATTESTOR_DRAFT_BUDGET_SECONDS", "420"))


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


#: How much of an inbound email body is kept on the audit trail.
#:
#: A questionnaire email is a few hundred words. This is an append-only store that the thread
#: projection reads on every render, so an unbounded field here is a document nobody decided
#: to keep, copied on every read, forever.
INBOUND_BODY_CEILING = 4_000


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
        inbox_state: InboxStateRepository | None = None,
        gmail: GmailClient | None = None,
        round_sources: RoundSourceRepository | None = None,
        artifacts: ArtifactRepository | None = None,
        drive: Any | None = None,
    ) -> None:
        self._reviews = reviews
        self._rounds = rounds
        self._questions = questions
        self._answers = answers
        self._audit = audit
        self._publisher = publisher
        self._fleet = fleet
        self._db = db
        self._inbox_state = inbox_state
        self._gmail = gmail
        self._round_sources = round_sources
        self._artifacts = artifacts
        self._drive = drive
        self._table: dict[WorkKind, Callable[[WorkEnvelope], HandlerResult]] = {
            WorkKind.INTAKE_DOCUMENT: self.intake_document,
            WorkKind.TRIAGE_QUESTIONS: self.triage_questions,
            WorkKind.DRAFT_ANSWER: self.draft_answer,
            WorkKind.ASSEMBLE_ROUND: self.assemble_round,
            WorkKind.CLOSE_ROUND: self.close_round,
            WorkKind.OPEN_FOLLOW_UP: self.open_follow_up,
            WorkKind.RESUME_AFTER_HUMAN: self.resume_after_human,
            WorkKind.INBOX_MESSAGE: self.inbox_message,
            WorkKind.DELIVER_PACK: self.deliver_pack,
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
    def inbox_state(self) -> InboxStateRepository:
        if self._inbox_state is None:
            self._inbox_state = InboxStateRepository()
        return self._inbox_state

    @property
    def gmail(self) -> GmailClient:
        if self._gmail is None:
            self._gmail = GmailClient()
        return self._gmail

    @property
    def artifacts(self) -> ArtifactRepository:
        if self._artifacts is None:
            self._artifacts = ArtifactRepository()
        return self._artifacts

    @property
    def drive(self) -> Any:
        if self._drive is None:
            from attestor_platform.drive import DriveClient

            self._drive = DriveClient()
        return self._drive

    @property
    def round_sources(self) -> RoundSourceRepository:
        if self._round_sources is None:
            self._round_sources = RoundSourceRepository()
        return self._round_sources

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
            department, sequence = split_partition(envelope.partition)
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
            DRAFT_BUDGET_SECONDS,
        )

        # Whatever the budget stopped this attempt from starting.
        drafted_ids = {a.question_id for a in answers}
        deferred = [q for q in outstanding if q.question_id not in drafted_ids]
        if deferred:
            return self._continue_partition(
                envelope, round_, department, sequence, mine, resumed, answers, deferred
            )

        remaining = self._close_partition(round_.round_id, department.value)
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

        if pending and review.auto_send:
            # The gate, deliberately opened for this review. Each answer is still resolved
            # individually and recorded individually; what is skipped is the waiting, not
            # the record. `auto-send` is the actor, never the person who flipped the switch:
            # they authorised the automation, they did not read these answers.
            for answer in pending:
                self.fleet.apply_decision(
                    round_.round_id,
                    answer.question_id,
                    approved=True,
                    resolved_by="auto-send",
                    edited_text=None,
                )
                self.audit.append_safe(
                    {
                        "kind": "human_decision",
                        "review_id": review.review_id,
                        "run_id": envelope.run_id,
                        "question_id": answer.question_id,
                        "actor": "auto-send",
                        "detail": {
                            "approved": True,
                            "edited": False,
                            "round_id": round_.round_id,
                            "automated": True,
                            "enabled_by": review.auto_send_enabled_by,
                            "enabled_at": review.auto_send_enabled_at,
                        },
                    }
                )
            pending = []

        if pending:
            state = self._move(review, ReviewState.AWAITING_HUMAN)
            detail = {
                "answers": len(answers),
                "awaiting_human": len(pending),
                "first_question_id": pending[0].question_id,
            }
            self._audit_stage(envelope, detail)
            self._notify_human(review, round_, len(pending), envelope.run_id)
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
        # The review has to remember which round it is on, and until Phase 7 nothing wrote
        # this back: `current_round` stayed 1 through every follow-up. It did not show
        # because rounds were opened by a tool that named the ordinal explicitly. The
        # inbound path derives the next ordinal from `current_round`, so a second follow-up
        # would have computed 2 again, collided with the round already there, and quietly
        # overwritten it. A unit test caught it before it was deployed.
        advanced = self._require_review(envelope.review_id)
        if advanced.current_round != payload.round_ordinal:
            self.reviews.put(advanced.model_copy(update={"current_round": payload.round_ordinal}))
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

    def _notify_human(self, review: Review, round_: Round, pending: int, run_id: str) -> None:
        """Tell the compliance owner the round is waiting on them, where they already are.

        A durable pause is only a feature if somebody finds out about it. Until this, a review
        stopped at `awaiting_human` and stayed there until a person happened to open the
        console — which is exactly the "nobody logs into a dashboard to check whether their
        questionnaire is done" problem the phase brief opens with, reproduced inside our own
        product.

        Sent to the watched mailbox itself, not to the customer. That is the compliance
        owner's inbox in this deployment, and mailing the *customer* to say their
        questionnaire needs internal review would be a different and much worse email.

        Never fatal. A round that has legitimately paused must not be failed because a
        notification could not be sent; the pause is durable and the console still shows it.
        """
        link = f"{_console_url()}/reviews/{review.review_id}?view=queue"
        body = "\n".join(
            [
                f"{pending} answer(s) in the {review.customer} review need a person.",
                "",
                "They were held because the evidence is thin, a prior-round commitment may be "
                "contradicted, the guardrail blocked something, or a separate agent could not "
                "find the claim in the passages it cites. Each one says which.",
                "",
                f"  {link}",
                "",
                f"Round {round_.ordinal} · run {run_id}",
                "Nothing is sent to the customer until somebody authorises it by name.",
            ]
        )
        try:
            self.gmail.send_reply(
                thread_id="",
                to=self.gmail.address,
                subject=f"{pending} answer(s) need review — {review.customer}",
                body=body,
            )
            logger.info("approval request sent for %s (%d pending)", review.review_id, pending)
        except Exception as exc:
            logger.warning("could not send the approval request for %s: %s", review.review_id, exc)
            return
        self.audit.append_safe(
            {
                "kind": "approval_requested",
                "review_id": review.review_id,
                "run_id": run_id,
                "actor": "AssemblerAgent",
                "detail": {
                    "pending": pending,
                    "round_id": round_.round_id,
                    "to": self.gmail.address,
                    "link": link,
                },
            }
        )

    # -- the way out --------------------------------------------------------------------

    def deliver_pack(self, envelope: WorkEnvelope) -> HandlerResult:
        """Put the finished pack in Drive and reply to the customer with it attached.

        **The only handler whose effect leaves the system and cannot be taken back.** Every
        other stage writes to Firestore, publishes a message, or calls a model; this one
        sends an email to a person outside the company. That difference shapes all of it.

        ## The gate is structural, not procedural

        `DeliverPackPayload.approved_by` is `Field(min_length=1)`, so the protocol itself
        refuses to carry an unapproved send — the same reasoning that put the citation
        requirement in `Answer`'s validator rather than in a prompt. There is no code path
        in which this handler runs without a named human on the envelope, because there is
        no envelope without one.

        ## Drive first, then the email

        Deliberately ordered. If the upload fails, nothing has been sent and the message is
        retried; if the send fails after the upload, the retry re-uploads to the *same*
        object name and re-sends. The reverse order has a state in which a customer has the
        pack and we have no record of what we sent them, which is the one outcome a
        compliance system may not have.

        ## What it refuses

        A review with no thread — one started from the browser rather than by email — has
        nowhere to reply to, and this says so rather than inventing a recipient. That is a
        `ContractViolation`, which dead-letters: no retry will conjure a thread.
        """
        payload = parse_payload(envelope)
        assert isinstance(payload, DeliverPackPayload)  # noqa: S101 - narrowed by kind

        review = self._require_review(envelope.review_id)
        round_ = self._require_round(envelope)
        thread = self.inbox_state.thread_for_review(review.review_id)
        if not thread or not thread.get("thread_id"):
            raise ContractViolation(
                f"review {review.review_id!r} has no email thread to reply on; it was not "
                "started from the mailbox, so there is no customer to send to",
                review_id=review.review_id,
                run_id=envelope.run_id,
            )

        bundle, workbook, evidence = self._build_pack(review, round_)
        folder = self.drive.folder_for_customer(review.customer)
        stored = []
        for kind, name, data, mime in (
            ("workbook", bundle.filename("xlsx"), workbook, XLSX_MIME),
            ("evidence_pack", bundle.filename("pdf"), evidence, "application/pdf"),
        ):
            if data is None:
                continue
            file = self.drive.upload(name, data, mime, parent=folder)
            self.artifacts.put(
                review.review_id,
                round_.round_id,
                kind,
                file_id=file.file_id,
                name=file.name,
                mime_type=file.mime_type,
                link=file.web_view_link,
                size_bytes=file.size_bytes,
                produced_by=payload.approved_by,
            )
            stored.append(file)

        attachments: list[tuple[str, str, bytes]] = []
        if workbook is not None:
            attachments.append((bundle.filename("xlsx"), XLSX_MIME, workbook))
        attachments.append((bundle.filename("pdf"), "application/pdf", evidence))

        reply_subject = f"Re: security review — {review.customer}"
        reply_body = _covering_note(review, bundle, payload.note)
        sent_id = self.gmail.send_reply(
            thread_id=str(thread["thread_id"]),
            to=str(thread.get("sender") or ""),
            subject=reply_subject,
            body=reply_body,
            attachments=tuple(attachments),
        )

        detail: dict[str, Any] = {
            "round_id": round_.round_id,
            "approved_by": payload.approved_by,
            "thread_id": thread["thread_id"],
            "to": thread.get("sender"),
            # What was actually sent, not a description of it. The same reasoning as the
            # inbound body: this is the other end of the claim that a customer got an
            # answered questionnaire back, and it should be readable rather than asserted.
            "subject": reply_subject,
            "reply_body": reply_body[:INBOUND_BODY_CEILING],
            "attached": [name for name, _mime, _data in attachments],
            "gmail_message_id": sent_id,
            "questions": len(bundle.rows),
            "sendable": bundle.sendable,
            "human_approved": bundle.human_approved,
            "artifacts": [f.as_detail() for f in stored],
        }
        self._audit_stage(envelope, detail)
        # Its own event, in the append-only collection, with the actor being the person and
        # not "Dispatcher". "Who authorised sending this to the customer, and when" is the
        # single most audit-relevant fact this system produces, and it must not be reachable
        # only by parsing a stage record whose actor is a service.
        self.audit.append_safe(
            {
                "kind": "pack_delivered",
                "review_id": review.review_id,
                "run_id": envelope.run_id,
                "actor": payload.approved_by,
                "detail": detail,
            }
        )
        logger.info(
            "pack delivered for %s by %s (thread %s)",
            review.review_id,
            payload.approved_by,
            thread["thread_id"],
        )
        return HandlerResult(published=[], detail=detail)

    def _build_pack(self, review: Review, round_: Round) -> tuple[Any, bytes | None, bytes]:
        """The same two files the export endpoint serves, built here.

        The workbook can legitimately be absent: it needs the customer's own uploaded file,
        and a review whose upload has expired can still produce its evidence pack. `None`
        rather than an empty `bytes`, so a caller cannot attach a zero-length spreadsheet and
        call it a deliverable.
        """
        from attestor_platform.export import build_bundle, build_evidence_pack, fill_workbook
        from attestor_platform.storage.gcs import download_to_temp

        bundle = build_bundle(
            review,
            round_,
            self.questions.for_round(round_.round_id),
            self.answers.for_round(round_.round_id),
            origin=_origin(),
        )
        evidence = build_evidence_pack(bundle)

        workbook: bytes | None = None
        source = self.round_sources.get(round_.round_id)
        if source:
            try:
                workbook = fill_workbook(download_to_temp(source), bundle)
            except Exception as exc:
                logger.warning("could not fill the customer workbook for %s: %s", source, exc)
        return bundle, workbook, evidence

    # -- the front door ----------------------------------------------------------------

    def inbox_message(self, envelope: WorkEnvelope) -> HandlerResult:
        """An email arrived. Decide what it is and, if it is work, start it.

        This is the stage that closes the loop on "runs in the background without being
        asked". Everything downstream has been autonomous since Phase 4; what stood in
        front of it was a person filling in a form. Now a customer emails the watched
        address and a `WorkEnvelope` appears on the same bus the browser would have put
        one on.

        Four outcomes, and each of them is recorded rather than inferred:

        1. **Not a review.** Labelled and left. The mailbox is a real inbox and most of
           what lands in it is not a questionnaire.
        2. **A follow-up on a thread we own.** `open_follow_up`, which loads the prior
           commitments -- so a reply three weeks later is checked against what was promised
           in the first round, with nobody involved.
        3. **A new review.** A `Review`, a round, and `intake_document`.
        4. **Refused.** At capacity, or nothing parseable to work from.

        ## Why the capacity check is here and not only in the control plane

        `guard.require_capacity` protects the *browser* path. This path is reachable by
        anyone who knows an email address, which is a strictly larger set than anyone who
        knows the web URL, and a stranger must not be able to start unbounded 312-question
        runs by sending mail. The ceiling is the same one, read from the same config, and
        being refused is answered with a labelled thread rather than silence.
        """
        payload = parse_payload(envelope)
        assert isinstance(payload, InboxMessagePayload)  # noqa: S101 - narrowed by kind

        message = self.gmail.get_message(payload.gmail_message_id)
        if message.sender and message.sender == self.gmail.address:
            # Our own outbound reply, echoed back into the thread. Answering our own email
            # would open a round per reply, forever.
            return self._inbox_stop(envelope, message, "own_message", "Sent by this mailbox.")

        known_review_id = self.inbox_state.review_for_thread(message.thread_id)
        verdict = self.fleet.classify_inbound(message, known_thread=bool(known_review_id))
        # The message itself, not just its metadata. This is what makes "an email started
        # this, and nobody read it" something a reader can check rather than take on trust.
        body = message.body_text.strip()
        detail: dict[str, Any] = {
            "gmail_message_id": message.message_id,
            "gmail_thread_id": message.thread_id,
            "sender": message.sender,
            "to": message.to,
            "subject": message.subject[:200],
            "received_at": message.received_at.isoformat(),
            "body": body[:INBOUND_BODY_CEILING],
            # Said rather than implied. A body that stops mid-sentence with no note reads as
            # a parsing fault, and this trail's whole value is that it does not do that.
            "body_truncated": len(body) > INBOUND_BODY_CEILING,
            "attachments": [a.filename for a in message.attachments],
            **verdict.as_detail(),
        }

        if not verdict.is_security_review and not known_review_id:
            return self._inbox_stop(envelope, message, "not_a_review", verdict.reason, detail)

        if known_review_id is None:
            active = [
                r.review_id
                for r in self.reviews.list_all(limit=200)
                if r.state not in {ReviewState.DELIVERED, ReviewState.FAILED} and not r.archived
            ]
            if len(active) >= max_active_reviews():
                detail["in_flight"] = sorted(active)
                return self._inbox_stop(
                    envelope,
                    message,
                    "at_capacity",
                    f"{len(active)} reviews are in flight and this deployment allows "
                    f"{max_active_reviews()}.",
                    detail,
                )

        gcs_uri, origin = self._stage_questionnaire(message, verdict.body_questions)
        if gcs_uri is None:
            return self._inbox_stop(
                envelope,
                message,
                "nothing_to_answer",
                "No questionnaire attachment and no questions in the body.",
                detail,
            )
        detail["gcs_uri"] = gcs_uri
        detail["questionnaire_origin"] = origin

        if known_review_id is not None:
            return self._open_round_from_email(envelope, message, known_review_id, gcs_uri, detail)
        return self._create_review_from_email(envelope, message, verdict, gcs_uri, detail)

    def _stage_questionnaire(
        self, message: InboundMessage, body_questions: tuple[str, ...]
    ) -> tuple[str | None, str]:
        """Get this email's questions into GCS, whichever form they arrived in.

        The attachment wins when there is one. A customer who attaches a workbook *and*
        writes two questions in the covering note is asking about the workbook, and the
        export has to hand their own file back -- synthesising a replacement from the note
        would silently substitute a document Attestor wrote for one they sent.
        """
        for attachment in message.questionnaires:
            if attachment.inline_data is not None:
                payload = attachment.inline_data
            elif attachment.attachment_id:
                payload = self.gmail.attachment_bytes(message.message_id, attachment.attachment_id)
            else:  # pragma: no cover - Gmail always gives one or the other
                continue
            if payload:
                return stage_attachment(message, attachment, payload), "attachment"
        if body_questions:
            return stage_body_questions(message, body_questions), "email body"
        return None, "none"

    def _create_review_from_email(
        self,
        envelope: WorkEnvelope,
        message: InboundMessage,
        verdict: Any,
        gcs_uri: str,
        detail: dict[str, Any],
    ) -> HandlerResult:
        """First contact: a review that nobody created by hand."""
        review_id = f"rev-{uuid.uuid4().hex[:12]}"
        review = Review(
            review_id=review_id,
            customer=verdict.customer,
            framework=verdict.framework,
            residency=Residency.US,
            current_round=1,
            state=ReviewState.INTAKE,
            # Carried onto the review rather than left in the inbound audit event. The
            # reviews board shows it on every card, and reading it back out of a
            # thousand-document audit trail per row is not a thing a list page can do.
            deadline=verdict.deadline,
        )
        self.reviews.put(review)
        # Bound before the work is published, not after. A reply that arrives while round
        # one is still drafting has to find this review, and a binding written afterwards
        # is a window in which it would not.
        self.inbox_state.bind_thread(
            message.thread_id, review_id, customer=verdict.customer, sender=message.sender
        )

        round_id = f"{review_id}-r1"
        self.rounds.put(
            Round(round_id=round_id, review_id=review_id, ordinal=1, state=ReviewState.INTAKE)
        )
        self.round_sources.put(round_id, gcs_uri)

        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-intake",
                    review_id=review_id,
                    run_id=envelope.run_id,
                    round_id=round_id,
                    kind=WorkKind.INTAKE_DOCUMENT,
                    payload={
                        "gcs_uri": gcs_uri,
                        "original_filename": gcs_uri.rsplit("/", 1)[-1],
                    },
                )
            )
        ]
        detail.update({"outcome": "review_created", "review_id": review_id, "round_id": round_id})
        self._audit_stage(envelope, detail)
        # Written a second time against the REAL review id. The stage event above is keyed
        # to the synthetic `inbox-...` id, because that is what the envelope carried and the
        # envelope is what the dedup key was derived from; without this line the new review
        # would have an audit trail that begins at intake with no record of where it came
        # from, and "an email started this" is the claim the whole phase rests on.
        self.audit.append_safe(
            {
                "kind": "review_started_by_email",
                "review_id": review_id,
                "run_id": envelope.run_id,
                "actor": "InboxAgent",
                "detail": detail,
            }
        )
        self._label(message, LABEL_STARTED)
        logger.info("email from %s started review %s", message.sender, review_id)
        return HandlerResult(state=ReviewState.INTAKE, published=published, detail=detail)

    def _open_round_from_email(
        self,
        envelope: WorkEnvelope,
        message: InboundMessage,
        review_id: str,
        gcs_uri: str,
        detail: dict[str, Any],
    ) -> HandlerResult:
        """A reply on a thread Attestor owns. Wake the review and open the next round.

        The demonstration this exists for: a review delivered in July, dormant for weeks,
        wakes because an email arrived. `open_follow_up` then loads the commitments made in
        round one from Memory Bank, and the consistency check refuses to contradict them.
        No human is involved at any point in that sentence.
        """
        review = self._require_review(review_id)
        if review.archived:
            return self._inbox_stop(
                envelope,
                message,
                "archived",
                f"Review {review_id} is archived; a reply does not un-archive it.",
                detail,
            )
        ordinal = review.current_round + 1
        round_id = f"{review_id}-r{ordinal}"
        self.round_sources.put(round_id, gcs_uri)
        published = [
            self._emit(
                WorkEnvelope.for_work(
                    message_id=f"{envelope.run_id}-followup-r{ordinal}",
                    review_id=review_id,
                    run_id=envelope.run_id,
                    round_id=round_id,
                    kind=WorkKind.OPEN_FOLLOW_UP,
                    payload={"gcs_uri": gcs_uri, "round_ordinal": ordinal},
                )
            )
        ]
        detail.update(
            {
                "outcome": "follow_up_opened",
                "review_id": review_id,
                "round_id": round_id,
                "ordinal": ordinal,
                "dormant_days": round((_utcnow() - review.created_at).total_seconds() / 86400, 1),
            }
        )
        self._audit_stage(envelope, detail)
        self.audit.append_safe(
            {
                "kind": "follow_up_started_by_email",
                "review_id": review_id,
                "run_id": envelope.run_id,
                "actor": "InboxAgent",
                "detail": detail,
            }
        )
        self._label(message, LABEL_FOLLOW_UP)
        logger.info("email from %s woke review %s for round %d", message.sender, review_id, ordinal)
        return HandlerResult(published=published, detail=detail)

    def _inbox_stop(
        self,
        envelope: WorkEnvelope,
        message: InboundMessage,
        outcome: str,
        reason: str,
        detail: dict[str, Any] | None = None,
    ) -> HandlerResult:
        """Record a decision not to start work, and say why.

        A `HandlerResult` with nothing published, which acks the message: the decision is
        final and redelivering it would re-run the classifier at cost to reach the same
        conclusion. Every one of these is in the audit trail with the reason attached,
        because "the fleet ignored my questionnaire" has to be answerable.
        """
        full = {
            **(detail or {}),
            "outcome": outcome,
            "reason": reason,
            "gmail_message_id": message.message_id,
        }
        self._audit_stage(envelope, full)
        self._label(message, LABEL_IGNORED if outcome == "not_a_review" else LABEL_HELD)
        logger.info("inbound %s: %s (%s)", message.message_id, outcome, reason)
        return HandlerResult(published=[], detail=full)

    def _label(self, message: InboundMessage, label: str) -> None:
        """Mark the thread in the mailbox. Never fatal.

        The label is how a person looking at the inbox can see what the fleet did without
        opening Attestor at all, which is the point of working inside the tools people
        already use. It is also the least important thing on this path, so a mailbox that
        refuses the write must not fail a review that has already started.
        """
        try:
            self.gmail.label_message(message.message_id, add=(self.gmail.ensure_label(label),))
        except Exception as exc:
            logger.warning("could not apply label %r: %s", label, exc)

    def _continue_partition(
        self,
        envelope: WorkEnvelope,
        round_: Round,
        department: Department,
        sequence: int,
        mine: list[Question],
        resumed: list[Question],
        answers: list[Any],
        deferred: list[Question],
    ) -> HandlerResult:
        """The budget expired with work left. Publish the rest and ack this attempt.

        This is the half of ADR-0008 that incremental persistence alone did not solve. With
        answers persisted, an interrupted attempt keeps its work — but it is still *killed*,
        and being killed is what costs a delivery attempt. A partition too large for the 600s
        ack deadline therefore burned its five attempts on being interrupted rather than on
        being finished, and the first deployed run to use the resume ended with 309 of 312
        answers written and no assembly.

        So the attempt ends itself instead. It publishes a fresh `draft_answer` for the same
        partition with a new dedup key, returns normally, and Pub/Sub acks it — a continuation
        rather than a redelivery, which means the attempt counter resets and the round advances
        by however many attempts it needs.

        The partition is deliberately **not** closed. `_close_partition` is what releases
        `assemble_round`, and closing a partition that still has undrafted questions would
        assemble a round from a slice that was never finished — a far worse failure than the
        one this fixes, because it would look like success.

        Raises:
            ContractViolation: if the attempt drafted nothing at all. A continuation that makes
                no progress republishes itself forever, at cost, and the only thing worse than
                a stalled round is a stalled round that keeps paying. Permanent, so it
                dead-letters rather than retrying.
        """
        if not answers:
            raise ContractViolation(
                f"partition {envelope.partition!r} drafted 0 of {len(deferred)} questions "
                "within its budget, so a continuation would make no progress",
                review_id=envelope.review_id,
                run_id=envelope.run_id,
            )

        # The partition string carries the sequence, and that is load-bearing rather than
        # cosmetic. `WorkEnvelope.for_work` derives the dedup key from
        # `(review, round, question, partition, kind)` and deliberately NOT from `message_id`
        # -- ADR-0005, so that a redelivery is recognised as the same work. A continuation
        # published with the same partition would therefore carry the *same* dedup key, be
        # refused by the claim repository as a duplicate, and stall the round permanently.
        # A unit test caught that before it was deployed.
        next_partition = f"{department.value}@{sequence + 1}"
        continuation = self._emit(
            WorkEnvelope.for_work(
                message_id=f"{envelope.run_id}-draft-{next_partition}",
                review_id=envelope.review_id,
                run_id=envelope.run_id,
                round_id=round_.round_id,
                kind=WorkKind.DRAFT_ANSWER,
                partition=next_partition,
            )
        )
        detail = {
            "department": department.value,
            "questions": len(mine),
            "answers": len(answers),
            "resumed_from_previous_attempt": len(resumed),
            "drafted_this_attempt": len(answers),
            "partition_total": len(resumed) + len(answers),
            "deferred_to_next_attempt": len(deferred),
            "continued_as": continuation.dedup_key,
            "cited": sum(1 for a in answers if a.citations),
            **getattr(self.fleet, "last_draft_stats", {}),
        }
        self._audit_stage(envelope, detail)
        logger.info(
            "partition %s hit its budget: %d drafted, %d deferred to %s",
            envelope.partition,
            len(answers),
            len(deferred),
            continuation.dedup_key,
        )
        return HandlerResult(published=[continuation], detail=detail)

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
