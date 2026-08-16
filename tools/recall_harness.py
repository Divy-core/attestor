#!/usr/bin/env python3
"""Measure retrieval recall@k: raw question text versus expanded queries.

Without a number, "we fixed retrieval" is a feeling. With a number it is a claim that
belongs in the write-up under findings and learnings, and the before/after delta is a
genuinely strong submission artifact.

    PROJECT_ID=attestor-505506 uv run python tools/recall_harness.py
    PROJECT_ID=attestor-505506 uv run python tools/recall_harness.py --k 5 --no-model

Gate: expanded recall@5 must reach 0.85 or better.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from attestor_core.domain import Department
from attestor_platform.search import CorpusSearch
from attestor_platform.search.expansion import ExpandingCorpusSearch, QueryExpander

EVAL_FILE = Path(__file__).parent.parent / "evals" / "retrieval_recall.json"
PROOF_FILE = Path(__file__).parent.parent / "docs" / "proof" / "retrieval-recall.md"
GATE = 0.85
#: Bounded so the harness does not become its own rate-limit incident. A run at 6
#: workers throttled Discovery Engine and produced a fake 56% recall figure before
#: `SearchUnavailable` existed to make the failure visible.
MAX_WORKERS = 3


@dataclass
class Outcome:
    department: str
    question: str
    expected: str
    raw_hit: bool
    expanded_hit: bool
    raw_docs: tuple[str, ...]
    expanded_docs: tuple[str, ...]
    queries_run: tuple[str, ...]


def doc_name(uri: str) -> str:
    """`gs://bucket/security/encryption-standard.txt` -> `encryption-standard`."""
    return uri.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def run_pair(
    pair: dict[str, str],
    k: int,
    searches: dict[str, CorpusSearch],
    expanders: dict[str, ExpandingCorpusSearch],
) -> Outcome:
    department = pair["department"]
    question = pair["question"]
    expected = pair["expected"]

    raw = searches[department].query(question, page_size=k)
    raw_docs = tuple(doc_name(e.document_uri) for e in raw)

    result = expanders[department].retrieve(question, top_k=k)
    expanded_docs = tuple(doc_name(e.document_uri) for e in result.evidence)

    return Outcome(
        department=department,
        question=question,
        expected=expected,
        raw_hit=expected in raw_docs,
        expanded_hit=expected in expanded_docs,
        raw_docs=raw_docs,
        expanded_docs=expanded_docs,
        queries_run=result.queries_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=5, help="recall@k (default 5)")
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="heuristic expansion only, no model call (faster, free, deterministic)",
    )
    parser.add_argument("--write-proof", action="store_true", help="write docs/proof/ report")
    args = parser.parse_args()

    if not (os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")):
        sys.exit("error: PROJECT_ID must be set")

    spec = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    pairs = spec["pairs"]

    expander = QueryExpander(use_model=not args.no_model)
    searches = {
        d.value: CorpusSearch(d)
        for d in (Department.SECURITY, Department.LEGAL, Department.ENGINEERING)
    }
    expanders = {
        d.value: ExpandingCorpusSearch(d, expander=expander, search=searches[d.value])
        for d in (Department.SECURITY, Department.LEGAL, Department.ENGINEERING)
    }

    mode = "heuristic only" if args.no_model else f"heuristic + model ({'flash-lite'})"
    print(f"recall@{args.k} over {len(pairs)} labelled pairs   [expansion: {mode}]\n")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outcomes = list(pool.map(lambda p: run_pair(p, args.k, searches, expanders), pairs))
    elapsed = time.perf_counter() - started

    raw_hits = sum(o.raw_hit for o in outcomes)
    exp_hits = sum(o.expanded_hit for o in outcomes)
    total = len(outcomes)
    raw_recall = raw_hits / total
    exp_recall = exp_hits / total

    by_dept: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_dept[outcome.department].append(outcome)

    print(f"{'department':14} {'pairs':>6} {'raw':>8} {'expanded':>10}")
    print("-" * 42)
    for department in sorted(by_dept):
        group = by_dept[department]
        r = sum(o.raw_hit for o in group) / len(group)
        e = sum(o.expanded_hit for o in group) / len(group)
        print(f"{department:14} {len(group):>6} {r:>8.0%} {e:>10.0%}")
    print("-" * 42)
    print(f"{'TOTAL':14} {total:>6} {raw_recall:>8.0%} {exp_recall:>10.0%}")
    print(f"\nelapsed: {elapsed:.1f}s")

    fixed = [o for o in outcomes if o.expanded_hit and not o.raw_hit]
    broken = [o for o in outcomes if o.raw_hit and not o.expanded_hit]
    still = [o for o in outcomes if not o.expanded_hit]

    if fixed:
        print(f"\n=== expansion FIXED {len(fixed)} ===")
        for o in fixed[:12]:
            print(f"  [{o.department}] {o.question[:62]!r} -> {o.expected}")
    if broken:
        print(f"\n=== expansion BROKE {len(broken)} (regression) ===")
        for o in broken:
            print(
                f"  [{o.department}] {o.question[:62]!r} -> wanted {o.expected}, got {o.expanded_docs}"
            )
    if still:
        print(f"\n=== STILL MISSING {len(still)} ===")
        for o in still:
            print(f"  [{o.department}] {o.question[:58]!r}")
            print(f"      wanted: {o.expected}")
            print(f"      got   : {o.expanded_docs or '(nothing)'}")
            print(f"      tried : {[q[:48] for q in o.queries_run]}")

    print(f"\nGATE recall@{args.k} >= {GATE:.0%}: ", end="")
    passed = exp_recall >= GATE
    print("PASS" if passed else f"FAIL (at {exp_recall:.0%})")

    if args.write_proof:
        _write_proof(args.k, mode, outcomes, raw_recall, exp_recall, elapsed, by_dept)
        print(f"wrote {PROOF_FILE}")

    return 0 if passed else 1


def _write_proof(
    k: int,
    mode: str,
    outcomes: list[Outcome],
    raw_recall: float,
    exp_recall: float,
    elapsed: float,
    by_dept: dict[str, list[Outcome]],
) -> None:
    lines = [
        "# Retrieval recall — raw question text vs expanded queries",
        "",
        f"`make recall` · {len(outcomes)} hand-labelled pairs · recall@{k} · expansion: {mode}",
        f"· elapsed {elapsed:.1f}s",
        "",
        "## Result",
        "",
        "| | recall@%d |" % k,
        "|---|---|",
        f"| Raw question text (baseline) | **{raw_recall:.0%}** |",
        f"| Expanded queries | **{exp_recall:.0%}** |",
        f"| Delta | **{exp_recall - raw_recall:+.0%}** |",
        "",
        "## By department",
        "",
        "| department | pairs | raw | expanded |",
        "|---|---|---|---|",
    ]
    for department in sorted(by_dept):
        group = by_dept[department]
        r = sum(o.raw_hit for o in group) / len(group)
        e = sum(o.expanded_hit for o in group) / len(group)
        lines.append(f"| {department} | {len(group)} | {r:.0%} | {e:.0%} |")

    fixed = [o for o in outcomes if o.expanded_hit and not o.raw_hit]
    lines += ["", f"## Cases expansion fixed ({len(fixed)})", ""]
    for o in fixed:
        lines.append(f"- `{o.question}` → `{o.expected}` ({o.department})")

    still = [o for o in outcomes if not o.expanded_hit]
    lines += ["", f"## Still missing ({len(still)})", ""]
    if not still:
        lines.append("None.")
    for o in still:
        lines.append(
            f"- `{o.question}` wanted `{o.expected}`, got `{o.expanded_docs or 'nothing'}`"
        )

    PROOF_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROOF_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
