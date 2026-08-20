"""The dispatcher: one Pub/Sub push delivery in, one state transition out.

Everything asynchronous in Attestor lands here. A review is driven from `intake` to
`delivered` by messages through this endpoint and nothing else — no synchronous HTTP call
drives the agent, which is the property Phase 4 exists to establish.

## Ack or nack, and why each

Pub/Sub reads the HTTP status: **2xx acks, anything else nacks and redelivers**. That one
bit decides whether work is lost or repeated forever, so the mapping is a table rather
than scattered `return`s:

| Situation | Why | Status |
|---|---|---|
| Malformed push body | Retrying identical bytes cannot fix a shape error | **200** + DLQ |
| Payload fails its model | Same: permanent | **200** + DLQ |
| `IllegalTransition` | The review is not in a state this work applies to | **200** + DLQ |
| Claim is `DUPLICATE` | The work is already done | **200**, no work |
| Claim is `HELD` | Another worker holds a live lease | **409** → redeliver |
| Handler succeeded | | **204** |
| Handler failed, attempts remain | Transient until proven otherwise | **500** → redeliver |
| Handler failed, attempts exhausted | Stop burning quota | **200** + DLQ |

The two 200s that are not successes matter most. Acking a permanently broken message is
correct — but only because it is dead-lettered *and* audited on the way out. An ack
without that record is how a review silently stops advancing with nothing to point at.

## The guard is called here, not merely present

`claim()` runs before any handler, on every path, in this function. Phase 3 shipped a
recursive-split backstop that was correct, tested, and never called; the fix was found by
reading call sites rather than function bodies. So the idempotency guard has a test that
asserts the *handler* did not run on a duplicate — not one that asserts the claim
repository returned `DUPLICATE`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Request, Response, status

from attestor_core.errors import ContractViolation, IllegalTransition
from attestor_core.protocol import WorkEnvelope
from attestor_platform.firestore import (
    AuditEventRepository,
    ClaimOutcome,
    InboxStateRepository,
    WorkClaimRepository,
)
from attestor_platform.gmail import GmailClient
from attestor_platform.pubsub import WorkPublisher
from dispatcher.deadletter import DeadLetterSink
from dispatcher.handlers import HandlerRegistry, HandlerResult
from dispatcher.inbox import (
    MAX_MESSAGES_PER_NOTIFICATION,
    envelope_for,
    parse_notification,
)
from dispatcher.lease import LeaseKeeper
from dispatcher.push import PushMessage, parse_push

logger = logging.getLogger(__name__)

VERSION = os.environ.get("ATTESTOR_VERSION", "0.1.0")

#: Attempts before a message is dead-lettered by us. Pub/Sub's own dead-letter policy is
#: configured as a backstop at a higher count; this one fires first so the failure is
#: *audited* rather than silently moved by the platform.
MAX_ATTEMPTS = int(os.environ.get("DISPATCHER_MAX_ATTEMPTS", "5"))

#: Identifies which instance holds a claim, so a stale lease can be attributed.
WORKER_ID = os.environ.get("K_REVISION") or os.environ.get("HOSTNAME") or "local"

app = FastAPI(title="Attestor Dispatcher", version=VERSION, docs_url=None, redoc_url=None)

#: Built once per instance. Both hold clients; constructing them per request would open a
#: new Firestore channel on every message.
_claims: WorkClaimRepository | None = None
_handlers: HandlerRegistry | None = None
_deadletter: DeadLetterSink | None = None
_inbox_state: InboxStateRepository | None = None
_gmail: GmailClient | None = None
_publisher: WorkPublisher | None = None
_audit: AuditEventRepository | None = None


def claims() -> WorkClaimRepository:
    global _claims
    if _claims is None:
        _claims = WorkClaimRepository()
    return _claims


def handlers() -> HandlerRegistry:
    global _handlers
    if _handlers is None:
        _handlers = HandlerRegistry()
    return _handlers


def deadletter() -> DeadLetterSink:
    global _deadletter
    if _deadletter is None:
        _deadletter = DeadLetterSink()
    return _deadletter


def inbox_state() -> InboxStateRepository:
    global _inbox_state
    if _inbox_state is None:
        _inbox_state = InboxStateRepository()
    return _inbox_state


def gmail() -> GmailClient:
    global _gmail
    if _gmail is None:
        _gmail = GmailClient()
    return _gmail


def work_publisher() -> WorkPublisher:
    global _publisher
    if _publisher is None:
        _publisher = WorkPublisher()
    return _publisher


def audit() -> AuditEventRepository:
    global _audit
    if _audit is None:
        _audit = AuditEventRepository()
    return _audit


@app.get("/healthz")
@app.get("/health")
def healthz() -> dict[str, str]:
    """Liveness only. Touches no dependency -- see the control plane for why both paths."""
    return {"status": "ok", "version": VERSION, "worker": WORKER_ID}


@app.post("/pubsub/push")
async def push(request: Request, response: Response) -> dict[str, Any]:
    """Handle one push delivery. The status code is the ack decision -- see the table."""
    try:
        body = await request.json()
    except Exception as exc:
        # Not even JSON. Nothing to correlate this with, so it cannot be dead-lettered
        # against a review; log and ack so it is not redelivered forever.
        logger.error("push body is not JSON: %s", exc)
        response.status_code = status.HTTP_200_OK
        return {"result": "discarded", "reason": "body is not JSON"}

    try:
        message = parse_push(body)
    except ContractViolation as exc:
        deadletter().record_unparseable(body, exc)
        response.status_code = status.HTTP_200_OK
        return {"result": "dead_lettered", "reason": str(exc)}

    return _dispatch(message, response)


@app.post("/gmail/push")
async def gmail_push(request: Request, response: Response) -> dict[str, Any]:
    """Gmail said the mailbox changed. Work out what arrived and publish it.

    Thin on purpose. It performs no classification, creates no review, and calls no model:
    it resolves a history delta into message ids and publishes one envelope each. Every
    judgement about what those emails *are* happens in the `inbox_message` handler, under
    the claim, the lease, and the dead-letter path -- so a classification that fails is
    retried and audited like any other stage rather than lost inside an HTTP handler that
    Gmail will not call again.

    Ack decisions follow the same table as `/pubsub/push`: a shape error is permanent and
    acked, a Gmail or Pub/Sub failure is transient and nacked, and the cursor is advanced
    only after the work is on the bus.
    """
    try:
        body = await request.json()
    except Exception as exc:
        logger.error("gmail push body is not JSON: %s", exc)
        response.status_code = status.HTTP_200_OK
        return {"result": "discarded", "reason": "body is not JSON"}

    try:
        notification = parse_notification(body)
    except ContractViolation as exc:
        logger.error("gmail push is not a notification: %s", exc)
        response.status_code = status.HTTP_200_OK
        return {"result": "discarded", "reason": str(exc)}

    cursor = inbox_state().cursor()
    start = str(cursor.get("history_id") or "")
    if not start:
        # No previous point means no delta can be computed -- Gmail's history is relative.
        # Recording this notification's id and stopping is the only correct move, and it is
        # said out loud rather than looking like an empty mailbox.
        inbox_state().advance(notification.history_id)
        logger.warning(
            "no history cursor; adopting %s. Run tools/gmail_watch.py to register a watch.",
            notification.history_id,
        )
        response.status_code = status.HTTP_200_OK
        return {"result": "cursor_adopted", "history_id": notification.history_id}

    try:
        page = gmail().history_since(start, limit=MAX_MESSAGES_PER_NOTIFICATION)
    except Exception as exc:
        logger.warning("gmail history read failed, asking for redelivery: %s", exc)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"result": "retry", "reason": str(exc)}

    if page.restarted:
        # The window expired. Real emails may have been missed, and saying "0 new messages"
        # would be a false statement about a mailbox rather than a true one about a gap.
        audit().append_safe(
            {
                "kind": "inbox_history_gap",
                "review_id": "-",
                "run_id": "-",
                "actor": "Dispatcher",
                "detail": {
                    "requested_from": start,
                    "resumed_at": page.history_id,
                    "reason": "Gmail expired the history window; messages in it are lost.",
                },
            }
        )

    published: list[str] = []
    for gmail_message_id, thread_id in page.messages:
        envelope = envelope_for(gmail_message_id, thread_id, notification.history_id)
        work_publisher().publish(envelope)
        published.append(envelope.dedup_key)

    # After the publish, never before: a crash here redelivers the same delta, and the
    # dedup key makes that a no-op. Advancing first would silently lose the email.
    inbox_state().advance(page.history_id)

    logger.info(
        "gmail notification: %d message(s) published",
        len(published),
        extra={"history_from": start, "history_to": page.history_id},
    )
    response.status_code = status.HTTP_200_OK
    return {
        "result": "ok",
        "messages": len(published),
        "dedup_keys": published,
        "history_from": start,
        "history_to": page.history_id,
        "gap": page.restarted,
    }


def _dispatch(message: PushMessage, response: Response) -> dict[str, Any]:
    """Claim, run, and translate the outcome into an ack decision."""
    envelope = message.envelope
    log_context = {
        "dedup_key": envelope.dedup_key,
        "kind": envelope.kind.value,
        "review_id": envelope.review_id,
        "run_id": envelope.run_id,
        "partition": envelope.partition,
        "attempt": message.attempt,
    }

    # THE GUARD. Before any side effect, on every path.
    claim = claims().claim(
        envelope.dedup_key,
        run_id=envelope.run_id,
        kind=envelope.kind.value,
        review_id=envelope.review_id,
        worker=WORKER_ID,
    )

    if claim.outcome is ClaimOutcome.DUPLICATE:
        logger.info("duplicate delivery acked without work", extra=log_context)
        response.status_code = status.HTTP_200_OK
        return {
            "result": "duplicate",
            "dedup_key": envelope.dedup_key,
            "completed_by_run": claim.completed_by_run,
        }

    if claim.outcome is ClaimOutcome.HELD:
        # Another instance is working on this right now. Not an error and not a
        # duplicate: redeliver, and if that worker dies its lease will lapse.
        logger.info("claim held by a live worker; asking for redelivery", extra=log_context)
        response.status_code = status.HTTP_409_CONFLICT
        return {"result": "held", "dedup_key": envelope.dedup_key}

    try:
        # The lease is pushed forward while the handler works, so a partition that runs
        # long cannot have its claim taken over by a redelivery. See `lease.py` for the
        # measured margins that made this necessary rather than decorative.
        with LeaseKeeper(claims(), envelope.dedup_key) as keeper:
            result: HandlerResult = handlers().run(envelope)
        log_context["lease_heartbeats"] = keeper.heartbeats
    except (ContractViolation, IllegalTransition) as exc:
        # Permanent by construction. A malformed payload or a transition this review
        # cannot make will fail identically on every retry.
        claims().fail(envelope.dedup_key, f"{type(exc).__name__}: {exc}")
        deadletter().record(envelope, exc, attempt=message.attempt, permanent=True)
        logger.error("permanent failure, dead-lettered", extra=log_context, exc_info=True)
        response.status_code = status.HTTP_200_OK
        return {"result": "dead_lettered", "reason": str(exc), "permanent": True}
    except Exception as exc:
        claims().fail(envelope.dedup_key, f"{type(exc).__name__}: {exc}")
        if message.attempt >= MAX_ATTEMPTS:
            deadletter().record(envelope, exc, attempt=message.attempt, permanent=False)
            logger.error("attempts exhausted, dead-lettered", extra=log_context, exc_info=True)
            response.status_code = status.HTTP_200_OK
            return {"result": "dead_lettered", "reason": str(exc), "attempts": message.attempt}

        logger.warning("transient failure, asking for redelivery", extra=log_context)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"result": "retry", "reason": str(exc), "attempt": message.attempt}

    claims().complete(envelope.dedup_key)
    logger.info(
        "work complete",
        extra={**log_context, "published": [e.kind.value for e in result.published]},
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return {
        "result": "ok",
        "dedup_key": envelope.dedup_key,
        "state": result.state.value if result.state else None,
        "published": [e.dedup_key for e in result.published],
    }


def dispatch_envelope(envelope: WorkEnvelope, attempt: int = 1) -> dict[str, Any]:
    """Drive one envelope without HTTP. For the local end-to-end harness and tests.

    Deliberately routes through `_dispatch` rather than duplicating the decision table,
    so the harness exercises the same claim-and-ack logic the endpoint does.
    """
    response = Response()
    outcome = _dispatch(PushMessage(envelope=envelope, pubsub_message_id="local"), response)
    return {**outcome, "status_code": response.status_code}
