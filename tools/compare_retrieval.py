#!/usr/bin/env python3
"""Same questions, both drafting paths, one table. Why 48.3% cited and not ~90%.

    PROJECT_ID=attestor-505506 uv run python tools/compare_retrieval.py \
        --round rev-deployed-f4ea2875-r1 --limit 30 --write-proof

The deployed 60-question review cited 29 of 60 answers and refused 31 for want of
evidence. Phase 3's local 312-question run cited ~90%. Both numbers are real; only one
of them can be quoted as the system's accuracy, and until this harness ran there was no
basis for choosing.

## The hypothesis this was built to test, and why reading the code was not enough

ADR-0003 established that raw question text retrieves badly against Discovery Engine --
`"Recovery Time Objective"` returned zero results from a document containing that exact
phrase -- and fixed it with query expansion plus section-level reranking, taking recall@5
from 90% to 95%. If that layer were absent from the engine path, retrieval would regress
to the pre-fix baseline: the passages genuinely would not support the answers, the engine
would correctly reply INSUFFICIENT_EVIDENCE, and the deployed fleet would look stricter
from outside while actually being worse-retrieving. A different problem with identical
symptoms.

Reading `services/runtime/fleet_runtime.py` says the layer *is* present -- the engine's
search tool calls `ExpandingCorpusSearch`, the same class the local pipeline uses. But
"the same class is imported" is not "the same passages come back". The engine constructs
it per invocation with default collaborators and a cold section index, the model chooses
its own query string rather than being handed the question, and it decides for itself how
many searches to run. Any of those can move retrieval without changing an import. So this
measures both paths on the same questions and compares what came back.

## What is held constant

The questions and their triaged departments are read from Firestore, so both paths answer
the same question with the same department binding -- not a fresh triage that might route
one of them differently. Both pipelines are constructed with the same guard, the same
audit sink, and the same prior commitments as `runner.py` builds them in production. The
only difference is where retrieval and drafting execute, which is the difference under
test.

Audit events are written to a `compare-` run id so this diagnostic is distinguishable
from a real review in the compliance plane rather than contaminating it.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from attestor_core.domain import Department, Question
from attestor_platform.firestore import QuestionRepository

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"

#: The round whose questions and triage decisions are reused. Its answers are what the
#: 48.3% figure was computed from.
DEFAULT_ROUND = "rev-deployed-f4ea2875-r1"


def _passages(evidence: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "uri": item.document_uri,
            "document": item.document_title,
            "section": item.section,
            "score": round(float(item.score), 4),
            "chars": len(item.content),
        }
        for item in evidence
    ]


def _summarise(outcome: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """One path's result for one question, in the terms the comparison needs."""
    answer = outcome.answer
    scores = [float(e.score) for e in outcome.evidence]
    record: dict[str, Any] = {
        "passages": _passages(outcome.evidence),
        "passage_count": len(outcome.evidence),
        "max_score": round(max(scores), 4) if scores else None,
        "mean_score": round(statistics.fmean(scores), 4) if scores else None,
        "status": answer.status.value if answer else None,
        "citation_count": len(answer.citations) if answer else 0,
        "confidence": answer.confidence.value if answer else None,
        "blocked": outcome.blocked,
        "denied": outcome.denied,
        "needs_human": outcome.needs_human,
        "error": outcome.error,
        "seconds": round(outcome.draft_seconds, 2),
        "answer_prefix": (answer.text[:200] if answer else None),
    }
    if extra:
        record.update(extra)
    return record


