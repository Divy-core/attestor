#!/usr/bin/env python3
"""Deploy the Attestor fleet to Agent Runtime — one engine per agent, one identity each.

    PROJECT_ID=attestor-505506 uv run python services/runtime/deploy_fleet.py --all
    PROJECT_ID=attestor-505506 uv run python services/runtime/deploy_fleet.py --role security
    PROJECT_ID=attestor-505506 uv run python services/runtime/deploy_fleet.py --list

Five separate `reasoningEngine` resources rather than one engine with nested sub-agents,
because Agent Registry catalogues engines and — the part that actually matters — nested
sub-agents share a single Agent Identity. One identity means one service account means the
union of every department's permissions on one credential, which is the least-privilege
violation this fleet exists to avoid.

Deploying takes several minutes per engine, so `--all` runs them one at a time and prints
each resource name as it lands. Nothing here is idempotent by accident: `--reuse` attaches
to an existing engine with the same display name instead of creating a second billable
copy.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import agentplatform
from agentplatform.agent_engines import AdkApp

# NOT `from app import ...` -- see the module docstring of fleet_runtime and
# tools/check_layering.py::BANNED_BUNDLE_FILENAMES.
from fleet_runtime import ROLES, build_agent

ROOT = Path(__file__).parent.parent.parent
PROOF = ROOT / "docs" / "proof" / "fleet-deployment.json"

#: Pinned, not ranged. A floating range resolves differently at bundle time than it did
#: locally, and the agent object is cloudpickled against the local versions. cloudpickle
#: and pydantic are not optional -- the SDK validates that both appear here.
REQUIREMENTS = [
    "google-adk==2.7.0",
    "google-cloud-aiplatform[agent-engines]==1.164.0",
    "google-cloud-firestore>=2.19",
    "google-cloud-storage>=2.18",
    "google-cloud-discoveryengine>=0.13",
    "cloudpickle==3.1.2",
    "pydantic==2.13.4",
]

#: What goes into the bundle, and where it lives in this repo. The KEY is the name the
#: bundle must expose at its top level; the VALUE is where to copy it from.
#:
#: `extra_packages` preserves the path it is given, so passing
#: "services/runtime/fleet_runtime.py" put the module at that path inside the bundle and
#: the engine died at startup with `No module named 'fleet_runtime'` -- behind the generic
#: "failed to start and cannot serve traffic", which names none of it. The fix is to stage
#: a FLAT directory whose entries are already importable, and deploy from there.
BUNDLE_CONTENTS: dict[str, str] = {
    "fleet_runtime.py": "services/runtime/fleet_runtime.py",
    "attestor_core": "packages/attestor-core/src/attestor_core",
    "attestor_platform": "packages/attestor-platform/src/attestor_platform",
    "attestor_fleet": "packages/attestor-fleet/src/attestor_fleet",
}

#: Agent Identity, passed as the documented string rather than importing the enum from a
#: private `_genai` module that is free to move between patch releases. Setting this means
#: `service_account` must NOT be set -- the two are mutually exclusive and setting both is
#: a deploy error.
IDENTITY_TYPE = "AGENT_IDENTITY"


def stage_bundle() -> Path:
    """Copy the bundle contents into a flat directory and return it.

    Flat because the engine imports them by bare name. `__pycache__` is excluded: stale
    bytecode compiled against a different Python would ship alongside the source.
    """
    staging = Path(tempfile.mkdtemp(prefix="attestor-bundle-"))
    for name, source in BUNDLE_CONTENTS.items():
        src = ROOT / source
        dst = staging / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    return staging


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"error: {name} must be set (no hardcoded project id in this repo)")
    return value


def display_name(role: str) -> str:
    return f"attestor-{role}"


def deploy_one(client: Any, role: str, staging_bucket: str, *, reuse: bool) -> dict[str, Any]:
    """Deploy one role. Returns the record written to the proof file."""
    name = display_name(role)

    if reuse:
        for existing in client.agent_engines.list():
            if getattr(existing, "display_name", None) == name:
                resource = existing.api_resource.name
                print(f"  {role:14} REUSED   {resource}")
                return {
                    "role": role,
                    "display_name": name,
                    "resource_name": resource,
                    "created": False,
                }

    agent = build_agent(role)
    # enable_tracing is what puts spans into Cloud Trace. Without it the span-tree exit
    # criterion cannot pass, and it is one flag.
    app = AdkApp(agent=agent, enable_tracing=True)

    config: dict[str, Any] = {
        "display_name": name,
        "description": f"Attestor fleet: {role} agent.",
        "staging_bucket": staging_bucket,
        "requirements": REQUIREMENTS,
        "extra_packages": sorted(BUNDLE_CONTENTS),
        "identity_type": IDENTITY_TYPE,
        "env_vars": {
            "PROJECT_ID": os.environ["PROJECT_ID"],
            "AGENT_ROLE": role,
        },
        # Scale to zero. An idle Agent Runtime is one of the two ways to burn the credit
        # budget, and five engines multiply that by five.
        "min_instances": 0,
        "max_instances": 1,
    }

    started = time.perf_counter()
    engine = client.agent_engines.create(agent=app, config=config)
    elapsed = time.perf_counter() - started
    resource = engine.api_resource.name
    print(f"  {role:14} DEPLOYED {resource}  ({elapsed:.0f}s)")
    return {
        "role": role,
        "display_name": name,
        "resource_name": resource,
        "created": True,
        "seconds": round(elapsed, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=ROLES, help="deploy one role")
    parser.add_argument("--all", action="store_true", help="deploy every role in turn")
    parser.add_argument("--list", action="store_true", help="list deployed engines and exit")
    parser.add_argument(
        "--reuse", action="store_true", help="attach to an existing engine of the same name"
    )
    args = parser.parse_args()

    project = _require_env("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    staging_bucket = os.environ.get("STAGING_BUCKET", f"gs://{project}-staging")

    client = agentplatform.Client(project=project, location=region)

    if args.list:
        print(f"{'display name':28} resource")
        for engine in client.agent_engines.list():
            print(f"  {getattr(engine, 'display_name', '?'):26} {engine.api_resource.name}")
        return 0

    roles = list(ROLES) if args.all else ([args.role] if args.role else [])
    if not roles:
        parser.error("pass --role, --all, or --list")

    print(f"project        : {project}")
    print(f"region         : {region}")
    print(f"identity_type  : {IDENTITY_TYPE}")
    print(f"roles          : {', '.join(roles)}\n")

    bundle = stage_bundle()
    print(f"staged bundle  : {bundle}")
    print(f"bundle contents: {', '.join(sorted(BUNDLE_CONTENTS))}\n")
    previous = Path.cwd()
    try:
        # extra_packages paths are resolved relative to the CWD, so the deploy runs from
        # inside the staged bundle and passes bare names.
        os.chdir(bundle)
        records = [deploy_one(client, r, staging_bucket, reuse=args.reuse) for r in roles]
    finally:
        os.chdir(previous)
        shutil.rmtree(bundle, ignore_errors=True)

    PROOF.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(PROOF.read_text(encoding="utf-8")) if PROOF.exists() else {}
    by_role = {r["role"]: r for r in existing.get("engines", [])}
    by_role.update({r["role"]: r for r in records})
    PROOF.write_text(
        json.dumps(
            {
                "project": project,
                "region": region,
                "identity_type": IDENTITY_TYPE,
                "engines": [by_role[r] for r in ROLES if r in by_role],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {PROOF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
