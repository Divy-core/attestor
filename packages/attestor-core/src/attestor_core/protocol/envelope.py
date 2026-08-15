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
}


class WorkEnvelope(BaseModel):
    """The wire contract for a unit of asynchronous work.

    FROZEN after Phase 1. Both the dispatcher and the control plane depend on it.
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
        payload: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> WorkEnvelope:
        """Build an envelope with a dedup key derived from the work itself.

        The key deliberately excludes ``run_id``, ``message_id``, ``attempt``, and
        ``occurred_at``. Including any of them would make every retry look like new
        work, which is exactly the bug idempotency exists to prevent.
        """
        dedup_key = make_dedup_key(
            review_id,
            round_id or "-",
            question_id or "-",
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
