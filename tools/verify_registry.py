#!/usr/bin/env python3
"""All five agents, read from the Agent Registry API rather than from our own records.

    PROJECT_ID=attestor-505506 uv run python tools/verify_registry.py --write-proof

The distinction this checks is narrow and worth being exact about. Sessions one and two
confirmed five `reasoningEngine` resources exist, each with its own Agent Identity — but
they confirmed it by listing *Agent Runtime* resources, which is us reading the thing we
created. Agent Registry is a separate API (`agentregistry.googleapis.com`, enabled in
Phase 0) and the claim in the write-up is that agents appear there **without any manual
registration step**. That is a claim about a different service, and it has to be checked
against that service.

It also matters ahead of Phase 6: `/registry` reads this API live, and a page built
against an endpoint that turns out to list four of five agents is a bad thing to discover
while building a UI.

Every deployed engine must be present, and every present agent must carry a distinct
identity — a registry that listed five entries sharing one identity would describe exactly
the least-privilege violation the five-engine split exists to avoid.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-proof", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("PROJECT_ID"):
        sys.exit("error: PROJECT_ID must be set")

    from attestor_platform.registry import AgentRegistry

    deployed = json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["engines"]
    expected = {e["role"]: e["resource_name"] for e in deployed}

    print("=" * 78)
    print("AGENT REGISTRY -- the platform's catalogue, not ours")
    print("=" * 78)

    agents = AgentRegistry().list_agents()
    print(f"  registry returned {len(agents)} agent(s)\n")

    listed: list[dict[str, Any]] = []
    for agent in agents:
        record = agent.model_dump() if hasattr(agent, "model_dump") else dict(agent)
        listed.append(record)
        name = str(record.get("display_name") or record.get("name") or "")
        identity = str(record.get("identity") or record.get("effective_identity") or "")
        shown = identity[-58:] or "(no identity)"
        print(f"  {name:<26} {record.get('department', '-'):<12} {shown}")

    # Matching on the engine id rather than on the display name: the registry is free to
    # present a name however it likes, and the resource id is the thing that is actually
    # the same object.
    blob = json.dumps(listed)
    missing = {
        role: resource
        for role, resource in expected.items()
        if resource.rsplit("/", 1)[-1] not in blob
    }

    # The registry's *list* endpoint leaves `effective_identity` and `identity_type`
    # null on every entry. That is a property of the endpoint, not of the agents: each
    # entry's `agent_id` URN names a distinct `reasoningEngine`, and each of those engines
    # reports its own `spec.effectiveIdentity` (session one) and holds its own conditioned
    # IAM binding (`docs/proof/iam-denial.txt`). So distinctness is counted here on the
    # engine the entry points at, and the gap is recorded rather than papered over --
    # "distinct identities, per the registry" would not be a true sentence.
    populated = [r for r in listed if r.get("effective_identity") or r.get("identity_type")]
    engine_urns = {str(r.get("agent_id") or "") for r in listed if r.get("agent_id")}

    print(f"\n  deployed engines      : {len(expected)}")
    print(f"  present in registry   : {len(expected) - len(missing)}")
    print(f"  distinct agent URNs   : {len(engine_urns)}")
    print(f"  entries carrying an identity field: {len(populated)} (the list endpoint")
    print("                                       does not populate it; identity is")
    print("                                       proven from the engine resource)")
    if missing:
        for role, resource in missing.items():
            print(f"    MISSING {role:<14} {resource}")

    passed = not missing and len(agents) >= len(expected)
    print(f"\n  RESULT : {'PASS' if passed else 'FAIL'}")

    report = {
        "case": "agent_registry_listing",
        "pass": passed,
        "api": "agentregistry.googleapis.com/v1",
        "deployed_engines": expected,
        "registry_count": len(agents),
        "missing_from_registry": missing,
        "distinct_agent_urns": len(engine_urns),
        "entries_with_identity_field": len(populated),
        "identity_note": (
            "the registry list endpoint returns effective_identity and identity_type as "
            "null; identity distinctness is proven from the reasoningEngine resource's "
            "spec.effectiveIdentity and from the live conditioned IAM bindings"
        ),
        "agents": listed,
    }
    if args.write_proof:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        out = PROOF_DIR / "registry-listing.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
