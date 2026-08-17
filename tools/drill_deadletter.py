#!/usr/bin/env python3
"""Force a message into the dead-letter path, live, and read it back out.

    PROJECT_ID=attestor-505506 uv run python tools/drill_deadletter.py --write-proof

Phase 4 proved this in unit tests against the real decision table. What it did not prove
is that the *deployed* wiring works — that a permanently broken message published to the
real topic reaches the Cloud Run dispatcher, is refused, is recorded, and can be found
afterwards by someone asking "what happened to that review?".

The message used is an envelope naming a review that does not exist. That produces a
`ContractViolation`, which the dispatcher classifies as **permanent**: retrying identical
bytes cannot make a missing review appear, so it acks with 200 rather than nacking, and
dead-letters it on the way out. The 200 is the interesting part — acking a broken message
is only correct because it is dead-lettered *and* audited, and this drill checks both
rather than checking that the message stopped being redelivered.

## Two dead-letter paths, and why both exist

* **Ours**, at `DISPATCHER_MAX_ATTEMPTS`, which writes a `work_dead_lettered` audit event
  and republishes the envelope to `attestor.deadletter`. This is the one that leaves a
  record a compliance reader can query.
* **Pub/Sub's**, at `maxDeliveryAttempts: 5` on the subscription, as a backstop for the
  case where the dispatcher is so broken it cannot dead-letter anything itself.

Ours fires first, deliberately. Until session three the dead-letter topic had **no
subscription**, so anything the platform moved there was discarded on arrival — which is
why session two's stalled run had nothing to inspect. `infra/deploy.sh` now creates
`attestor.deadletter.sub`, and this drill reads from it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import pubsub_v1  # type: ignore[attr-defined]

from attestor_core.protocol import WorkEnvelope, WorkKind
from attestor_platform.firestore import AuditEventRepository
from attestor_platform.pubsub import WorkPublisher

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"

DLQ_SUBSCRIPTION = "attestor.deadletter.sub"
WAIT_SECONDS = 180


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("error: PROJECT_ID must be set")

    audit = AuditEventRepository()
    subscriber = pubsub_v1.SubscriberClient()
    dlq_path = subscriber.subscription_path(project, DLQ_SUBSCRIPTION)

    # A review id that is certain not to exist. Deliberately not a malformed body: a body
    # that fails to parse never reaches a handler, and this drill is about a message that
    # is well-formed and still impossible to act on -- the harder of the two.
    review_id = f"rev-does-not-exist-{uuid.uuid4().hex[:8]}"
    run_id = f"dlq-drill-{int(time.time())}"

    envelope = WorkEnvelope.for_work(
        message_id=f"{run_id}-poison",
        review_id=review_id,
        run_id=run_id,
        round_id=f"{review_id}-r1",
        kind=WorkKind.TRIAGE_QUESTIONS,
    )

    print("=" * 78)
    print("DEAD-LETTER DRILL -- a permanently broken message, on the deployed stack")
    print("=" * 78)
    print(f"  review      : {review_id}  (does not exist)")
    print(f"  kind        : {envelope.kind.value}")
    print(f"  dedup key   : {envelope.dedup_key}\n")

    # Drain anything already sitting in the DLQ, so what is found afterwards is this
    # drill's message and not a souvenir from an earlier failure.
    drained = _drain(subscriber, dlq_path)
    if drained:
        print(f"  drained {len(drained)} pre-existing dead letter(s) before starting\n")

    WorkPublisher().publish(envelope)
    print("  published to attestor.work; the dispatcher on Cloud Run has it now\n")

    audit_event: dict[str, Any] | None = None
    dlq_message: dict[str, Any] | None = None
    deadline = time.perf_counter() + WAIT_SECONDS

    while time.perf_counter() < deadline and not (audit_event and dlq_message):
        if audit_event is None:
            for event in audit.for_review(review_id, limit=50):
                if event.get("kind") == "work_dead_lettered":
                    audit_event = dict(event)
                    print(f"  {time.perf_counter():.0f}  audit event found: work_dead_lettered")
                    break
        if dlq_message is None:
            for message in _drain(subscriber, dlq_path):
                if message.get("review_id") == review_id:
                    dlq_message = message
                    print("       dead letter found on attestor.deadletter.sub")
                    break
        if audit_event and dlq_message:
            break
        time.sleep(5)

    detail = (audit_event or {}).get("detail") or {}
    print(f"\n  audit event written  : {audit_event is not None}")
    if audit_event:
        print(f"    permanent          : {detail.get('permanent')}")
        print(f"    error_type         : {detail.get('error_type')}")
        print(f"    error              : {str(detail.get('error'))[:120]}")
        print(f"    attempt            : {detail.get('attempt')}")
    print(f"  dead letter readable : {dlq_message is not None}")

    passed = audit_event is not None and dlq_message is not None
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")
    if audit_event and not dlq_message:
        print("  (the failure was audited but the message is not findable in the DLQ --")
        print("   which is the exact gap that made session two's stall uninvestigable)")

    report = {
        "case": "dead_letter_drill",
        "pass": passed,
        "live": True,
        "review_id": review_id,
        "dedup_key": envelope.dedup_key,
        "audit_event": audit_event,
        "dead_letter_message": dlq_message,
        "dlq_subscription": DLQ_SUBSCRIPTION,
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "drill-deadletter.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


def _drain(subscriber: Any, path: str) -> list[dict[str, Any]]:
    """Pull and ack whatever is on the dead-letter subscription right now.

    An empty unary pull can raise `DeadlineExceeded` rather than returning an empty
    response -- found while diagnosing session two's stall, and treated as "nothing
    there" rather than as an error.
    """
    try:
        response = subscriber.pull(
            request={"subscription": path, "max_messages": 20}, timeout=15
        )
    except gexc.DeadlineExceeded:
        return []

    found: list[dict[str, Any]] = []
    ack_ids: list[str] = []
    for received in response.received_messages:
        ack_ids.append(received.ack_id)
        try:
            found.append(json.loads(received.message.data.decode("utf-8")))
        except Exception:
            found.append({"raw": received.message.data[:400].decode("utf-8", "replace")})
    if ack_ids:
        subscriber.acknowledge(request={"subscription": path, "ack_ids": ack_ids})
    return found


if __name__ == "__main__":
    raise SystemExit(main())