class _InstrumentedRemote:
    """Mixin that records what the engine actually returned, before the pipeline judges it.

    `_parse_events` is where a remote question's fate is decided, and the two things worth
    knowing are invisible in the outcome: whether the engine called its search tool at all,
    and what its own prose said. A question the engine answered `INSUFFICIENT_EVIDENCE`
    and a question whose tool returned nothing both arrive at the pipeline as an outcome
    with no citations, and they are different failures with different fixes.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.observed: dict[str, dict[str, Any]] = {}
        #: Which question *this thread* is working on. Thread-local for the same reason
        #: `RemoteDraftingPipeline` makes the drafted text thread-local: the comparison runs
        #: question pairs concurrently, and an instance attribute here would attribute one
        #: thread's engine prose to another thread's question. That would not crash -- it
        #: would produce a plausible-looking table with some rows quietly wrong, which is
        #: the worst failure mode a measurement harness has.
        self._current = threading.local()

    def _query_with_retry(
        self, engine: Any, department: Department, question: Question, message: str
    ) -> list[Any]:
        self._current.question_id = question.question_id
        events: list[Any] = super()._query_with_retry(  # type: ignore[misc]
            engine, department, question, message
        )
        tool_calls = 0
        passages = 0
        queries: list[str] = []
        for event in events:
            payload = event if isinstance(event, dict) else {}
            for part in (payload.get("content") or {}).get("parts") or []:
                if part.get("function_call"):
                    tool_calls += 1
                response = (part.get("function_response") or {}).get("response")
                if isinstance(response, dict):
                    passages += len(response.get("passages") or [])
                    queries.extend(str(q) for q in (response.get("queries_run") or ()))
        self.observed[question.question_id] = {
            "engine_tool_calls": tool_calls,
            "engine_passages_before_dedup": passages,
            "engine_queries_run": queries,
            "engine_events": len(events),
        }
        return events

    def _generate(self, model: str, prompt: str) -> str:
        text = super()._generate(model, prompt)  # type: ignore[misc]
        record = self.observed.get(getattr(self._current, "question_id", ""))
        if record is not None and "engine_text_prefix" not in record:
            record["engine_text_prefix"] = text[:300]
            record["engine_said_insufficient"] = "INSUFFICIENT_EVIDENCE" in text
        return str(text)


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """One path's aggregate over however many questions have finished."""
    cited = [r for r in rows if r[key]["citation_count"] > 0]
    scored = [r[key]["max_score"] for r in rows if r[key]["max_score"] is not None]
    return {
        "cited": len(cited),
        "citation_rate": round(len(cited) / len(rows), 4) if rows else None,
        "zero_passage_questions": sum(1 for r in rows if r[key]["passage_count"] == 0),
        "mean_passage_count": (
            round(statistics.fmean(r[key]["passage_count"] for r in rows), 2) if rows else None
        ),
        "mean_top_score": round(statistics.fmean(scored), 4) if scored else None,
        "flagged_no_evidence": sum(1 for r in rows if r[key]["status"] == "flagged_no_evidence"),
        "quarantined": sum(1 for r in rows if r[key]["blocked"]),
        "seconds_total": round(sum(r[key]["seconds"] for r in rows), 1),
    }


