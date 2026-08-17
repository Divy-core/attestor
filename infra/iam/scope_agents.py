#!/usr/bin/env python3
"""Scope each deployed agent's identity to its own corpus prefix, at the IAM layer.

    PROJECT_ID=attestor-505506 uv run python infra/iam/scope_agents.py --apply
    PROJECT_ID=attestor-505506 uv run python infra/iam/scope_agents.py --show

Phase 3 proved the **policy** layer denies: `enforce_tool_policy` refuses when an agent's
department does not match the datastore its search object is bound to, and that refusal is
an exception with an audit event behind it (`docs/proof/defence-denial.json`).

This is the second layer, and it answers a different question: *what happens if the
interceptor is bypassed?* A prompt cannot talk its way past IAM, and neither can a bug in
our own callback wiring. `SecurityAgent` must be unable to read `corpus/legal/**` because
the **credential is refused**, independently of anything the agent code does.

## The principal

Deploying with `identity_type=AGENT_IDENTITY` gives each engine its own cryptographic
identity, readable from the resource:

    spec.effectiveIdentity =
      agents.global.proj-906988347581.system.id.goog/resources/aiplatform
      /projects/906988347581/locations/us-central1/reasoningEngines/3145088689823023104

Prefixed with `principal://`, that is bindable in an IAM policy. Five engines means five
distinct principals, which is the entire reason the fleet deploys as five engines rather
than one with nested sub-agents — nested agents would share a single identity, and a
single identity would need the union of every department's permissions.

## The grant

Each department engine gets `roles/storage.objectViewer` on the corpus bucket, **with an
IAM Condition** restricting it to objects under its own prefix. The other prefixes are not
denied explicitly; they are simply never granted, which is the stronger form — there is no
deny rule to misconfigure, only an allow that does not reach.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
DEPLOYMENT = ROOT / "docs" / "proof" / "fleet-deployment.json"
PROOF = ROOT / "docs" / "proof" / "iam-scoping.json"

#: Which corpus prefix each role may read. Roles absent from this map get no corpus
#: access at all -- the orchestrator judges and does not retrieve.
CORPUS_PREFIX: dict[str, str] = {
    "security": "security/",
    "legal": "legal/",
    "engineering": "engineering/",
}

#: The shared evidence agent is the one legitimate cross-department reader, and it is
#: scoped by the `department` argument its tool takes rather than by IAM. Recorded so the
#: asymmetry is a decision rather than an oversight.
UNRESTRICTED_ROLES = frozenset({"evidence"})


def principal_for(resource_name: str, project_number: str) -> str:
    """Build the IAM principal string for an engine's Agent Identity."""
    return (
        f"principal://agents.global.proj-{project_number}.system.id.goog"
        f"/resources/aiplatform/{resource_name}"
    )


#: `gcloud` on Windows is a `.cmd` shim, which CreateProcess will not launch directly --
#: subprocess raises "The system cannot find the file specified" naming nothing useful.
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"


def _run(args: list[str]) -> tuple[int, str]:
    resolved = [GCLOUD if a == "gcloud" else a for a in args]
    completed = subprocess.run(  # noqa: S603
        resolved, capture_output=True, text=True, timeout=300
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def condition_for(bucket: str, prefix: str) -> str:
    """An IAM Condition limiting object reads to one prefix.

    `startsWith` on the object name is the mechanism GCS exposes for prefix scoping;
    there is no per-folder ACL, because folders do not exist -- the prefix is part of the
    object name.
    """
    return f'resource.name.startsWith("projects/_/buckets/{bucket}/objects/{prefix}")'


def load_engines() -> list[dict[str, Any]]:
    if not DEPLOYMENT.exists():
        sys.exit(f"error: {DEPLOYMENT} not found -- deploy the fleet first")
    return list(json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["engines"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="add the IAM bindings")
    parser.add_argument("--show", action="store_true", help="print what would be bound")
    args = parser.parse_args()
    if not (args.apply or args.show):
        parser.error("pass --apply or --show")

    project = os.environ.get("PROJECT_ID")
    if not project:
        sys.exit("error: PROJECT_ID must be set")
    bucket = f"{project}-corpus"

    code, number = _run(
        ["gcloud", "projects", "describe", project, "--format=value(projectNumber)"]
    )
    if code != 0:
        sys.exit(f"error: could not read project number: {number}")
    project_number = number.strip()

    engines = load_engines()
    records: list[dict[str, Any]] = []

    print(f"project        : {project} ({project_number})")
    print(f"corpus bucket  : gs://{bucket}\n")

    for engine in engines:
        role = engine["role"]
        prefix = CORPUS_PREFIX.get(role)
        principal = principal_for(engine["resource_name"], project_number)

        if prefix is None:
            note = (
                "shared reader, scoped by tool argument"
                if role in UNRESTRICTED_ROLES
                else "no corpus access"
            )
            print(f"  {role:14} {note}")
            records.append({"role": role, "principal": principal, "prefix": None, "note": note})
            continue

        condition = condition_for(bucket, prefix)
        print(f"  {role:14} -> gs://{bucket}/{prefix}")
        record: dict[str, Any] = {
            "role": role,
            "principal": principal,
            "prefix": prefix,
            "role_granted": "roles/storage.objectViewer",
            "condition": condition,
        }

        if args.apply:
            code, output = _run(
                [
                    "gcloud",
                    "storage",
                    "buckets",
                    "add-iam-policy-binding",
                    f"gs://{bucket}",
                    f"--member={principal}",
                    "--role=roles/storage.objectViewer",
                    f"--condition=title={role}-corpus-only,expression={condition}",
                    f"--project={project}",
                ]
            )
            record["applied"] = code == 0
            if code != 0:
                record["error"] = output[:600]
                print(f"      FAILED: {output[:200]}")
            else:
                print("      bound")
        records.append(record)

    PROOF.parent.mkdir(parents=True, exist_ok=True)
    PROOF.write_text(
        json.dumps(
            {
                "project": project,
                "project_number": project_number,
                "bucket": bucket,
                "applied": args.apply,
                "bindings": records,
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
