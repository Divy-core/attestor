"""Deploy the Phase 0 probe agent to Agent Runtime with Agent Identity.

    PROJECT_ID=attestor-505506 REGION=us-central1 uv run python services/runtime/deploy.py

Prints the reasoningEngine resource name on success. Idempotent-ish: pass
--reuse to attach to an existing deployment with the same display name instead
of creating a second one, so repeated runs do not accumulate billable engines.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import agentplatform
from agentplatform.agent_engines import AdkApp

# NOT `from app import ...`. The Agent Runtime container has its own top-level `app`
# package at /code/app/__init__.py. cloudpickle serialises tool functions by module
# reference, so a local module named `app.py` unpickles against Google's package
# instead of ours and the engine dies at startup with:
#   UserCodeControlPlaneError: Control plane operation failed due to user code:
#   Can't get attribute 'get_review_count' on <module 'app' from '/code/app/__init__.py'>
# The create() call still "succeeds" for several minutes before failing with the
# generic "failed to start and cannot serve traffic", which names none of this.
from runtime_app import root_agent

DISPLAY_NAME = "attestor-probe"

# Deployment requirements. Minimal, but not smaller than the runtime actually needs.
# Pinned, not ranged -- see docs/proof/PHASE-0-DISCOVERY.md section 2.3.
#
# cloudpickle and pydantic are NOT optional: the agent object is cloudpickled into the
# bundle, and the SDK validates that both appear in this list. Omitting them produced
#   "The following requirements are missing: {'cloudpickle', 'pydantic'}"
# followed by a deploy that reached the platform and then failed with
#   "Reasoning Engine resource [...] failed to start and cannot serve traffic."
# The second message says nothing about the cause; the first is the real one. Pin both
# to the versions resolved locally so the bundle matches what the agent was pickled with.
REQUIREMENTS = [
    "google-adk==2.7.0",
    "google-cloud-aiplatform[agent-engines]==1.164.0",
    "cloudpickle==3.1.2",
    "pydantic==2.13.4",
]

# Agent Identity. The accepted values are exactly IDENTITY_TYPE_UNSPECIFIED,
# SERVICE_ACCOUNT, and AGENT_IDENTITY, defined in
#   .venv/Lib/site-packages/agentplatform/_genai/types/common.py:261
#   (class IdentityType(_common.CaseInSensitiveEnum))
# and mirrored at vertexai/_genai/types/common.py:261.
#
# We pass the *string* rather than importing IdentityType, because that enum lives
# under a private `_genai` module which is free to move between patch releases; a
# runtime import of it would break the deploy at the worst possible moment.
# AgentEngineConfig accepts an AgentEngineConfigDict, so the string is sufficient.
#
# NOTE: the enum docstring states "Use Agent Identity. The `service_account` field
# must not be set." Setting both is a deploy error -- so `service_account` is
# deliberately absent from the config below.
IDENTITY_TYPE = "AGENT_IDENTITY"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"error: {name} must be set (no hardcoded project id in this repo)")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="attach to an existing deployment with the same display name instead of creating one",
    )
    args = parser.parse_args()

    project = _require_env("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    staging_bucket = os.environ.get("STAGING_BUCKET", f"gs://{project}-staging")

    print(f"project        : {project}")
    print(f"region         : {region}")
    print(f"staging bucket : {staging_bucket}")
    print(f"identity_type  : {IDENTITY_TYPE}")
    print(f"requirements   : {REQUIREMENTS}")

    client = agentplatform.Client(project=project, location=region)

    if args.reuse:
        for existing in client.agent_engines.list():
            if getattr(existing, "display_name", None) == DISPLAY_NAME:
                print(f"\nreusing existing engine: {existing.api_resource.name}")
                return 0

    # enable_tracing is what puts spans into Cloud Trace. Without it, gate item 4
    # cannot pass.
    app = AdkApp(agent=root_agent, enable_tracing=True)

    config: dict[str, Any] = {
        "display_name": DISPLAY_NAME,
        "description": "Attestor Phase 0 proof-of-life probe (one LlmAgent, one tool).",
        "staging_bucket": staging_bucket,
        "requirements": REQUIREMENTS,
        # cloudpickle stores the agent's tools BY REFERENCE to their defining module,
        # so the module file itself must be uploaded alongside the pickle or the engine
        # starts and dies with "No module named 'runtime_app'". `requirements` covers
        # PyPI dependencies only -- local source needs extra_packages.
        "extra_packages": ["runtime_app.py"],
        "identity_type": IDENTITY_TYPE,
        # Scale to zero when idle. The hackathon brief calls this out as the main
        # cost lever, and an idle Agent Runtime is one of the two ways to burn the
        # credit budget.
        "min_instances": 0,
        "max_instances": 1,
    }

    print("\ncreating agent engine (this takes several minutes)...")
    engine = client.agent_engines.create(agent=app, config=config)

    resource_name = engine.api_resource.name
    print("\nDEPLOYED")
    print(f"resource_name: {resource_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
