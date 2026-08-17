#!/usr/bin/env python3
"""The 22-day resume: a dormant review wakes with its memory, and does not start over.

    PROJECT_ID=attestor-505506 uv run python tools/verify_resume.py --write-proof

The seeded review `rev-acme-2026-q3` was created backdated, delivered its first round, and
has been dormant since. Round 2 arrives now. Two things have to be true, and they are
different claims:

1. **It resumes rather than restarts.** Round 1's answers are still there, untouched, and
   round 2 is a new round on the same review — not a re-run of the questionnaire.
2. **It resumes with context.** The round-1 commitments come back out of Memory Bank and
   are in hand *before* any round-2 question is drafted, which is what makes the
   consistency check possible at all.

The second is the one that is easy to fake and easy to get subtly wrong. `open_follow_up`
reads the commitments on the round that will use them and fails loudly if Memory Bank is
unreachable, rather than proceeding with an empty list — because an empty list is
indistinguishable from "this customer was promised nothing", and that silently disables the
consistency check for every question in the round. This harness therefore checks the
*count* that the handler recorded, not merely that the stage succeeded.

Everything after the first published envelope happens on Cloud Run. This process publishes
`open_follow_up` and then watches Firestore.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from attestor_core.protocol import WorkEnvelope, WorkKind
from attestor_platform.firestore import (
    AnswerRepository,
    AuditEventRepository,
    ReviewRepository,
)
from attestor_platform.pubsub import WorkPublisher
from attestor_platform.storage import StorageClient

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"
FOLLOWUP = ROOT / "seed" / "questionnaires" / "followup" / "acme-vendor-review-r2.xlsx"

REVIEW_ID = "rev-acme-2026-q3"
ROUND_ONE = f"{REVIEW_ID}-r1"

STALL_SECONDS = 900


def _age_days(value: Any) -> float | None:
    """How old a Firestore timestamp is, in days. `None` if it is not a timestamp at all.

    The narrowed value gets its own name rather than being reassigned over the `Any`
    parameter: rebinding `value` keeps it `Any` for the type checker, so the arithmetic
    below silently loses its types and the `float` this promises to return is unchecked.
    """
    when: datetime
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return None
    elif isinstance(value, datetime):
        when = value
    else:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - when).total_seconds() / 86400, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    parser.add_argument("--ordinal", type=int, default=2)
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    from google.cloud import firestore

    db = firestore.Client(project=os.environ["PROJECT_ID"])
    reviews = ReviewRepository()
    answers_repo = AnswerRepository()
    audit = AuditEventRepository()
    storage = StorageClient()

    review = reviews.get(REVIEW_ID)
    if review is None:
        sys.exit(f"error: {REVIEW_ID} not found -- run the seed first")

    snapshot = db.collection("reviews").document(REVIEW_ID).get()
    created_at = (snapshot.to_dict() or {}).get("created_at")
    age = _age_days(created_at)

    before = answers_repo.for_round(ROUND_ONE)

    print("=" * 78)
    print("THE DORMANT REVIEW WAKES UP")
    print("=" * 78)
    print(f"  review          : {REVIEW_ID}")
    print(f"  created         : {created_at} ({age} days ago)")
    print(f"  state           : {review.state.value}")
    print(f"  round 1 answers : {len(before)}")
    print(f"  round 1 hash    : {_answers_fingerprint(before)}\n")

    gcs_uri = storage.upload_file(
        f"questionnaires/followup-{uuid.uuid4().hex[:8]}/{FOLLOWUP.name}",
        str(FOLLOWUP),
        bucket_suffix="uploads",
    )
    run_id = f"resume-{int(time.time())}"
    round_id = f"{REVIEW_ID}-r{args.ordinal}"

    started = time.perf_counter()
    WorkPublisher().publish(
        WorkEnvelope.for_work(
            message_id=f"{run_id}-followup",
            review_id=REVIEW_ID,
            run_id=run_id,
            round_id=round_id,
            kind=WorkKind.OPEN_FOLLOW_UP,
            payload={"gcs_uri": gcs_uri, "round_ordinal": args.ordinal},
        )
    )
    print(f"  published open_follow_up (round {args.ordinal}); watching\n")

    print(f"  {'elapsed':>8}  {'state':<16} round-2 answers")
    print(f"  {'-' * 8}  {'-' * 16} ---------------")

    last_change = time.perf_counter()
    signature: tuple[Any, ...] = ()
    state = review.state.value

    # The review starts in `delivered` -- that is what "dormant" means here. So the exit
    # condition cannot simply be "state is delivered", or the loop returns on its first
    # poll having watched nothing. It has to see the review *leave* the terminal state
    # first, which is what `open_follow_up` does, and only then wait for it to arrive back.
    # The first version of this harness did not, reported a 4.6-second resume, and would
    # have been a very convincing artefact for something that had not happened.
    left_terminal = False

    while True:
        elapsed = time.perf_counter() - started
        current_review = reviews.get(REVIEW_ID)
        state = current_review.state.value if current_review else state
        produced = answers_repo.for_round(round_id)

        current = (state, len(produced))
        if current != signature:
            signature = current
            last_change = time.perf_counter()
            print(f"  {elapsed:7.0f}s  {state:<16} {len(produced)}")

        if state not in {"delivered", "awaiting_human"}:
            left_terminal = True
        if left_terminal and state in {"delivered", "awaiting_human"}:
            break
        if time.perf_counter() - last_change > STALL_SECONDS:
            print(f"\n  no progress for {STALL_SECONDS}s -- stopping")
            break
        time.sleep(10)

    wall = time.perf_counter() - started
    after = answers_repo.for_round(ROUND_ONE)
    produced = answers_repo.for_round(round_id)
    events = [e for e in audit.for_review(REVIEW_ID, limit=1000) if e.get("run_id") == run_id]

    # Did the handler actually have the commitments in hand before drafting?
    prior_commitments = 0
    for event in events:
        detail = event.get("detail") or {}
        if detail.get("stage") == "open_follow_up":
            prior_commitments = int(detail.get("prior_commitments") or 0)

    contradictions = [
        e
        for e in events
        if (e.get("detail") or {}).get("redraft") or (e.get("detail") or {}).get("second_verdict")
    ]

    resumed = _answers_fingerprint(before) == _answers_fingerprint(after)
    with_context = prior_commitments > 0
    produced_round_two = len(produced) > 0

    print(f"\n  wall clock              : {wall:.1f}s")
    print(f"  final state             : {state}")
    print(f"  round 1 answers after   : {len(after)} (hash {_answers_fingerprint(after)})")
    print(f"  round 1 untouched       : {resumed}")
    print(f"  prior commitments loaded: {prior_commitments}")
    print(f"  round 2 answers         : {len(produced)}")
    print(f"  constrained redrafts    : {len(contradictions)}")

    passed = resumed and with_context and produced_round_two
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")
    if not with_context:
        print("  (a round that resumes with zero commitments has no consistency check)")

    report = {
        "case": "dormant_review_resume",
        "pass": passed,
        "review_id": REVIEW_ID,
        "created_at": str(created_at),
        "age_days": age,
        "round_one": {
            "round_id": ROUND_ONE,
            "answers_before": len(before),
            "answers_after": len(after),
            "fingerprint_before": _answers_fingerprint(before),
            "fingerprint_after": _answers_fingerprint(after),
            "untouched": resumed,
        },
        "round_two": {
            "round_id": round_id,
            "run_id": run_id,
            "gcs_uri": gcs_uri,
            "answers": len(produced),
            "prior_commitments_loaded": prior_commitments,
            "constrained_redrafts": len(contradictions),
        },
        "final_state": state,
        "wall_seconds": round(wall, 1),
        "execution": "Cloud Run dispatcher via Eventarc push; drafting on Agent Runtime",
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "resume-22-day.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


def _answers_fingerprint(answers: list[Any]) -> str:
    """A stable digest of round 1, so "untouched" is checked rather than assumed.

    Comparing counts alone would miss a resume that rewrote every answer in place, which
    is exactly the failure this is here to rule out.
    """
    import hashlib

    # sha256 of the text rather than `hash()`: Python randomises string hashing per
    # process, so a `hash()`-based fingerprint would be meaningless the moment anyone
    # compared this artefact with a later run's.
    material = "|".join(
        sorted(
            f"{a.question_id}:{a.status.value}:{hashlib.sha256(a.text.encode('utf-8')).hexdigest()}"
            for a in answers
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    raise SystemExit(main())