def _build_report(
    records: list[dict[str, Any]], args: argparse.Namespace, *, review_id: str, run_id: str
) -> dict[str, Any]:
    """The artefact, computable at any point rather than only when every question is done."""
    local_stats = _rate(records, "local")
    remote_stats = _rate(records, "deployed")
    jaccards = [r["jaccard_documents"] for r in records if r["jaccard_documents"] is not None]

    # The verdict is written by the numbers rather than by me. Three outcomes were possible
    # and each has a different fix, so the artefact says which one it found.
    local_rate = local_stats["citation_rate"] or 0.0
    remote_rate = remote_stats["citation_rate"] or 0.0
    if remote_rate >= local_rate - 0.05:
        verdict = (
            "NO RETRIEVAL REGRESSION. The deployed path cites at or above the local path on "
            "the same questions, so the 48.3% figure from the throttled 60-question run is "
            "not a property of executing on the engines."
        )
    elif (remote_stats["mean_top_score"] or 0.0) >= (local_stats["mean_top_score"] or 0.0) - 0.02:
        verdict = (
            "RETRIEVAL IS INTACT, THE INSTRUCTION IS STRICTER. The engine retrieves passages "
            "of equivalent relevance and then declines to answer from them more often. The "
            "fix is prompt-level, in the department instruction pickled into the engine."
        )
    else:
        verdict = (
            "RETRIEVAL REGRESSION ON THE ENGINE PATH. Passages retrieved are materially worse, "
            "so the engine's INSUFFICIENT_EVIDENCE replies are correct and the fault is in "
            "retrieval. Port expansion and section reranking into the engine and re-measure."
        )

    return {
        "case": "citation_gap_side_by_side",
        "round_id": args.round,
        "review_id": review_id,
        "audit_run_id": run_id,
        "armor": "off" if args.no_guard else "on, both paths",
        "concurrency": args.workers,
        "questions": len(records),
        "questions_requested": args.limit,
        "complete": len(records) >= min(args.limit, len(records)) and len(records) == args.limit,
        "local": local_stats,
        "deployed": remote_stats,
        "engine_said_insufficient": sum(
            1 for r in records if r["deployed"].get("engine_said_insufficient") is True
        ),
        "engine_ran_no_search": sum(
            1 for r in records if r["deployed"].get("engine_tool_calls") == 0
        ),
        "mean_document_jaccard": round(statistics.fmean(jaccards), 4) if jaccards else None,
        "verdict": verdict,
        "held_constant": (
            "question text and triaged department read from Firestore; same guard, audit "
            "sink and prior commitments as runner.py builds in production; only the "
            "execution location of retrieval and drafting differs"
        ),
        "per_question": records,
    }


def _write_partial(
    records: list[dict[str, Any]], args: argparse.Namespace, *, review_id: str, run_id: str
) -> Path:
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    out = PROOF_DIR / "citation-gap-side-by-side.json"
    report = _build_report(records, args, review_id=review_id, run_id=run_id)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return out


