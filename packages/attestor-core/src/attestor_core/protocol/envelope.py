"""Pub/Sub message envelope.

Every unit of asynchronous work travels in one of these. The `dedup_key` is what makes
the Phase 4 dispatcher idempotent under Pub/Sub's at-least-once delivery guarantee, so
it is **deterministic from the work being requested** -- never random, never a uuid4.
Two publishes of the same work produce the same key, and the dispatcher recognises the
second as a redelivery rather than new work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from attestor_core.domain.ids import make_dedup_key
from attestor_core.errors import ContractViolation

ContentId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class WorkKind(StrEnum):
    """What a worker is being asked to do.

    Kept coarse on purpose: one kind per resumable step of the state machine, so the
    dispatcher's dispatch table stays a flat mapping rather than a nested decision tree.
    """

    INTAKE_DOCUMENT = "intake_document"
    TRIAGE_QUESTIONS = "triage_questions"
    DRAFT_ANSWER = "draft_answer"
    GATHER_EVIDENCE = "gather_evidence"
    ASSEMBLE_ROUND = "assemble_round"
    CLOSE_ROUND = "close_round"
    OPEN_FOLLOW_UP = "open_follow_up"
    #: A human answered an approval request; resume the paused run.
    RESUME_AFTER_HUMAN = "resume_after_human"
    #: SLA / follow-up timer fired.
    TIMER_FIRED = "timer_fired"
    #: An email landed in the watched mailbox. ADDED IN PHASE 7 (ADR-0009).
    INBOX_MESSAGE = "inbox_message"
    #: A human approved sending the finished pack back to the customer. ADR-0009.
    DELIVER_PACK = "deliver_pack"


# ---------------------------------------------------------------------------------
# Payload models
#
# The envelope already carries review_id, round_id, and question_id, so most payloads
# are empty: `draft_answer`, `triage_questions`, `assemble_round`, and `close_round`
# need nothing beyond what the envelope holds. A nine-variant discriminated union to
# carry roughly four fields would be over-engineering.
#
# But an open dict means a malformed publish surfaces as a KeyError deep inside a
# worker rather than at construction. These models close that: `for_work()` validates
# at publish time, `parse_payload()` validates at consume time, and the wire format
# stays a plain dict so adding a field never forces a protocol change.
# ---------------------------------------------------------------------------------


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EmptyPayload(_Payload):
    """For work fully described by the envelope's own correlation fields."""


class IntakeDocumentPayload(_Payload):
    """Parse an uploaded questionnaire."""

    gcs_uri: str
    content_type: str | None = None
    original_filename: str | None = None


class OpenFollowUpPayload(_Payload):
    """Round N>1 has arrived."""

    gcs_uri: str
    #: Ordinal of the round being opened. Round 1 is the initial questionnaire.
    round_ordinal: int = Field(ge=2)


class ResumeAfterHumanPayload(_Payload):
    """A human answered an approval request; resume the paused run."""

    approved: bool
    resolved_by: str
    #: Present when the human edited the text before approving.
    edited_text: str | None = None


class InboxMessagePayload(_Payload):
    """One message from the watched mailbox, to be classified and acted on.

    Deliberately carries **ids only, never content**. The message body is attacker-
    controlled and can be megabytes; putting it on the bus would mean an untrusted payload
    replayed on every redelivery and a Pub/Sub message that can exceed the 10MB limit
    because someone pasted a spreadsheet inline. The handler fetches the message from
    Gmail, which is also the only way a redelivery sees the message as it is *now*.
    """

    gmail_message_id: str = Field(min_length=1)
    gmail_thread_id: str = Field(min_length=1)
    #: The mailbox history point this was discovered at. Diagnostic; the handler does not
    #: need it to do its work.
    history_id: str = ""


class DeliverPackPayload(_Payload):
    """Send the completed pack back to the customer, in the thread it arrived on.

    The only work kind in the protocol whose effect leaves the system irreversibly, which
    is why it exists as its own kind rather than as a branch of `close_round`. A human
    approved this specific act, `approved_by` names them, and a kind that cannot be
    published without that field is a structural gate rather than a policy sentence.
    """

    approved_by: str = Field(min_length=1)
    #: What the human was shown when they approved. Recorded so the audit trail holds the
    #: decision and its basis, not just the outcome.
    note: str = ""


class TimerFiredPayload(_Payload):
    """An SLA or follow-up timer elapsed."""

    #: e.g. "sla_breach", "follow_up_due", "round_stale".
    timer_kind: str
    scheduled_for: datetime


#: Which model validates each kind's payload. Anything absent uses EmptyPayload, which
#: forbids extras -- so publishing junk on a no-payload kind fails loudly.
PAYLOAD_MODELS: dict[WorkKind, type[BaseModel]] = {
    WorkKind.INTAKE_DOCUMENT: IntakeDocumentPayload,
    WorkKind.TRIAGE_QUESTIONS: EmptyPayload,
    WorkKind.DRAFT_ANSWER: EmptyPayload,
    WorkKind.GATHER_EVIDENCE: EmptyPayload,
    WorkKind.ASSEMBLE_ROUND: EmptyPayload,
    WorkKind.CLOSE_ROUND: EmptyPayload,
    WorkKind.OPEN_FOLLOW_UP: OpenFollowUpPayload,
    WorkKind.RESUME_AFTER_HUMAN: ResumeAfterHumanPayload,
    WorkKind.TIMER_FIRED: TimerFiredPayload,
    WorkKind.INBOX_MESSAGE: InboxMessagePayload,
    WorkKind.DELIVER_PACK: DeliverPackPayload,
}


