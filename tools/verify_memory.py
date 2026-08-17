#!/usr/bin/env python3
"""Prove cross-session recall from Memory Bank, and prove the failure mode fails loudly.

    PROJECT_ID=attestor-505506 uv run python tools/verify_memory.py --write-proof

Two things get checked, and the second is the one that would have gone unnoticed.

**Recall.** This process never wrote anything. It reads commitments a *different* process
wrote — `seed.py`, in an earlier session, against a review created 22 days ago — and it
must get back the exact sentences, byte for byte, with their question ids. "Cross-session"
means precisely this: no shared memory, no shared client, no handoff.

**Unavailability is not emptiness.** A Memory Bank read that fails must raise, not return
`[]`. An empty list is indistinguishable from "this customer has no history", which
disables the consistency check for the whole round while the run reports success. That
mistake has been made four times in this project already, so it is tested rather than
asserted: the store is pointed at a nonexistent engine and the read must raise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from attestor_core.errors import ContextUnavailable
from attestor_platform.config import memory_bank_engine_id
from attestor_platform.memory import MemoryBankCommitments

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"
REVIEW_ID = "rev-acme-2026-q3"
ENGINE_ID = memory_bank_engine_id()

#: What round 1 committed to, 22 days ago. Round 2 attacks the first of these.
EXPECTED = {
    "Kestrel Data does not offer on-premises or self-hosted deployment",
    "Kestrel Data encrypts all customer data at rest",
    "Kestrel Data commits to a Recovery Time Objective of 4 hours",
    "Kestrel Data enforces hardware-backed multi-factor authentication",
    "Kestrel Data has never experienced a customer data breach",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    print("=" * 74)
    print("MEMORY BANK -- cross-session recall")
    print("=" * 74)
    print(f"  engine    : reasoningEngines/{ENGINE_ID}")
    print(f"  scope     : {{'review_id': '{REVIEW_ID}'}}")
    print("  this process wrote nothing; everything below was written by seed.py")
    print()

    store = MemoryBankCommitments(engine_id=ENGINE_ID)
    recalled = store.for_review(REVIEW_ID)

    print(f"  recalled  : {len(recalled)} commitments")
    for question_id, statement in sorted(recalled, key=lambda p: p[1]):
        print(f"    [{question_id or '- no ref -':16}] {statement[:78]}")

    statements = " || ".join(s for _, s in recalled)
    matched = sorted(e for e in EXPECTED if e in statements)
    missing = sorted(EXPECTED - set(matched))
    with_refs = sum(1 for question_id, _ in recalled if question_id)

    print(f"\n  matched   : {len(matched)}/{len(EXPECTED)} expected commitments")
    for gap in missing:
        print(f"    MISSING : {gap}")
    print(f"  with ids  : {with_refs}/{len(recalled)} carry a question ref")

    # --- the failure mode ---------------------------------------------------------
    print("\n  unavailable-is-not-empty:")
    broken = MemoryBankCommitments(engine_id="0000000000000000000")
    try:
        result = broken.for_review(REVIEW_ID)
    except ContextUnavailable as exc:
        raised = True
        detail = str(exc)[:120]
    else:
        raised = False
        detail = f"returned {result!r} instead of raising"
    print(f"    nonexistent engine raises : {raised}")
    print(f"    {detail}")

    passed = not missing and with_refs == len(recalled) and raised
    print(f"\n  RESULT    : {'PASS' if passed else 'FAIL'}")

    report: dict[str, Any] = {
        "case": "memory_bank_cross_session_recall",
        "pass": passed,
        "engine_id": ENGINE_ID,
        "review_id": REVIEW_ID,
        "written_by_this_process": 0,
        "recalled": len(recalled),
        "commitments": [{"question_id": q, "statement": s} for q, s in recalled],
        "expected_matched": len(matched),
        "expected_missing": missing,
        "carrying_question_ref": with_refs,
        "unavailable_raises": raised,
        "unavailable_detail": detail,
    }

    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "memory-bank-recall.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {out}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
