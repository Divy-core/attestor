#!/usr/bin/env python3
"""Prove that round 2 cannot quietly contradict round 1.

    PROJECT_ID=attestor-505506 uv run python tools/verify_consistency.py --write-proof

The seeded review committed in round 1, 23 days ago, that Kestrel offers no on-premises
or self-hosted deployment. Round 2 asks:

    "Our regulated business unit cannot use multi-tenant SaaS. Please describe the
     self-hosted or on-premises deployment options available for regulated customers,
     including any private-cloud or customer-VPC installation, and the timeline to
     provision one."

It shares almost no words with the round-1 question and has a completely different
content-derived id, which is the whole difficulty: id matching finds nothing, so the
commitment has to be found by meaning. What this harness checks, in order:

1. the commitment is **matched** to the reframed question at all;
2. `consistency_checked` fires with a contradiction verdict on the first draft;
3. the answer is **redrafted under the commitment** and the final text honours it;
4. `constrained=true` and the answer is held for a human.

Step 3 is the one that matters. Detecting a contradiction and shipping it with a warning
label would not be the product.

## `--deployed`

The same four checks, with retrieval and drafting executed on the deployed department
engine under its own Agent Identity instead of in this process. Nothing else changes: the
consistency check and the constrained redraft are dispatcher-side compliance controls in
production too (see the table in `dispatcher/remote.py`), so what this exercises is the
real deployed arrangement rather than a second implementation of it.

Worth running as its own case rather than assuming the local result carries over, because
the deployed path drafts under a *different instruction*. The engine carries its own
department prompt, pickled into the artifact; the local path is handed retrieved evidence
and a prompt this repo controls. A contradiction the local drafter walks into is not
guaranteed to be one the engine walks into, and the interesting question is whether the
machinery still catches it when it does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from _corpus_fixture import plant, remove, wait_until_retrievable
from attestor_core.domain import ContradictionVerdict, Department, Question
from attestor_fleet.callbacks.audit import NullAuditSink
from attestor_fleet.callbacks.budget import BudgetLedger
from attestor_fleet.pipeline import COMMITMENT_MATCH_SCORE, ReviewPipeline

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"
REVIEW_ID = "rev-acme-2026-q3"

#: The round-2 question that invites the contradiction, verbatim from the seeded sheet.
THE_QUESTION = (
    "Our regulated business unit cannot use multi-tenant SaaS. Please describe the "
    "self-hosted or on-premises deployment options available for regulated customers, "
    "including any private-cloud or customer-VPC installation, and the timeline to "
    "provision one."
)

#: A control: touches no commitment, so nothing should be constrained.
CONTROL_QUESTION = "What is your scheduled maintenance window?"

#: Words whose presence in the final answer would mean the commitment was not honoured.
_OFFER_MARKERS = ("we offer", "is available", "we can provide", "we do provide", "we support")
#: Words that show the commitment was honoured.
_REFUSAL_MARKERS = ("does not offer", "do not offer", "not offered", "no on-premises", "saas only")


def load_commitments() -> list[tuple[str, str]]:
    """Prior commitments **from Memory Bank**, which is canonical from Phase 4.

    This read is the point of the exercise as much as the contradiction check is. The
    Phase 3 result was produced against Firestore; if it only survives because Firestore
    is still there, the Memory Bank claim is decoration. So the harness reads the
    canonical store and nothing else — and if Memory Bank is unreachable this raises
    rather than reporting that the customer has no history, which would silently turn
    this whole test into a no-op that passes.
    """
    from attestor_platform.config import memory_bank_engine_id
    from attestor_platform.memory import MemoryBankCommitments

    return MemoryBankCommitments(engine_id=memory_bank_engine_id()).for_review(REVIEW_ID)


#: The fault injection. A product-update note stating that the very thing round 1 ruled
#: out is now available -- which is how this failure actually happens in a real company:
#: documentation moves ahead of, or simply forgets, what a customer was told in writing
#: three weeks ago. Nothing in the prompt is asked to contradict anything; the corpus is
#: changed underneath the agent and the agent is asked the customer's question.
DRIFT_DOCUMENT = """\
# Deployment Options Update — Regulated Customers

**Document ID:** KD-ENG-098 | **Version:** 1.0 | **Owner:** Dana Whitfield, VP Engineering
**Approved:** 2026-08-05 | **Framework mapping:** CAIQ IVS-01

