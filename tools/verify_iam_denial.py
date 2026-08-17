#!/usr/bin/env python3
"""Prove the platform layer refuses, by asking a deployed engine to cross the line.

    PROJECT_ID=attestor-505506 uv run python tools/verify_iam_denial.py --write-proof

Phase 3 proved the **policy** layer refuses: `enforce_tool_policy` raises when an agent's
department does not match the datastore its search object is bound to
(`docs/proof/defence-denial.json`). That is our code refusing.

This proves the layer underneath, and it answers a harder question: *what if our code is
bypassed?* The deployed `attestor-security` engine carries one tool that is a deliberate
bypass — `probe_platform_boundary` takes an arbitrary `gs://` path and asks the platform
for it, with no department binding in the way. Both directions are checked, because only
the pair is evidence:

* its **own** prefix must succeed — otherwise the "denial" is just a broken deployment;
* the **legal** prefix must fail — with the platform's verbatim words, not ours.

The engine's Agent Identity is the principal being refused. Nothing in this harness holds
that credential; the engine does, and the engine is what runs the read.
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

#: One object the security engine is granted, one it is not. Real corpus objects, so a
#: failure cannot be blamed on a missing file.
ALLOWED = "gs://{bucket}/security/access-control-standard.txt"
FORBIDDEN = "gs://{bucket}/legal/data-processing-agreement.txt"


def engine_for(role: str) -> str:
    if not DEPLOYMENT.exists():
        sys.exit(f"error: {DEPLOYMENT} not found -- deploy the fleet first")
    engines = json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["engines"]
    for record in engines:
        if record["role"] == role:
            return str(record["resource_name"])
    sys.exit(f"error: no deployed engine for role {role!r}")


def ask(engine: Any, prompt: str) -> str:
    """Send one turn and return the concatenated text the engine produced."""
    chunks: list[str] = []
    for event in engine.stream_query(message=prompt, user_id="iam-denial-harness"):
        payload = event if isinstance(event, dict) else {}
        for part in (payload.get("content") or {}).get("parts") or []:
            if isinstance(part.get("text"), str):
                chunks.append(part["text"])
            # The tool's own return value is the evidence, not the model's paraphrase.
            response = part.get("function_response") or {}
            if response:
                chunks.append(json.dumps(response.get("response", response)))
    return "\n".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("error: PROJECT_ID must be set")
    bucket = f"{project}-corpus"

    import agentplatform

    resource = engine_for("security")
    client = agentplatform.Client(project=project, location="us-central1")
    engine = client.agent_engines.get(name=resource)

    print("=" * 78)
    print("PLATFORM-LAYER DENIAL -- a deployed engine, its own identity, no interceptor")
    print("=" * 78)
    print(f"  engine : {resource}")
    print(f"  bucket : gs://{bucket}\n")

    results: dict[str, Any] = {}
    for label, template in (("allowed", ALLOWED), ("forbidden", FORBIDDEN)):
        uri = template.format(bucket=bucket)
        print(f"  {label:10} {uri}")
        answer = ask(
            engine,
            f"Call probe_platform_boundary with gcs_uri='{uri}' and report the raw "
            "result verbatim, including any error text.",
        )
        results[label] = {"gcs_uri": uri, "response": answer}
        condensed = " ".join(answer.split())
        print(f"             {condensed[:220]}\n")

    allowed_ok = (
        '"allowed": true' in results["allowed"]["response"].lower().replace("'", '"')
        or "allowed=true" in results["allowed"]["response"].lower()
    )
    denied_text = results["forbidden"]["response"].lower()
    denied_ok = any(
        marker in denied_text
        for marker in ("403", "permission", "denied", "forbidden", "does not have")
    )

    passed = allowed_ok and denied_ok
    print(f"  own prefix readable   : {allowed_ok}")
    print(f"  legal prefix refused  : {denied_ok}")
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")

    report = {
        "case": "platform_layer_iam_denial",
        "pass": passed,
        "engine": resource,
        "bucket": bucket,
        "own_prefix_readable": allowed_ok,
        "other_prefix_refused": denied_ok,
        "probes": results,
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "iam-runtime-denial.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
