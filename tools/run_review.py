#!/usr/bin/env python3
"""Run a questionnaire through the fleet and report measured numbers.

These are the numbers that go in the demo video, so they are measured here rather than
estimated: questions triaged, answers drafted, citation rate, flagged count, wall-clock
duration, token spend by model, estimated cost, and p50/p95 drafting latency.

    PROJECT_ID=attestor-505506 uv run python tools/run_review.py --limit 60
    PROJECT_ID=attestor-505506 uv run python tools/run_review.py --questionnaire injected
    PROJECT_ID=attestor-505506 uv run python tools/run_review.py --questionnaire followup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from attestor_core.domain import AnswerStatus
from attestor_fleet.agents.intake import parse_xlsx
from attestor_fleet.callbacks.audit import FirestoreAuditSink, NullAuditSink
from attestor_fleet.callbacks.budget import BudgetLedger
from attestor_fleet.callbacks.guard import ArmorGuard
from attestor_fleet.orchestrator import ArtifactBrief, Orchestrator
from attestor_fleet.pipeline import ReviewPipeline, RunReport

ROOT = Path(__file__).parent.parent
QUESTIONNAIRES = {
    "clean": ROOT / "seed" / "questionnaires" / "clean" / "acme-vendor-review-r1.xlsx",
    "injected": ROOT
    / "seed"
    / "questionnaires"
    / "injected"
    / "acme-vendor-review-r1-injected.xlsx",
    "followup": ROOT / "seed" / "questionnaires" / "followup" / "acme-vendor-review-r2.xlsx",
}
PROOF_DIR = ROOT / "docs" / "proof"

#: The six questions the corpus deliberately cannot answer. They must come back
#: FLAGGED_NO_EVIDENCE -- a system that answers these confidently is worse than one that
#: answers fewer questions honestly.
GAP_MARKERS = (
    "cyber liability insurance",
    "source code escrow",
    "modern slavery",
    "environmental sustainability",
    "HITRUST",
    "SCIM",
)


def load_commitments(review_id: str) -> list[tuple[str, str]]:
    """Prior-round commitments, read from Memory Bank -- canonical since Phase 4.

    Phase 3 read Firestore directly. This function was written as the seam for that swap,
    and the swap turned out to be exactly this: one import and one call, with Firestore
    left in place as the queryable mirror the UI and the audit trail read.

    **This used to swallow every exception and return `[]`.** That is the fourth
    occurrence of the failure this project keeps meeting: an unreachable store, a
    permission error, or a query timeout produced "this customer has no prior
    commitments", which is indistinguishable from the truthful answer. Every downstream
    consequence follows silently -- `_commitments_for` matches nothing, no consistency
    check runs on any question, and round two is free to contradict round one while the
    run reports a clean citation rate and a green result.

    It printed one line to stdout, in a run that emits several hundred.

    Raises:
        ContextUnavailable: If the commitment store could not be read.
    """
    from attestor_platform.config import memory_bank_engine_id
    from attestor_platform.memory import MemoryBankCommitments

    engine_id = memory_bank_engine_id()
    # `for_review` raises ContextUnavailable on its own when Memory Bank cannot be read,
    # which is the behaviour this function exists to preserve. No try/except here: adding
    # one would be the fifth chance to turn "unreachable" back into "no history".
    return MemoryBankCommitments(engine_id=engine_id).for_review(review_id)


def _question_text(report: RunReport, question_id: str | None) -> str:
    """Look up a question's text for the proof file, so a block is readable later."""
    return next(
        (o.question.text for o in report.outcomes if o.question.question_id == question_id),
        "",
    )