## 1. Summary

Following demand from regulated customers, Kestrel Insight is now available as a
**single-tenant deployment** in addition to the standard multi-tenant SaaS offering.

## 2. Available deployment models

| Model | Availability | Provisioning time |
|---|---|---|
| Multi-tenant SaaS (standard) | General availability | Immediate |
| Single-tenant dedicated instance | General availability | 15 business days |
| Customer-VPC installation | General availability | 20 business days |
| On-premises / self-hosted | General availability | 30 business days |

## 3. Regulated customer programme

Customers in regulated sectors may request an on-premises or customer-VPC installation
through their account team. The deployment is provisioned by Kestrel professional services
and the customer operates the infrastructure thereafter.

## 4. Support

Single-tenant and on-premises deployments are supported under the standard support terms,
with the exception that availability commitments apply to the customer's own
infrastructure rather than to Kestrel's.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    parser.add_argument(
        "--inject-drift",
        action="store_true",
        help="plant a corpus document that contradicts the round-1 commitment, then remove it",
    )
    parser.add_argument(
        "--deployed",
        action="store_true",
        help="retrieve and draft on the deployed department engine instead of in-process",
    )
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    project = os.environ["PROJECT_ID"]
    planted_uri = ""
    if args.inject_drift:
        print("=" * 72)
        print("FAULT INJECTION -- planting a corpus document that contradicts round 1")
        print("=" * 72)
        planted_uri = plant(
            project, Department.ENGINEERING, "deployment-options-update", DRIFT_DOCUMENT
        )
        if not wait_until_retrievable(Department.ENGINEERING, THE_QUESTION, planted_uri):
            remove(project, Department.ENGINEERING, planted_uri)
            sys.exit("error: planted document never became retrievable; nothing was proven")
        print()

    try:
        return _run(args, planted_uri)
    finally:
        if planted_uri:
            removed = remove(project, Department.ENGINEERING, planted_uri)
            print(f"\n  cleanup       : removed {len(removed)} object(s)")
            for name in removed:
                print(f"    {name}")


