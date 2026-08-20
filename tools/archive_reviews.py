#!/usr/bin/env python
"""Take dead runs out of the working set without deleting them.

    CONTROL_PLANE_URL=https://... ATTESTOR_WRITE_TOKEN=... \
    uv run python tools/archive_reviews.py                  # reports, changes nothing
    uv run python tools/archive_reviews.py --apply          # archives every `failed` review
    uv run python tools/archive_reviews.py --apply --restore rev-...   # put one back

## Why this exists

Thirteen reviews are live and seven of them say `failed` -- debris from the Phase 6.5 quota
work, every one of which ran out of delivery attempts hours before anyone looked. The first
thing a judge sees on the landing page is therefore a list that is majority failure, and the
honest reading of that page is "this system does not work". The runs are real and the state
word is true; what is wrong is that they are still in the working set.

## Archiving is not deleting and it is not a state change

`docs/proof/` references several of these reviews by id, and the measured record is the point
of this repository -- deleting them would break artefacts that exist to be checked. `failed`
also stays true: it is a terminal state and archiving does not move it, because there is no
legal transition out of `failed` and there should not be one. Archived is an assertion about
*attention*, which is a different axis, so it is a flag rather than a state.

## What it refuses to touch

Anything not in `--state`, which defaults to `failed` alone. In particular `awaiting_human` is
never selected by default: that is the durable pause working, and hiding it would hide the
human-in-the-loop beat. Pass `--review` to archive something specific and say so out loud.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from attestor_platform.firestore import ReviewRepository

DEFAULT_REASON = (
    "Dead run from the Phase 6.5 quota work: the drafting partitions exhausted their "
    "delivery attempts. Kept as history because docs/proof/ references it."
)


def _post_archive(
    base: str, token: str, review_id: str, *, archived: bool, reason: str, actor: str
) -> tuple[int, str]:
    body = json.dumps({"archived": archived, "reason": reason, "actor": actor}).encode()
    request = urllib.request.Request(  # noqa: S310
        f"{base}/reviews/{review_id}/archive",
        data=body,
        method="POST",
        headers={"X-Attestor-Token": token, "Content-Type": "application/json"},
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
    parser.add_argument("--apply", action="store_true", help="actually archive them")
    parser.add_argument(
        "--state",
        action="append",
        default=None,
        help="review states to select (default: failed). Repeatable.",
    )
    parser.add_argument(
        "--review", action="append", default=[], help="archive this id whatever its state"
    )
    parser.add_argument("--restore", action="append", default=[], help="un-archive this id")
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument("--actor", default="operator")
    args = parser.parse_args()

    states = set(args.state or ["failed"])
    base = os.environ.get("CONTROL_PLANE_URL", "").rstrip("/")
    token = os.environ.get("ATTESTOR_WRITE_TOKEN", "").strip()
    if args.apply and (not base or not token):
        sys.exit("error: CONTROL_PLANE_URL and ATTESTOR_WRITE_TOKEN must be set to --apply")

    all_reviews = ReviewRepository().list_all(limit=200)
    named = set(args.review)
    restore = set(args.restore)

    selected: list[str] = []
    print("=" * 78)
    print("ARCHIVE -- what the landing page shows, and what it should")
    print("=" * 78)
    extra = f" + {sorted(named)}" if named else ""
    print(f"  selecting      : state in {sorted(states)}{extra}")
    print(f"  mode           : {'APPLY' if args.apply else 'report only'}\n")
    print(f"  {'review':26} {'state':16} {'now':>9}  action")
    print(f"  {'-' * 26} {'-' * 16} {'-' * 9}  {'-' * 28}")

    for review in all_reviews:
        now = "archived" if review.archived else "visible"
        if review.review_id in restore:
            action = "RESTORE -> visible" if review.archived else "already visible"
        elif review.archived:
            action = "already archived"
        elif review.state.value in states or review.review_id in named:
            action = "ARCHIVE"
            selected.append(review.review_id)
        else:
            action = "left visible"
        print(f"  {review.review_id:26} {review.state.value:16} {now:>9}  {action}")

    visible_after = sum(
        1
        for r in all_reviews
        if not (r.archived or r.review_id in selected) or r.review_id in restore
    )
    print(
        f"\n  {len(selected)} to archive, {len(restore)} to restore, {visible_after} visible after"
    )
    if not args.apply:
        print("  re-run with --apply.")
        return 0

    results: list[dict[str, Any]] = []
    for review_id in selected:
        status, body = _post_archive(
            base, token, review_id, archived=True, reason=args.reason, actor=args.actor
        )
        results.append({"review_id": review_id, "archived": True, "status": status})
        mark = "ok  " if status == 200 else "FAIL"
        print(f"  {mark} archive {review_id:26} {status} {body[:70]}")
    for review_id in restore:
        status, body = _post_archive(
            base, token, review_id, archived=False, reason="restored", actor=args.actor
        )
        results.append({"review_id": review_id, "archived": False, "status": status})
        mark = "ok  " if status == 200 else "FAIL"
        print(f"  {mark} restore {review_id:26} {status} {body[:70]}")

    failed = [r for r in results if r["status"] != 200]
    print(f"\n  {len(results) - len(failed)} of {len(results)} applied")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
