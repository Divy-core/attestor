"""Gmail's change notification, turned into work on the bus Attestor already has.

## The shape of the thing

Gmail's `users.watch` publishes to a Pub/Sub topic, and what it publishes is deliberately
uninformative:

    {"emailAddress": "trust@...", "historyId": "994712"}

No message, no subject, no sender — only "this mailbox changed, and it is now at this
point in its history". Turning that into "these three emails arrived" requires the
*previous* history point, which is why `InboxStateRepository` exists and why the cursor is
advanced only after the resulting work has been published.

## Why this is a separate endpoint from `/pubsub/push`

Both are Pub/Sub push deliveries, and the temptation is to fold them together. They are
not the same thing: `/pubsub/push` carries a `WorkEnvelope` we published and therefore
controls, and `parse_push` treats a shape error as permanent because it is. A Gmail
notification is Google's shape, carries no correlation ids, and its "failure" mode is a
history window that has expired — recoverable, and nothing to dead-letter against. Sharing
one endpoint would mean one function that has to guess which contract it is looking at.

So the translation happens here and the *output* is a `WorkEnvelope` on the normal topic.
Everything downstream — the claim, the lease, the dead-letter path, the audit trail — is
the Phase 4 machinery, unchanged. That is the point of B1: an email is a new *source* of
work, not a new pipeline.

## Idempotency across a boundary we do not control

Gmail redelivers, Pub/Sub redelivers, and `history.list` can return a message id twice
across overlapping windows. The envelope's `review_id` is set to `inbox-{gmail message
id}` before any review exists, which makes `make_dedup_key` produce the same key for the
same email every time — so the second arrival is recognised as a redelivery by the same
claim repository that protects every other stage. The synthetic id is replaced by the real
`review_id` the moment the handler creates one, and both are recorded.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from attestor_core.errors import ContractViolation
from attestor_core.protocol import WorkEnvelope, WorkKind

logger = logging.getLogger(__name__)

#: The synthetic review id an inbound message carries until it has a real one. Prefixed so
#: that anything reading the audit trail can tell at a glance that no review existed yet.
INBOX_REVIEW_PREFIX = "inbox-"

#: Message ids pulled from one history delta. A mailbox that received more than this in one
#: window is not a demo mailbox, and publishing an unbounded fan-out from an external
#: trigger is how a credit budget disappears.
MAX_MESSAGES_PER_NOTIFICATION = 25


def synthetic_review_id(gmail_message_id: str) -> str:
    return f"{INBOX_REVIEW_PREFIX}{gmail_message_id}"


@dataclass(frozen=True)
class InboxNotification:
    """One decoded Gmail change notification."""

    email_address: str
    history_id: str
    pubsub_message_id: str


def parse_notification(body: dict[str, Any]) -> InboxNotification:
    """Decode a Gmail push body.

    Raises:
        ContractViolation: If it is not one. Permanent -- the same bytes will decode the
            same way forever -- so the caller acks rather than asking for redelivery.
    """
    message = body.get("message")
    if not isinstance(message, dict):
        raise ContractViolation("gmail push body has no 'message' object")
    raw = message.get("data")
    if not isinstance(raw, str):
        raise ContractViolation("gmail push message carries no base64 'data'")
    try:
        decoded = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"gmail push data is not base64 JSON: {exc}") from exc
    if not isinstance(decoded, dict) or not decoded.get("historyId"):
        raise ContractViolation(f"gmail push data has no historyId: {str(decoded)[:200]}")
    return InboxNotification(
        email_address=str(decoded.get("emailAddress") or ""),
        history_id=str(decoded["historyId"]),
        pubsub_message_id=str(message.get("messageId") or ""),
    )


def envelope_for(gmail_message_id: str, gmail_thread_id: str, history_id: str) -> WorkEnvelope:
    """One inbound email as a unit of work."""
    run_id = f"inbox-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    return WorkEnvelope.for_work(
        message_id=f"{run_id}-{gmail_message_id}",
        review_id=synthetic_review_id(gmail_message_id),
        run_id=run_id,
        kind=WorkKind.INBOX_MESSAGE,
        payload={
            "gmail_message_id": gmail_message_id,
            "gmail_thread_id": gmail_thread_id,
            "history_id": history_id,
        },
    )
