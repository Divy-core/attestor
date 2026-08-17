#!/usr/bin/env python3
"""Move commitments off the Phase 0 probe engine onto the deployed fleet.

    PROJECT_ID=attestor-505506 uv run python tools/migrate_memory.py --verify
    PROJECT_ID=attestor-505506 uv run python tools/migrate_memory.py --apply --write-proof

Memory Bank is scoped **per Agent Engine**. Phase 0 created a throwaway probe engine to
prove the platform worked, Phase 4 made Memory Bank the canonical commitment store, and
the commitments have been living on that probe ever since. Phase 5 deployed the real
fleet, so the probe is now a resource the whole consistency story depends on and nothing
else uses — which is exactly the sort of thing that gets deleted by someone tidying up.

The migration is a copy, a readback, and a comparison, in that order, and the probe engine
is **not deleted by this script**. Deletion happens by hand, after the readback has
passed, because a migration tool that deletes its own source is one bug away from being an
erasure tool. The commitments are the round-1 promises the round-2 consistency check is
measured against; losing them loses the hardest thing in the build.

## What "byte-identical" means here

Not "the target has the same number of memories". The comparison is on the exact
`(question_id, statement)` pairs the *production read path* returns — `for_review`, the
same call the pipeline makes — because a memory that survives the copy but comes back
differently through retrieval is not migrated, it is corrupted in a way that would only
show up as a consistency check quietly failing to match.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"
DEPLOYMENT = PROOF_DIR / "fleet-deployment.json"

#: The Phase 0 probe. Recorded here rather than passed, so the source of truth for "which
#: engine currently holds the commitments" is in the repo.
PROBE_ENGINE_ID = "8598754324522205184"

#: Reviews whose commitments must survive. `rev-acme-2026-q3` is the backdated seeded
#: review the 22-day resume and the round-2 consistency demo both run against.
REVIEWS = ("rev-acme-2026-q3",)


def target_engine_id() -> str:
    """The orchestrator engine. Commitments are review-scoped, not department-scoped.

    Putting them on a department engine would mean the legal drafter could not read a
    commitment the security drafter made, which is precisely the cross-department
    contradiction the consistency check exists to catch.
    """
    engines = json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["engines"]
    for engine in engines:
        if engine["role"] == "orchestrator":
            return str(engine["resource_name"]).rsplit("/", 1)[-1]
    sys.exit("error: no deployed orchestrator engine in fleet-deployment.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="copy source -> target")
    parser.add_argument("--verify", action="store_true", help="compare without writing")
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()
    if not (args.apply or args.verify):
        parser.error("pass --apply or --verify")

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    from attestor_core.domain import Commitment
    from attestor_core.domain.ids import make_dedup_key
    from attestor_platform.memory import MemoryBankCommitments

    target = target_engine_id()
    source_store = MemoryBankCommitments(engine_id=PROBE_ENGINE_ID)
    target_store = MemoryBankCommitments(engine_id=target)

    print("=" * 78)
    print("MEMORY BANK MIGRATION -- probe engine -> deployed fleet")
    print("=" * 78)
    print(f"  source (probe) : reasoningEngines/{PROBE_ENGINE_ID}")
    print(f"  target (fleet) : reasoningEngines/{target}")
    print(f"  mode           : {'apply' if args.apply else 'verify only'}\n")

    results: list[dict[str, Any]] = []
    all_identical = True

    for review_id in REVIEWS:
        source_pairs = sorted(source_store.for_review(review_id))
        print(f"  {review_id}")
        print(f"    source commitments : {len(source_pairs)}")

        if args.apply:
            for question_id, statement in source_pairs:
                # The commitment id is derived, not carried: `make_dedup_key` over the
                # same inputs produces the same id on the target, so re-running the
                # migration overwrites rather than duplicating.
                target_store.record(
                    Commitment(
                        commitment_id=make_dedup_key(review_id, "migrated", question_id),
                        review_id=review_id,
                        round_id="migrated",
                        question_id=question_id,
                        statement=statement,
                    )
                )
            print(f"    wrote to target    : {len(source_pairs)}")

        target_pairs = sorted(target_store.for_review(review_id))
        print(f"    target commitments : {len(target_pairs)}")

        missing = [p for p in source_pairs if p not in target_pairs]
        altered = [
            (qid, statement)
            for qid, statement in source_pairs
            if any(q == qid and s != statement for q, s in target_pairs)
        ]
        identical = not missing and not altered
        all_identical = all_identical and identical

        print(f"    missing on target  : {len(missing)}")
        print(f"    altered in transit : {len(altered)}")
        print(f"    readback           : {'IDENTICAL' if identical else 'MISMATCH'}\n")

        results.append(
            {
                "review_id": review_id,
                "source_count": len(source_pairs),
                "target_count": len(target_pairs),
                "missing": [q for q, _ in missing],
                "altered": [q for q, _ in altered],
                "identical": identical,
                # The statements themselves, so a later reader can check the comparison
                # rather than trust the boolean.
                "source_pairs": source_pairs,
                "target_pairs": target_pairs,
            }
        )

    print(f"  RESULT : {'PASS' if all_identical else 'FAIL'}")
    if all_identical and args.apply:
        print(
            "\n  The probe engine is now safe to delete. This script does not delete it:\n"
            f"    reasoningEngines/{PROBE_ENGINE_ID}"
        )
    elif not all_identical:
        print("\n  DO NOT delete the probe engine. The readback did not match.")

    report = {
        "case": "memory_bank_migration",
        "pass": all_identical,
        "applied": args.apply,
        "source_engine": PROBE_ENGINE_ID,
        "target_engine": target,
        "reviews": results,
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "memory-bank-migration.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if all_identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
