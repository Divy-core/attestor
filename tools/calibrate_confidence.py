#!/usr/bin/env python3
"""Measure the retrieval-score distribution and pick confidence thresholds from it.

    PROJECT_ID=attestor-505506 uv run python tools/calibrate_confidence.py --write-proof

The old thresholds (`_WEAK_SCORE = 0.55`, `_STRONG_MAX_SCORE = 0.75`,
`_STRONG_MEAN_SCORE = 0.60`) were calibrated against rank-derived scores, which is to say
against a scale that did not mean anything: the top hit always scored 0.95. Cosine
similarity from `text-embedding-005` uses a different and much narrower range -- unrelated
text in the same domain still scores around 0.6 -- so those constants do not transfer and
must not be assumed to.

This measures the two distributions that actually matter, using the 63 hand-labelled
retrieval pairs as ground truth:

* **relevant** — the passage from the document the label says answers the question;
* **irrelevant** — every other passage retrieved for that question.

A threshold is only defensible if it separates those two. The output reports both
distributions, the separation between them, and the thresholds derived from it.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from attestor_core.domain import Department
from attestor_platform.search import ExpandingCorpusSearch, QueryExpander, RelevanceScorer

ROOT = Path(__file__).parent.parent
PAIRS = ROOT / "evals" / "retrieval_recall.json"
PROOF_DIR = ROOT / "docs" / "proof"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "p05": round(percentile(values, 5), 4),
        "p25": round(percentile(values, 25), 4),
        "p50": round(percentile(values, 50), 4),
        "p75": round(percentile(values, 75), 4),
        "p95": round(percentile(values, 95), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    pairs = json.loads(PAIRS.read_text(encoding="utf-8"))["pairs"]
    if args.limit:
        pairs = pairs[: args.limit]

    expander = QueryExpander(use_model=False)
    scorer = RelevanceScorer()
    searches = {
        department: ExpandingCorpusSearch(department, expander=expander, scorer=scorer)
        for department in (Department.SECURITY, Department.LEGAL, Department.ENGINEERING)
    }

    relevant: list[float] = []
    irrelevant: list[float] = []
    #: Per QUESTION, the best passage from the labelled document and the best from any
    #: other document. This is the comparison that actually decides an answer: does the
    #: passage that answers outrank the best distractor? The passage-level distributions
    #: below cannot answer that, because section-level reranking retrieves several
    #: sections of the labelled document and most of them are not the answering section.
    best_relevant: list[float] = []
    best_distractor: list[float] = []
    max_scores: list[float] = []
    mean_scores: list[float] = []
    top_is_labelled = 0
    label_retrieved = 0
    started = time.perf_counter()

    for index, pair in enumerate(pairs, start=1):
        department = Department(pair["department"])
        expected = pair["expected"]
        result = searches[department].retrieve(pair["question"])
        if not result.evidence:
            print(f"  {index:3}/{len(pairs)}  NO EVIDENCE  {pair['question'][:56]}")
            continue

        scores = [e.score for e in result.evidence]
        max_scores.append(max(scores))
        mean_scores.append(statistics.fmean(scores))
        if expected in result.evidence[0].document_uri:
            top_is_labelled += 1

        hit = False
        question_relevant: list[float] = []
        question_distractors: list[float] = []
        for item in result.evidence:
            if expected in item.document_uri:
                relevant.append(item.score)
                question_relevant.append(item.score)
                hit = True
            else:
                irrelevant.append(item.score)
                question_distractors.append(item.score)
        label_retrieved += int(hit)
        if question_relevant:
            best_relevant.append(max(question_relevant))
        if question_distractors:
            best_distractor.append(max(question_distractors))

        print(
            f"  {index:3}/{len(pairs)}  max={max(scores):.3f} "
            f"mean={statistics.fmean(scores):.3f} label={'HIT ' if hit else 'MISS'} "
            f"{pair['question'][:48]}"
        )

    elapsed = time.perf_counter() - started

    # The thresholds, derived from the distributions `compute_confidence` actually
    # consumes -- per-QUESTION max and mean over the cited passages, not the pooled
    # passage-level scores. That distinction matters once sections compete globally: the
    # pooled "relevant" bucket fills with non-answering sections of the right document
    # and its percentiles describe nothing the policy layer ever sees.
    #
    # _WEAK_SCORE   -- p05 of the per-question best score. Below the level at which a
    #                  question whose answer WAS retrieved ever lands, retrieval missed.
    # _STRONG_MAX   -- p50 of the per-question best score: a single hit this good stands
    #                  on its own.
    # _STRONG_MEAN  -- p25 of the per-question mean: a citation set averaging above this
    #                  is corroboration rather than noise.
    weak = round(percentile(max_scores, 5), 2)
    strong_max = round(percentile(max_scores, 50), 2)
    strong_mean = round(percentile(mean_scores, 25), 2)

    proposed = {
        "_WEAK_SCORE": weak,
        "_STRONG_MAX_SCORE": strong_max,
        "_STRONG_MEAN_SCORE": strong_mean,
    }

    report: dict[str, Any] = {
        "method": scorer.last_method,
        "pairs": len(pairs),
        "label_retrieved": label_retrieved,
        "top_hit_is_labelled": top_is_labelled,
        "seconds": round(elapsed, 1),
        "billable_characters": scorer.billable_characters,
        "relevant_passage_scores": describe(relevant),
        "irrelevant_passage_scores": describe(irrelevant),
        "best_relevant_per_question": describe(best_relevant),
        "best_distractor_per_question": describe(best_distractor),
        "per_question_max_score": describe(max_scores),
        "per_question_mean_score": describe(mean_scores),
        # Two separations, because they answer different questions. The pooled one is
        # depressed by non-answering sections of the correct document; the best-vs-best
        # one is what decides whether the answer outranks its best distractor.
        "separation_median_pooled": round(
            (statistics.median(relevant) if relevant else 0.0)
            - (statistics.median(irrelevant) if irrelevant else 0.0),
            4,
        ),
        "separation_median_best": round(
            (statistics.median(best_relevant) if best_relevant else 0.0)
            - (statistics.median(best_distractor) if best_distractor else 0.0),
            4,
        ),
        "best_beats_distractor": sum(
            1 for r, d in zip(best_relevant, best_distractor, strict=False) if r > d
        ),
        "proposed_thresholds": proposed,
    }

    print("\n" + "=" * 66)
    print("SCORE DISTRIBUTION")
    print("=" * 66)
    print(json.dumps(report, indent=2))

    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "confidence-calibration.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
