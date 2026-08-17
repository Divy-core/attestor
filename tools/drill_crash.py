#!/usr/bin/env python3
"""A worker dies holding a claim. Does the work get picked up, or is it lost forever?

    PROJECT_ID=attestor-505506 uv run python tools/drill_crash.py --write-proof

Phase 4 proved lease takeover in unit tests against fakes. This runs the same three
decisions against the deployed project's **real Firestore**, through the same
`WorkClaimRepository` the dispatcher runs — because a transaction that works against a
fake and not against Firestore is worth nothing, and a conditional `create` is exactly the
sort of thing that differs.

## What is real here and what is not, stated plainly

**Real:** the repository, the transaction, the datastore, and the claim decisions — the
identical code path `_dispatch` takes before any handler runs.

**Not real:** the death, and the delivery. No process is killed: a claim is written
directly, attributed to a worker id that does not exist, with an expiry already in the
past. That is precisely what a killed Cloud Run instance leaves behind — an `in_progress`
claim whose lease stopped being extended — but it is manufactured rather than caused. And
no message is published, so Pub/Sub and the dispatcher's HTTP endpoint are not exercised
here; the ack decisions that sit on top of these outcomes are covered by the unit tests in
`tests/unit/test_dispatcher.py`.

Calling this a "live crash drill" without that paragraph would be the kind of claim this
project has spent three sessions refusing to make.

Both halves are checked, because only the pair means anything:

* a claim with a **live** lease must be refused (`409 HELD`) — otherwise a slow partition
  gets drafted twice, at double the cost, with two sets of writes and nothing reporting an
  error. This is the property the 900s-lease-over-600s-ack-deadline ordering exists for.
* a claim with a **lapsed** lease must be taken over (`RECLAIMED`) — otherwise a single
  instance restart strands a partition permanently.

A drill that only tested the second would pass on a repository that ignored leases
entirely.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from attestor_platform.firestore import ClaimOutcome, WorkClaimRepository
from attestor_platform.firestore.claims import LEASE_SECONDS

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"

DEAD_WORKER = "attestor-dispatcher-00000-crashed"
LIVE_WORKER = "attestor-dispatcher-00000-alive"
TAKEOVER_WORKER = "attestor-dispatcher-99999-takeover"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    from google.cloud import firestore

    db = firestore.Client(project=os.environ["PROJECT_ID"])
    claims = WorkClaimRepository()

    print("=" * 78)
    print("CRASH DRILL -- a claim left behind by a worker that is not coming back")
    print("=" * 78)
    print(f"  lease : {LEASE_SECONDS}s   ack deadline : 600s\n")

    results: dict[str, Any] = {}

    # -- case 1: the lease is still live. The work is genuinely in progress. -----------
    live_key = f"drill-live-{uuid.uuid4().hex[:12]}"
    claims.claim(
        live_key,
        run_id="drill",
        kind="draft_answer",
        review_id="rev-crash-drill",
        worker=LIVE_WORKER,
    )
    held = claims.claim(
        live_key,
        run_id="drill-redelivery",
        kind="draft_answer",
        review_id="rev-crash-drill",
        worker=TAKEOVER_WORKER,
    )
    print(f"  live lease, redelivered  -> {held.outcome.name}")
    results["live_lease"] = {
        "outcome": held.outcome.name,
        "expected": ClaimOutcome.HELD.name,
        "correct": held.outcome is ClaimOutcome.HELD,
    }

    # -- case 2: the worker died. Its lease has lapsed. -------------------------------
    dead_key = f"drill-dead-{uuid.uuid4().hex[:12]}"
    lapsed = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    db.collection("work_claims").document(dead_key).set(
        {
            "state": "in_progress",
            "run_id": "drill",
            "kind": "draft_answer",
            "review_id": "rev-crash-drill",
            "worker": DEAD_WORKER,
            "attempts": 1,
            "claimed_at": (datetime.now(UTC) - timedelta(seconds=1000)).isoformat(),
            # The whole drill turns on this: a lease nobody is extending any more.
            "lease_expires_at": lapsed,
            "completed_at": None,
        }
    )
    reclaimed = claims.claim(
        dead_key,
        run_id="drill-redelivery",
        kind="draft_answer",
        review_id="rev-crash-drill",
        worker=TAKEOVER_WORKER,
    )
    print(f"  lapsed lease, redelivered-> {reclaimed.outcome.name}")

    after = db.collection("work_claims").document(dead_key).get().to_dict() or {}
    print(f"  new owner                : {after.get('worker')}")
    print(f"  attempts                 : {after.get('attempts')}")
    results["lapsed_lease"] = {
        "outcome": reclaimed.outcome.name,
        "expected": ClaimOutcome.RECLAIMED.name,
        "correct": reclaimed.outcome is ClaimOutcome.RECLAIMED,
        "previous_worker": DEAD_WORKER,
        "new_worker": after.get("worker"),
        "attempts": after.get("attempts"),
    }

    # -- case 3: already done. A redelivery must not redo it. -------------------------
    done_key = f"drill-done-{uuid.uuid4().hex[:12]}"
    claims.claim(
        done_key,
        run_id="drill",
        kind="close_round",
        review_id="rev-crash-drill",
        worker=LIVE_WORKER,
    )
    claims.complete(done_key)
    duplicate = claims.claim(
        done_key,
        run_id="drill-redelivery",
        kind="close_round",
        review_id="rev-crash-drill",
        worker=TAKEOVER_WORKER,
    )
    print(f"  completed, redelivered   -> {duplicate.outcome.name}")
    results["completed"] = {
        "outcome": duplicate.outcome.name,
        "expected": ClaimOutcome.DUPLICATE.name,
        "correct": duplicate.outcome is ClaimOutcome.DUPLICATE,
    }

    passed = all(case["correct"] for case in results.values())
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")

    report = {
        "case": "crash_and_lease_takeover_drill",
        "pass": passed,
        "against": "the deployed project's Firestore, via the real WorkClaimRepository",
        "real": "the repository, the transaction, the datastore, the claim decisions",
        "not_real": (
            "no process was killed and no message was published; the abandoned claim is "
            "manufactured, and the ack decisions above it are covered by unit tests"
        ),
        "lease_seconds": LEASE_SECONDS,
        "ack_deadline_seconds": 600,
        "cases": results,
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "drill-crash.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
