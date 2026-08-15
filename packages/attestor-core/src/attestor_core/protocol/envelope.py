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

from pydantic import BaseModel, ConfigDict, Field

from attestor_core.domain.ids import make_dedup_key

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