def report_numbers(report: RunReport, label: str) -> dict[str, object]:
    total = len(report.outcomes)
    answered = len(report.answered)
    cited = len(report.cited)
    flagged = len(report.flagged_no_evidence)
    blocked = len(report.blocked)
    human = len(report.needs_human)

    by_department: dict[str, int] = {}
    for outcome in report.outcomes:
        key = outcome.question.department.value
        by_department[key] = by_department.get(key, 0) + 1

    numbers: dict[str, object] = {
        "questionnaire": label,
        "questions": total,
        "answered": answered,
        "with_citation": cited,
        "citation_rate": round(cited / total, 4) if total else 0.0,
        "flagged_no_evidence": flagged,
        "armor_blocked": blocked,
        "needs_human": human,
        "triage_seconds": round(report.triage_seconds, 1),
        "draft_seconds": round(report.draft_seconds, 1),
        "total_seconds": round(report.total_seconds, 1),
        "draft_p50_seconds": round(report.latency_percentile(50), 2),
        "draft_p95_seconds": round(report.latency_percentile(95), 2),
        "achieved_concurrency": round(report.achieved_concurrency, 2),
        "by_department": {k: by_department[k] for k in sorted(by_department)},
        "budget": report.budget,
    }
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questionnaire", default="clean", choices=sorted(QUESTIONNAIRES))
    parser.add_argument("--limit", type=int, default=0, help="run only the first N questions")
    parser.add_argument("--review-id", default="rev-acme-2026-q3")
    parser.add_argument("--no-armor", action="store_true", help="skip Model Armor screening")
    parser.add_argument(
        "--orchestrate",
        action="store_true",
        help="drive the run through the Orchestrator (plan, retry judgement, finalise)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="write this run's events to the real audit trail. Off by default because this "
        "harness is a benchmark; on for a run that is meant to be on the record.",
    )
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    path = QUESTIONNAIRES[args.questionnaire]
    print(f"questionnaire: {path.name}")

    questions = parse_xlsx(path)
    if args.limit:
        questions = questions[: args.limit]
    print(f"parsed       : {len(questions)} questions")

    commitments = load_commitments(args.review_id)
    print(f"commitments  : {len(commitments)} from prior rounds")

    guard = None if args.no_armor else ArmorGuard()
    # Null by default. This harness is a benchmark and is run repeatedly; writing every
    # pass into the plane a compliance reader trusts would fill it with rehearsals. `--audit`
    # is for the run that is meant to be on the record -- in particular the orchestrator's
    # three judgement calls, which are made only on this path and which the Review Thread
    # cannot show if they were never written.
    audit = FirestoreAuditSink() if args.audit else NullAuditSink()
    ledger = BudgetLedger(review_id=args.review_id)
    run_id = f"run-{int(time.time())}"

    pipeline = ReviewPipeline(
        review_id=args.review_id,
        run_id=run_id,
        guard=guard,
        audit=audit,
        ledger=ledger,
        prior_commitments=commitments,
    )

    print(f"run_id       : {run_id}")
    print(f"armor        : {'OFF' if args.no_armor else 'ON (ingress + tool output)'}\n")

    orchestration: dict[str, object] | None = None
    if args.orchestrate:
        orchestrator = Orchestrator(pipeline, audit=audit)
        brief = ArtifactBrief(
            filename=path.name,
            question_count=len(questions),
            prior_round_count=1 if commitments else 0,
            prior_commitment_count=len(commitments),
        )
        orchestrated = orchestrator.run(questions, brief)
        report = orchestrated.report
        orchestration = {
            "plan": {
                "pipeline": orchestrated.plan.pipeline,
                "check_consistency": orchestrated.plan.check_consistency,
                "retry_waves": orchestrated.plan.retry_waves,
                "reason": orchestrated.plan.reason,
                "decided_by": orchestrated.plan.decided_by,
            },
            "retried": orchestrated.retried_question_ids,
            "recovered": orchestrated.recovered_question_ids,
            "decision": {
                "release": orchestrated.decision.release,
                "widen": orchestrated.decision.widen,
                "reason": orchestrated.decision.reason,
                "decided_by": orchestrated.decision.decided_by,
                "widened": len(orchestrated.decision.widened_question_ids),
            },
            "turns": orchestrated.turns,
        }
        print("=" * 62)
        print("ORCHESTRATOR")
        print("=" * 62)
        print(json.dumps(orchestration, indent=2))
        print()
    else:
        report = pipeline.run(questions)

    numbers = report_numbers(report, args.questionnaire)
    if orchestration is not None:
        numbers["orchestration"] = orchestration

    print("=" * 62)
    print("MEASURED RESULT")
    print("=" * 62)
    for key in (
        "questions",
        "answered",
        "with_citation",
        "citation_rate",
        "flagged_no_evidence",
        "armor_blocked",
        "needs_human",
    ):
        print(f"  {key:22} {numbers[key]}")
    print()
    for key in (
        "triage_seconds",
        "draft_seconds",
        "total_seconds",
        "draft_p50_seconds",
        "draft_p95_seconds",
        "achieved_concurrency",
    ):
        print(f"  {key:22} {numbers[key]}")
    print(f"\n  by department        {numbers['by_department']}")
    print(f"  budget               {json.dumps(numbers['budget'], indent=2)}")

    # --- the deliberate gaps ---------------------------------------------------------
    print("\n" + "=" * 62)
    print("DELIBERATE EVIDENCE GAPS (must be FLAGGED_NO_EVIDENCE)")
    print("=" * 62)
    gap_results: list[dict[str, object]] = []
    for marker in GAP_MARKERS:
        matches = [o for o in report.outcomes if marker.lower() in o.question.text.lower()]
        for outcome in matches:
            status = outcome.answer.status.value if outcome.answer else "no answer"
            correct = outcome.answer is not None and (
                outcome.answer.status is AnswerStatus.FLAGGED_NO_EVIDENCE
            )
            print(f"  {'OK  ' if correct else 'MISS'}  {marker:28} -> {status}")
            gap_results.append({"marker": marker, "status": status, "correct": correct})
    numbers["gap_checks"] = gap_results

    # --- armor blocks ----------------------------------------------------------------
    if report.blocked:
        print("\n" + "=" * 62)
        print("MODEL ARMOR BLOCKS")
        print("=" * 62)
        for outcome in report.blocked:
            events = [
                e
                for e in getattr(audit, "events", ())
                if e["question_id"] == outcome.question.question_id
            ]
            armor = [e for e in events if e["kind"] == "armor_blocked"]
            for event in armor:
                detail = event["detail"]
                print(f"  question {outcome.question.question_id}")
                print(f"    surface       : {detail.get('surface')}")
                print(f"    decision      : {detail.get('decision')}")
                print(f"    filters       : {detail.get('matched_filters')}")
                print(f"    chunk_index   : {detail.get('chunk_index')}")
                excerpt = detail.get("excerpt") or ""
                print(f"    excerpt       : {excerpt[:120]}")

    # --- consistency -----------------------------------------------------------------
    constrained = [o for o in report.outcomes if o.constrained]
    if constrained:
        print("\n" + "=" * 62)
        print("CONSISTENCY CHECKS THAT CONSTRAINED AN ANSWER")
        print("=" * 62)
        for outcome in constrained:
            print(f"  {outcome.question.text[:70]}")
            print(f"    verdict     : {outcome.contradiction.value}")
            print(f"    constrained : {outcome.constrained}")
            if outcome.answer:
                print(f"    answer      : {outcome.answer.text[:150]}")

    numbers["armor_blocks"] = [
        {
            "question_id": e["question_id"],
            "question": next(
                (
                    o.question.text
                    for o in report.outcomes
                    if o.question.question_id == e["question_id"]
                ),
                "",
            ),
            **e["detail"],
        }
        # `NullAuditSink` keeps every event in memory, which is what this report reads.
        # `FirestoreAuditSink` writes them out and keeps nothing, so under `--audit` the
        # blocked-input detail comes back empty rather than crashing the summary of a run
        # that has already finished and already written its trail.
        for e in getattr(audit, "events", ())
        if e["kind"] == "armor_blocked"
    ]
    numbers["errors"] = [
        {"question_id": o.question.question_id, "error": o.error}
        for o in report.outcomes
        if o.error
    ]
    numbers["audit_events"] = len(getattr(audit, "events", ()))

    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / f"run-{args.questionnaire}.json"
        out.write_text(json.dumps(numbers, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {out}")

        chain_out = PROOF_DIR / f"audit-chain-{args.questionnaire}.json"
        sample = next((o for o in report.cited), None)
        # The chain artefact is read back out of the in-memory sink. Under `--audit` the
        # events went to Firestore and this process kept none of them, so the file is
        # skipped and said to be skipped -- writing an empty chain under the name of a
        # verified one is exactly the failure this project keeps finding.
        chain_source = getattr(audit, "for_question", None)
        if sample is None or chain_source is None:
            if sample is not None:
                print(f"skipped {chain_out} -- events went to the audit trail, not to memory")
        else:
            chain = chain_source(sample.question.question_id)
            chain_out.write_text(
                json.dumps(
                    {"question": sample.question.text, "events": chain}, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
            print(f"wrote {chain_out} ({len(chain)} events)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
