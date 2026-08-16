"""Parsing an Eventarc / Pub/Sub push request into a `WorkEnvelope`.

Kept separate from the endpoint and free of FastAPI, because this is the layer where a
malformed message must be distinguished from a *transient* failure — and getting that
wrong is expensive in both directions. Nacking a permanently malformed message retries it
until the subscription's expiry, burning quota on something that will never succeed;
acking a transient failure discards real work.

The push body shape is Google's, not ours:

    {"message": {"data": "<base64>", "attributes": {...}, "messageId": "...",
                 "publishTime": "..."},
     "subscription": "projects/p/subscriptions/s"}
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from attestor_core.errors import ContractViolation
from attestor_core.protocol import WorkEnvelope


@dataclass(frozen=True)
class PushMessage:
    """One decoded push delivery."""

    envelope: WorkEnvelope
    #: Pub/Sub's own message id. Changes on redelivery, which is exactly why it is not
    #: the idempotency key -- `envelope.dedup_key` is.
    pubsub_message_id: str
    #: Pub/Sub's delivery attempt, when the subscription has a dead-letter policy
    #: configured. Absent otherwise, in which case `envelope.attempt` is all we have.
    delivery_attempt: int | None = None

    @property
    def attempt(self) -> int:
        """The attempt number to make retry decisions on.

        Prefers Pub/Sub's count, because ours is only as accurate as the publisher that
        set it, and a redelivery of the *same* published message never increments ours.
        """
        return self.delivery_attempt or self.envelope.attempt


def parse_push(body: dict[str, Any]) -> PushMessage:
    """Decode a push request body.

    Raises:
        ContractViolation: If the body is not a valid push delivery of a `WorkEnvelope`.
            Every raise from here is permanent -- retrying identical bytes cannot fix a
            shape error -- so the caller dead-letters rather than nacking.
    """
    message = body.get("message")
    if not isinstance(message, dict):
        raise ContractViolation(
            "push body has no 'message' object",
            subscription=body.get("subscription"),
        )

    raw = message.get("data")
    if not isinstance(raw, str):
        raise ContractViolation(
            "push message carries no base64 'data'",
            pubsub_message_id=message.get("messageId"),
        )

    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContractViolation(
            f"push message data is not valid base64: {exc}",
            pubsub_message_id=message.get("messageId"),
        ) from exc

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractViolation(
            f"push message data is not JSON: {exc}",
            pubsub_message_id=message.get("messageId"),
        ) from exc

    try:
        envelope = WorkEnvelope.model_validate(payload)
    except Exception as exc:
        raise ContractViolation(
            f"push message is not a WorkEnvelope: {exc}",
            pubsub_message_id=message.get("messageId"),
        ) from exc

    attempt = body.get("deliveryAttempt")
    return PushMessage(
        envelope=envelope,
        pubsub_message_id=str(message.get("messageId") or ""),
        delivery_attempt=int(attempt) if isinstance(attempt, int) else None,
    )
