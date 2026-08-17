#!/usr/bin/env python3
"""What each observability plane actually contains, measured rather than claimed.

    PROJECT_ID=attestor-505506 uv run python tools/capture_traces.py --review rev-... \\
        --write-proof

Attestor keeps two observability planes on purpose, and the whole point of the split is
that they answer different questions:

* **Cloud Trace** — engineering. How long did this take, what called what, where did the
  latency go. Roughly 30-day retention. Nobody audits from it.
* **`audit_events` in Firestore** — compliance. *Why did we answer yes to Q112?* Immutable,
  queryable, exportable, and expected to still answer that question in six months.

Conflating them is the common mistake, and claiming both when only one exists is worse.
So this tool reports what is genuinely in each for one review, including the gaps.

## What is instrumented, and what is not

Stated up front because the honest answer is mixed:

* The **deployed engines** carry `enable_tracing=True`, so Agent Runtime emits spans for
  the drafting calls itself.
* **Cloud Run** emits a request span per push delivery.
* Our own code emits **no custom OTel spans** — `attestor_platform.telemetry` contains the
  audit writer and nothing else. So there is no hand-rolled
  `orchestrator → pipeline → parallel agents → tools` span tree of our own making; what
  Cloud Trace holds is what the platform produced.

The compliance plane is the one this system actually leans on, and it is complete: every
stage, every retrieval, every Armor verdict, every denial, every human decision.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PROOF_DIR = ROOT / "docs" / "proof"


def fetch_traces(project: str, minutes: int) -> list[dict[str, Any]]:
    """List recent traces with their spans, via the Cloud Trace v1 API."""
    import google.auth
    import google.auth.transport.requests
    import urllib.parse
    import urllib.request

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    start = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    query = urllib.parse.urlencode(
        {"startTime": start, "view": "COMPLETE", "pageSize": "200"}
    )
    url = f"https://cloudtrace.googleapis.com/v1/projects/{project}/traces?{query}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {credentials.token}"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("traces") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True)
    parser.add_argument("--minutes", type=int, default=90)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("error: PROJECT_ID must be set")

    from attestor_platform.firestore import AuditEventRepository

    print("=" * 78)
    print("TWO OBSERVABILITY PLANES")
    print("=" * 78)

    # -- the compliance plane ---------------------------------------------------------
    events = AuditEventRepository().for_review(args.review, limit=2000)
    kinds = Counter(str(e.get("kind")) for e in events)
    actors = Counter(str(e.get("actor")) for e in events)
    stages = Counter(
        str((e.get("detail") or {}).get("stage"))
        for e in events
        if (e.get("detail") or {}).get("stage")
    )

    print(f"\n  COMPLIANCE PLANE -- audit_events, review {args.review}")
    print(f"  {'-' * 66}")
    print(f"  events : {len(events)}")
    for kind, count in kinds.most_common():
        print(f"    {kind:<26} {count}")
    print("  actors :")
    for actor, count in actors.most_common(8):
        print(f"    {actor:<26} {count}")

    # -- the engineering plane --------------------------------------------------------
    try:
        traces = fetch_traces(project, args.minutes)
        trace_error = None
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        traces, trace_error = [], f"{type(exc).__name__}: {exc}"

    span_names: Counter[str] = Counter()
    for trace in traces:
        for span in trace.get("spans") or []:
            span_names[str(span.get("name"))] += 1

    print(f"\n  ENGINEERING PLANE -- Cloud Trace, last {args.minutes} minutes")
    print(f"  {'-' * 66}")
    if trace_error:
        print(f"  could not read: {trace_error}")
    print(f"  traces : {len(traces)}")
    print(f"  spans  : {sum(span_names.values())}")
    for name, count in span_names.most_common(15):
        print(f"    {name[:58]:<58} {count}")

    deepest = max(
        ((len(t.get("spans") or []), t.get("traceId")) for t in traces),
        default=(0, None),
    )
    print(f"\n  deepest trace : {deepest[0]} span(s)  id={deepest[1]}")

    print("\n  The two planes are distinguishable by construction:")
    print("    a permission denial is a COMPLIANCE event and is written to audit_events")
    print("    a slow retrieval is an ENGINEERING fact and is a Cloud Trace span")
    print("  Our own code emits no custom OTel spans; the spans above are the platform's.")

    report = {
        "case": "two_observability_planes",
        "review_id": args.review,
        "compliance_plane": {
            "store": "Firestore audit_events (append-only)",
            "events": len(events),
            "by_kind": dict(kinds),
            "by_actor": dict(actors),
            "by_stage": dict(stages),
            "answers": "why did we answer yes to Q112, six months from now",
        },
        "engineering_plane": {
            "store": "Cloud Trace",
            "window_minutes": args.minutes,
            "traces": len(traces),
            "spans": sum(span_names.values()),
            "span_names": dict(span_names.most_common(30)),
            "deepest_trace_spans": deepest[0],
            "deepest_trace_id": deepest[1],
            "error": trace_error,
            "instrumentation": (
                "engines deployed with enable_tracing=True; Cloud Run request spans; "
                "no custom OTel spans in attestor code"
            ),
            "answers": "where did the latency go, what called what",
        },
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "observability-planes.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