def _select(questions: list[Question], limit: int) -> list[Question]:
    """Take a department-balanced slice rather than the first N.

    The questions are stored in questionnaire order, and questionnaires cluster by topic,
    so the first 30 would be almost entirely one department. A gap that lives in one
    corpus would then either dominate the result or vanish from it.
    """
    by_department: dict[Department, list[Question]] = {}
    for question in questions:
        by_department.setdefault(question.department, []).append(question)
    ordered = sorted(by_department.items(), key=lambda kv: kv[0].value)
    chosen: list[Question] = []
    index = 0
    while len(chosen) < limit and any(index < len(qs) for _, qs in ordered):
        for _, group in ordered:
            if index < len(group) and len(chosen) < limit:
                chosen.append(group[index])
        index += 1
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", default=DEFAULT_ROUND)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--write-proof", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="question pairs in flight. 3 is six concurrent engine queries -- deliberately "
        "well under the load at which the deployed run met the regional quota.",
    )
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="Skip Model Armor on both paths. Symmetric either way; on by default because "
        "that is what production does.",
    )
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    from attestor_fleet.callbacks.audit import FirestoreAuditSink
    from attestor_fleet.callbacks.guard import ArmorGuard
    from attestor_fleet.pipeline import ReviewPipeline
    from dispatcher.remote import RemoteDraftingPipeline

    class Instrumented(_InstrumentedRemote, RemoteDraftingPipeline):
        pass

    review_id = args.round.rsplit("-r", 1)[0]
    questions = _select(QuestionRepository().for_round(args.round), args.limit)
    if not questions:
        sys.exit(f"error: no questions found for round {args.round!r}")

    guard = None if args.no_guard else ArmorGuard()
    run_id = f"compare-{int(time.time())}"
    common: dict[str, Any] = {
        "review_id": review_id,
        "run_id": run_id,
        "round_id": f"{run_id}-compare",
        "guard": guard,
        "audit": FirestoreAuditSink(),
    }

    local = ReviewPipeline(**common)
    remote = Instrumented(**common)

    print("=" * 100)
    print(f"SIDE BY SIDE: {len(questions)} questions, local in-process vs deployed engines")
    print("=" * 100)
    print(f"  round     : {args.round}")
    print(f"  armor     : {'off' if args.no_guard else 'on (both paths)'}")
    print(f"  audit run : {run_id}\n")
    header = f"  {'question':<20} {'dept':<12} {'local':>22}  {'deployed':>22}"
    print(header)
    print(f"  {'-' * 20} {'-' * 12} {'-' * 22}  {'-' * 22}")

    def cell(row: dict[str, Any]) -> str:
        return (
            f"{row['passage_count']}p/{row['citation_count']}c "
            f"{row['max_score'] if row['max_score'] is not None else '--':>6} "
            f"{(row['status'] or 'none')[:8]:<8}"
        )

    def compare_one(question: Question) -> dict[str, Any]:
        """Both paths for one question. The pair stays on one thread so the two results
        are taken under the same conditions rather than minutes apart."""
        local_row = _summarise(local.draft(question))
        remote_outcome = remote.draft(question)
        remote_row = _summarise(remote_outcome, extra=remote.observed.get(question.question_id, {}))

        local_uris = {p["uri"] for p in local_row["passages"]}
        remote_uris = {p["uri"] for p in remote_row["passages"]}
        union = local_uris | remote_uris
        return {
            "question_id": question.question_id,
            "text": question.text,
            "department": question.department.value,
            "local": local_row,
            "deployed": remote_row,
            "documents_in_common": sorted(local_uris & remote_uris),
            "jaccard_documents": (
                round(len(local_uris & remote_uris) / len(union), 3) if union else None
            ),
        }

    # Concurrent, and modestly so. The first attempt at this harness ran serially, took
    # ~35s per question pair, and was cut off by its own timeout at 24 of 30 with the
    # aggregate never printed. Three workers is six concurrent engine queries -- a quarter
    # of the load at which the deployed run first met the regional quota, so the throttling
    # that confounded that run cannot confound this one.
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for record in pool.map(compare_one, questions):
            records.append(record)
            print(
                f"  {str(record['question_id'])[:20]:<20} {record['department']!s:<12} "
                f"{cell(record['local']):>22}  {cell(record['deployed']):>22}"
            )
            # Written as they arrive. A harness whose only output is at the end has no
            # output at all if it is interrupted, which is exactly what happened once.
            if args.write_proof:
                _write_partial(records, args, review_id=review_id, run_id=run_id)

    report = _build_report(records, args, review_id=review_id, run_id=run_id)
    local_stats = report["local"]
    remote_stats = report["deployed"]

    print(f"\n  {'figure':<28} {'local':>12} {'deployed':>12}")
    print(f"  {'-' * 28} {'-' * 12} {'-' * 12}")
    for label, key in (
        ("cited", "cited"),
        ("citation rate", "citation_rate"),
        ("questions with 0 passages", "zero_passage_questions"),
        ("mean passages retrieved", "mean_passage_count"),
        ("mean top relevance score", "mean_top_score"),
        ("flagged no evidence", "flagged_no_evidence"),
        ("drafting seconds, summed", "seconds_total"),
    ):
        print(f"  {label:<28} {local_stats[key]!s:>12} {remote_stats[key]!s:>12}")

    n = len(records)
    print(f"\n  engine replied INSUFFICIENT_EVIDENCE : {report['engine_said_insufficient']} of {n}")
    print(f"  engine ran no search at all          : {report['engine_ran_no_search']} of {n}")
    print(f"  document overlap (Jaccard, mean)     : {report['mean_document_jaccard']}")
    print(f"\n  {report['verdict']}")

    if args.write_proof:
        print(f"\nwrote {_write_partial(records, args, review_id=review_id, run_id=run_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