class WorkEnvelope(BaseModel):
    """The wire contract for a unit of asynchronous work.

    FROZEN after Phase 1, amended in Phase 4 (`partition`, ADR-0005) and again in Phase 7
    (two new kinds, ADR-0009), re-frozen each time. Both the dispatcher and the control
    plane depend on it.

    The Phase 7 amendment adds `kind` values and their payload models and changes no
    existing field, so a producer on an older revision is unaffected and a consumer on an
    older revision rejects the new kinds with `ContractViolation` -- loudly, at the edge,
    which is the failure mode a frozen protocol is supposed to have.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Our id for the message. Distinct from Pub/Sub's own message id, which we do not
    #: control and which changes on redelivery.
    message_id: str
    #: Deterministic identity of the *work*. Redeliveries share it; genuinely new work
    #: does not. Built by `for_work` below.
    dedup_key: ContentId
    #: Delivery attempt, 1-based. Carried so the handler can decide when to give up
    #: rather than relying on Pub/Sub's own retry metadata.
    attempt: int = Field(default=1, ge=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    review_id: str
    #: Correlates every log line, span, and audit event of one execution.
    run_id: str
    round_id: str | None = None
    question_id: ContentId | None = None
    #: Which slice of a stage this message covers, when a stage is split across several
    #: messages that share every other correlation field. Department for `draft_answer`,
    #: batch index for `triage_questions`, wave number for a retry.
    #:
    #: ADDED IN PHASE 4 (ADR-0005), and it fixes a real bug rather than adding a feature.
    #: Drafting is partitioned by department, so three messages of one round share
    #: `review_id`, `round_id`, a null `question_id`, and `kind` -- and therefore collided
    #: on one dedup key. The dispatcher would have acked two of the three partitions as
    #: redeliveries and silently dropped two thirds of the drafting work, which is
    #: precisely the failure idempotency exists to prevent, caused by idempotency.
    partition: str | None = None

    kind: WorkKind
    #: Kind-specific data. Deliberately open: constraining it here would force a
    #: protocol change for every new field a worker needs.
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def for_work(
        cls,
        *,
        message_id: str,
        review_id: str,
        run_id: str,
        kind: WorkKind,
        round_id: str | None = None,
        question_id: str | None = None,
        partition: str | None = None,
        payload: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> WorkEnvelope:
        """Build an envelope with a dedup key derived from the work itself.

        The key deliberately excludes ``run_id``, ``message_id``, ``attempt``, and
        ``occurred_at``. Including any of them would make every retry look like new
        work, which is exactly the bug idempotency exists to prevent.

        It deliberately *includes* ``partition``, because excluding it caused the
        opposite bug: three department partitions of one round produced one key, and two
        of the three would have been acked as redeliveries. See ADR-0005.
        """
        dedup_key = make_dedup_key(
            review_id,
            round_id or "-",
            question_id or "-",
            partition or "-",
            kind.value,
        )
        # Validate the payload here so a malformed publish fails at the call site, with
        # a pydantic error naming the field, rather than as a KeyError inside a worker
        # three services away and possibly days later.
        model = PAYLOAD_MODELS.get(kind, EmptyPayload)
        try:
            model.model_validate(payload or {})
        except ValidationError as exc:
            raise ContractViolation(
                f"payload does not match {model.__name__} for kind {kind.value!r}",
                review_id=review_id,
                round_id=round_id,
                question_id=question_id,
                run_id=run_id,
                errors=exc.errors(),
            ) from exc
        return cls(
            message_id=message_id,
            dedup_key=dedup_key,
            attempt=attempt,
            review_id=review_id,
            run_id=run_id,
            round_id=round_id,
            question_id=question_id,
            partition=partition,
            kind=kind,
            payload=payload or {},
        )


def parse_payload(envelope: WorkEnvelope) -> BaseModel:
    """Validate and return an envelope's payload as its typed model.

    Called by the Phase 4 dispatcher at consume time. The wire format stays a plain
    dict, so a producer on an older revision that omits a newly-added optional field
    still parses -- but a genuinely malformed message is rejected here, at the edge,
    instead of failing somewhere inside the handler.

    Raises:
        ContractViolation: If the payload does not match the model for this kind.
    """
    model = PAYLOAD_MODELS.get(envelope.kind, EmptyPayload)
    try:
        return model.model_validate(envelope.payload)
    except ValidationError as exc:
        raise ContractViolation(
            f"payload does not match {model.__name__} for kind {envelope.kind.value!r}",
            review_id=envelope.review_id,
            round_id=envelope.round_id,
            question_id=envelope.question_id,
            run_id=envelope.run_id,
            message_id=envelope.message_id,
            errors=exc.errors(),
        ) from exc