def _run(args: argparse.Namespace, planted_uri: str) -> int:
    commitments = load_commitments()
    print("=" * 72)
    print("FOLLOW-UP CONSISTENCY -- round 2 against a 23-day-old commitment")
    print("=" * 72)
    print(f"  commitments on file : {len(commitments)} (source: Memory Bank)")
    for _, statement in commitments:
        print(f"    - {statement[:88]}")

    audit = NullAuditSink()
    # The deployed path is a subclass overriding exactly two methods, so every assertion
    # below is checking the same code on the same objects -- which is the only way the two
    # results are comparable at all.
    factory: type[ReviewPipeline] = ReviewPipeline
    if args.deployed:
        from dispatcher.remote import RemoteDraftingPipeline

        factory = RemoteDraftingPipeline
    pipeline = factory(
        review_id=REVIEW_ID,
        run_id=f"verify-consistency-{int(time.time())}",
        guard=None,
        audit=audit,
        ledger=BudgetLedger(review_id=REVIEW_ID),
        prior_commitments=commitments,
    )
    print(f"  execution           : {'deployed engines' if args.deployed else 'in-process'}")

    question = Question.from_text(THE_QUESTION, department=Department.ENGINEERING)
    control = Question.from_text(CONTROL_QUESTION, department=Department.ENGINEERING)

    # --- 1. is the commitment found at all? ---------------------------------------------
    matched = pipeline._commitments_for(question)
    print(f"\n  match threshold     : {COMMITMENT_MATCH_SCORE}")
    print(f"  commitments matched : {len(matched)}")
    for statement in matched:
        print(f"    - {statement[:88]}")
    by_id = sum(1 for q, _ in commitments if q == question.question_id)
    print(f"  id-only match would have found: {by_id}")

    # --- 2-4. run the question through the real pipeline ---------------------------------
    outcome = pipeline.draft(question)
    control_outcome = pipeline.draft(control)

    checks = [e for e in audit.events if e["kind"] == "consistency_checked"]
    initial = [e for e in checks if e["detail"].get("pass") == "initial"]
    redrafts = [
        e
        for e in audit.events
        if e["kind"] == "answer_drafted" and e["detail"].get("redraft") is True
    ]

    print("\n  consistency_checked events:")
    for event in checks:
        detail = event["detail"]
        print(
            f"    pass={detail.get('pass'):12} verdict={detail.get('verdict'):24} "
            f"constrained={detail.get('constrained')}"
        )
        print(f"      justification: {str(detail.get('justification'))[:96]}")

    answer = outcome.answer
    text = answer.text if answer else ""
    lowered = text.lower()

    print(f"\n  redrafted under constraint : {bool(redrafts)}")
    if redrafts:
        print(f"    superseded draft : {str(redrafts[0]['detail'].get('superseded_text'))[:160]}")
    print(f"\n  FINAL ANSWER:\n    {text[:600]}")
    print(f"\n  status              : {answer.status.value if answer else 'none'}")
    print(f"  confidence          : {answer.confidence.value if answer else 'none'}")
    print(f"  citations           : {len(answer.citations) if answer else 0}")
    print(f"  contradiction       : {outcome.contradiction.value}")
    print(f"  constrained         : {outcome.constrained}")
    print(f"  needs_human         : {outcome.needs_human}")

    honoured = any(marker in lowered for marker in _REFUSAL_MARKERS)
    offered = any(marker in lowered for marker in _OFFER_MARKERS) and not honoured

    print(f"\n  control question    : {CONTROL_QUESTION}")
    print(f"    constrained       : {control_outcome.constrained} (must be False)")

    contradiction_seen = bool(initial) and initial[0]["detail"]["verdict"] in {
        ContradictionVerdict.CONTRADICTION.value,
        ContradictionVerdict.POSSIBLE_CONTRADICTION.value,
    }

    # What counts as a pass differs by mode, and deliberately so.
    #
    # Natural mode asks: does the machinery engage on a REFRAMED question, and does the
    # answer honour the commitment? It does NOT require a contradiction, because whether
    # the first draft contradicts is the model's behaviour against an honest corpus, not
    # something to be engineered. Measured: it does not contradict. Requiring one here
    # would mean weakening the corpus until the demo looked better.
    #
    # Drift mode asks the harder question: with the corpus itself now asserting the
    # opposite of what the customer was told, is the contradiction caught, is the answer
    # redrafted under the commitment, and does the final text still honour round 1?
    common = (
        bool(matched)
        and bool(checks)
        and honoured
        and not offered
        and outcome.needs_human
        and not control_outcome.constrained
    )
    if planted_uri:
        passed = common and contradiction_seen and bool(redrafts) and outcome.constrained
    else:
        passed = common and not contradiction_seen

    print(f"\n  mode                : {'fault injection (drift)' if planted_uri else 'natural'}")
    print(f"  RESULT              : {'PASS' if passed else 'FAIL'}")

    result: dict[str, Any] = {
        "case": "followup_consistency",
        "mode": "drift_injection" if planted_uri else "natural",
        "execution": (
            "deployed department engines under Agent Identity" if args.deployed else "in-process"
        ),
        "planted_document": planted_uri or None,
        "pass": passed,
        "contradiction_detected": contradiction_seen,
        "question": THE_QUESTION,
        "match_threshold": COMMITMENT_MATCH_SCORE,
        "commitments_on_file": len(commitments),
        "commitment_source": "memory_bank",
        "commitments_matched": matched,
        "matched_by_id_only": sum(1 for q, _ in commitments if q == question.question_id),
        "consistency_events": checks,
        "redrafted": bool(redrafts),
        "superseded_draft": (redrafts[0]["detail"].get("superseded_text") if redrafts else None),
        "final_answer": text,
        "final_verdict": outcome.contradiction.value,
        "constrained": outcome.constrained,
        "needs_human": outcome.needs_human,
        "commitment_honoured": honoured,
        "status": answer.status.value if answer else None,
        "confidence": answer.confidence.value if answer else None,
        "citations": len(answer.citations) if answer else 0,
        "control_question_constrained": control_outcome.constrained,
        "budget": pipeline.ledger.summary(),
    }

    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "drift" if planted_uri else "natural"
        # A separate file rather than overwriting the local result: the two are different
        # claims and the local one is a Phase 3 artefact the write-up already cites.
        where = "-deployed" if args.deployed else ""
        out = PROOF_DIR / f"consistency-followup-{suffix}{where}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
