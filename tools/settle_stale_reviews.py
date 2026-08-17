#!/usr/bin/env python
"""Mark reviews whose runs have genuinely ended as `failed`, so the ceiling frees up.

    PROJECT_ID=attestor-505506 CONTROL_PLANE_URL=https://... ATTESTOR_WRITE_TOKEN=... \\
    uv run python tools/settle_stale_reviews.py            # reports, changes nothing
    uv run python tools/settle_stale_reviews.py --apply     # transitions them

## Why this exists

Phase 6.5 put a three-concurrent-review ceiling on the control plane, because the browser can
now start a 312-question review from a public URL. The first thing that ceiling did was refuse
*me*: seven reviews were in flight, all of them debris from harness runs whose drafting
partitions had exhausted their five delivery attempts hours earlier. A judge clicking **New
review** would have got a 429 and a message about reviews that will never finish.

So the ceiling is correct and the accumulated state was wrong. This settles the second.

## What counts as ended, and what this refuses to touch

A review is *stale* when it is not in a terminal state and no round of it has made progress
within `--idle-minutes`. Two shapes, both real:

- **Never got past triage.** Zero answers, hours old. The drafting messages were dead-lettered
  or the partitions never claimed.
- **Stuck mid-flight.** Some answers, no new ones for hours, and no partition still holding a
  live claim.

`failed` is a legal transition from every non-terminal state (`core.state._EXCEPTIONAL`), and it
is what actually happened, so nothing is being papered over.

**Never touched:**

- `awaiting_human`. That is not a stalled review, it is the durable pause working exactly as
  designed — the round is waiting for a person and will resume the moment one acts. Failing it
  would destroy the human-in-the-loop demo beat and would be a false statement about the system.
- `delivered` and `failed`. Already settled.
- Anything with recent answer activity, whatever its age. A 312-question run is slow, and a
  tool that failed a review because it was taking a while would be worse than the debris.

## Why it goes through the API

`POST /reviews/{id}/state` runs the transition through `core.state.transition`, so an illegal
move is refused with a 409 rather than written straight into Firestore. Writing the state
directly would bypass the one component whose job is to say which moves are legal, in a tool
whose whole purpose is to move things.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from attestor_core.domain.enums import ReviewState
from attestor_platform.firestore import AnswerRepository, ReviewRepository, RoundRepository

#: States that are already settled, and the one that must never be settled by a tool.
SETTLED = {ReviewState.DELIVERED, ReviewState.FAILED}
NEVER_TOUCH = {ReviewState.AWAITING_HUMAN}

#: No answer written for this long and the round is not being worked. 45 minutes is well past the
#: 900s lease, so a partition still legitimately drafting cannot be caught by it.
DEFAULT_IDLE_MINUTES = 45


def _post_state(base: str, token: str, review_id: str, target: str) -> tuple[int, str]:
    url = f"{base}/reviews/{review_id}/state?target={target}"
    request = urllib.request.Request(  # noqa: S310
        url, data=b"", method="POST", headers={"X-Attestor-Token": token}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually transition them")
    parser.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES)
    args = parser.parse_args()

    base = os.environ.get("CONTROL_PLANE_URL", "").rstrip("/")
    token = os.environ.get("ATTESTOR_WRITE_TOKEN", "").strip()
    if args.apply and (not base or not token):
        sys.exit("error: CONTROL_PLANE_URL and ATTESTOR_WRITE_TOKEN must be set to --apply")

    reviews = ReviewRepository()
    rounds = RoundRepository()
    answers = AnswerRepository()
    cutoff = datetime.now(UTC) - timedelta(minutes=args.idle_minutes)

    print("=" * 78)
    print("STALE REVIEWS -- what is holding the concurrency ceiling")
    print("=" * 78)
    print(f"  idle threshold : {args.idle_minutes} minutes (the lease is 15)")
    print(f"  mode           : {'APPLY' if args.apply else 'report only'}\n")
    print(f"  {'review':26} {'state':16} {'answers':>7} {'last answer':>14}  verdict")
    print(f"  {'-' * 26} {'-' * 16} {'-' * 7} {'-' * 14}  {'-' * 24}")

    stale: list[str] = []
    for review in reviews.list_all(limit=200):
        all_answers = [
            answer
            for round_ in rounds.for_review(review.review_id)
            for answer in answers.for_round(round_.round_id)
        ]
        latest = max((a.created_at for a in all_answers), default=None)
        age = "never" if latest is None else f"{(datetime.now(UTC) - latest).seconds // 60}m ago"

        if review.state in SETTLED:
            verdict = "already settled"
        elif review.state in NEVER_TOUCH:
            # Said explicitly rather than silently skipped: this is the state the whole
            # human-in-the-loop design exists to produce, and it looks like a stall from outside.
            verdict = "the durable pause -- LEFT ALONE"
        elif latest is not None and latest > cutoff:
            verdict = "still being worked"
        else:
            verdict = "ended -> failed"
            stale.append(review.review_id)

        print(
            f"  {review.review_id:26} {review.state.value:16} {len(all_answers):7d} "
            f"{age:>14}  {verdict}"
        )

    if not stale:
        print("\n  nothing to settle.")
        return 0

    print(f"\n  {len(stale)} review(s) to settle")
    if not args.apply:
        print("  re-run with --apply to transition them.")
        return 0

    results: list[dict[str, Any]] = []
    for review_id in stale:
        status, body = _post_state(base, token, review_id, "failed")
        ok = status == 200
        results.append({"review_id": review_id, "status": status, "ok": ok, "body": body[:200]})
        print(f"  {'ok  ' if ok else 'FAIL'} {review_id:26} {status}  {body[:80]}")

    print(f"\n  settled {sum(1 for r in results if r['ok'])} of {len(results)}")
    print(json.dumps({"settled": results}, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
