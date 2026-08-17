#!/usr/bin/env python3
"""A human approves one flagged answer, and the paused review carries on.

    PROJECT_ID=attestor-505506 CONTROL_PLANE_URL=https://... \\
        uv run python tools/drill_approval.py --review rev-deployed-xxxx --write-proof

Run this against a review the deployed stack has already parked in `awaiting_human`.
Nothing here fabricates that state: a review reaches it because `assemble_round` found at
least one answer the confidence rules would not ship unattended, which is the decision
`requires_human` makes and the reason the whole pause exists.

What is being checked is that the pause is **durable rather than a held connection**. The
run stopped. No process is waiting, no HTTP request is open, no timer is counting. The
review sits in Firestore in `awaiting_human` and stays there — for a minute or for three
weeks — until a person acts. Then a single POST to the control plane publishes
`resume_after_human`, the dispatcher applies the decision, and the round finishes.

The approval endpoint deliberately does not apply the decision itself; it publishes. So a
resume behaves identically whether it came from a human clicking approve or from Pub/Sub
redelivering that message an hour later, which is what makes the approval idempotent
rather than merely usually-fine.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from attestor_platform.firestore import AnswerRepository, AuditEventRepository, ReviewRepository

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"

WAIT_SECONDS = 600


def post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: {exc.code} from {url}: {exc.read().decode('utf-8', 'replace')[:400]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True)
    parser.add_argument("--round", default=None, help="defaults to <review>-r1")
    parser.add_argument("--resolved-by", default="divy@kestreldata.example")
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    base = os.environ.get("CONTROL_PLANE_URL")
    if not base or not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID and CONTROL_PLANE_URL must be set")
    base = base.rstrip("/")

    reviews = ReviewRepository()
    answers_repo = AnswerRepository()
    audit = AuditEventRepository()

    round_id = args.round or f"{args.review}-r1"
    review = reviews.get(args.review)
    if review is None:
        sys.exit(f"error: {args.review} not found")

    pending = [a for a in answers_repo.for_round(round_id) if a.status.value == "needs_human"]

    print("=" * 78)
    print("APPROVAL DRILL -- a durable pause, resumed by one HTTP call")
    print("=" * 78)
    print(f"  review        : {args.review}")
    print(f"  state         : {review.state.value}")
    print(f"  control plane : {base}")
    print(f"  awaiting a human: {len(pending)} answer(s)\n")

    if review.state.value != "awaiting_human":
        print("  This review is not paused. The drill needs one that is -- run a review")
        print("  whose answers include at least one the confidence rules will not ship.")
        return 1
    if not pending:
        print("  Paused with nothing pending: that is a bug, not a drill result.")
        return 1

    started = time.perf_counter()
    approved: list[dict[str, Any]] = []
    for answer in pending:
        result = post(
            f"{base}/rounds/{round_id}/answers/{answer.question_id}/approval",
            {"approved": True, "resolved_by": args.resolved_by, "edited_text": None},
        )
        approved.append({"question_id": answer.question_id, **result})
        print(f"  approved {answer.question_id}  -> run {result.get('run_id')}")

    print("\n  waiting for the dispatcher to resume the round\n")
    state = review.state.value
    while time.perf_counter() - started < WAIT_SECONDS:
        current = reviews.get(args.review)
        state = current.state.value if current else state
        if state in {"delivered", "assembling"}:
            break
        time.sleep(5)

    wall = time.perf_counter() - started
    still_pending = [a for a in answers_repo.for_round(round_id) if a.status.value == "needs_human"]
    final = [a for a in answers_repo.for_round(round_id) if a.status.value == "approved"]
    decisions = [
        e for e in audit.for_review(args.review, limit=1000) if e.get("kind") == "human_decision"
    ]

    print(f"  final state          : {state}")
    print(f"  approved answers     : {len(final)}")
    print(f"  still awaiting human : {len(still_pending)}")
    print(f"  human_decision events: {len(decisions)}")
    print(f"  resume wall clock    : {wall:.1f}s")

    passed = state == "delivered" and not still_pending and len(final) >= len(pending)
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")

    report = {
        "case": "human_approval_resume",
        "pass": passed,
        "review_id": args.review,
        "round_id": round_id,
        "control_plane": base,
        "paused_answers": len(pending),
        "approvals_posted": approved,
        "approved_answers": len(final),
        "still_awaiting_human": len(still_pending),
        "human_decision_events": len(decisions),
        "final_state": state,
        "resume_seconds": round(wall, 1),
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "drill-approval.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
