"""Prove the layering checker actually catches violations.

A layering checker that has never caught anything is not known to work. Each test
here builds a deliberate violation in a temp tree and asserts the checker sees it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from check_layering import check, main

CORE_SRC = "packages/attestor-core/src/attestor_core"
PLATFORM_SRC = "packages/attestor-platform/src/attestor_platform"
FLEET_SRC = "packages/attestor-fleet/src/attestor_fleet"
CONTROL_PLANE_SRC = "services/control-plane/src/control_plane"
DISPATCHER_SRC = "services/dispatcher/src/dispatcher"


def write(root: Path, rel: str, source: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def reasons(root: Path) -> list[str]:
    return [v.reason for v in check(root)]


# ---------------------------------------------------------------------------------
# The load-bearing rule: the Agent Runtime bundle must not reach a web framework.
# ---------------------------------------------------------------------------------


def test_fleet_importing_fastapi_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{FLEET_SRC}/orchestrator.py", "import fastapi\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert violations[0].imported == "fastapi"
    assert "web/server framework is forbidden" in violations[0].reason
    assert "Agent Runtime" in violations[0].reason


@pytest.mark.parametrize("framework", ["uvicorn", "starlette", "flask", "django"])
def test_fleet_rejects_every_known_web_framework(tmp_path: Path, framework: str) -> None:
    write(tmp_path, f"{FLEET_SRC}/agents/intake.py", f"from {framework} import something\n")

    assert len(check(tmp_path)) == 1


def test_fleet_importing_a_service_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{FLEET_SRC}/tools/review.py", "from control_plane import deps\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert "packages must never import a service" in violations[0].reason


# ---------------------------------------------------------------------------------
# attestor_core stays pure.
# ---------------------------------------------------------------------------------


def test_core_importing_a_cloud_sdk_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{CORE_SRC}/policy/scope.py", "from google.cloud import firestore\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert violations[0].imported == "google"
    assert "stdlib + pydantic" in violations[0].reason


def test_core_importing_platform_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{CORE_SRC}/state/machine.py", "import attestor_platform\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert "may only import: nothing" in violations[0].reason


def test_core_may_import_stdlib_and_pydantic(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{CORE_SRC}/domain/review.py",
        "import enum\nimport dataclasses\nfrom pydantic import BaseModel\n",
    )

    assert check(tmp_path) == []


# ---------------------------------------------------------------------------------
# Layer direction: platform may not reach up into fleet.
# ---------------------------------------------------------------------------------


def test_platform_importing_fleet_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{PLATFORM_SRC}/armor/client.py", "import attestor_fleet\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert "may only import: attestor_core" in violations[0].reason


def test_platform_may_import_core_and_google(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{PLATFORM_SRC}/firestore/reviews.py",
        "from google.cloud import firestore\nfrom attestor_core.domain import Review\n",
    )

    assert check(tmp_path) == []


def test_fleet_may_import_core_platform_and_adk(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{FLEET_SRC}/pipeline.py",
        "from google.adk.agents import LlmAgent\n"
        "from attestor_core.state import ReviewState\n"
        "from attestor_platform.armor import screen_long_text\n",
    )

    assert check(tmp_path) == []


# ---------------------------------------------------------------------------------
# Services: may use packages, never each other.
# ---------------------------------------------------------------------------------


def test_service_importing_another_service_is_a_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{CONTROL_PLANE_SRC}/routes/reviews.py", "from dispatcher import handler\n")

    violations = check(tmp_path)

    assert len(violations) == 1
    assert "services must never import another service" in violations[0].reason


def test_service_may_import_packages_and_fastapi(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{CONTROL_PLANE_SRC}/main.py",
        "import fastapi\n"
        "from attestor_core.protocol import ReviewCreated\n"
        "from attestor_platform.firestore import ReviewRepository\n"
        "from attestor_fleet import orchestrator\n",
    )

    assert check(tmp_path) == []


def test_service_may_import_itself(tmp_path: Path) -> None:
    write(tmp_path, f"{DISPATCHER_SRC}/main.py", "from dispatcher.handler import handle\n")

    assert check(tmp_path) == []


# ---------------------------------------------------------------------------------
# Mechanics.
# ---------------------------------------------------------------------------------


def test_relative_imports_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, f"{CORE_SRC}/policy/__init__.py", "from .scope import decide_tool\n")

    assert check(tmp_path) == []


def test_files_outside_every_zone_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "seed/seed.py", "import fastapi\nfrom google.cloud import firestore\n")
    write(tmp_path, "tools/gen_types.py", "import fastapi\n")

    assert check(tmp_path) == []


def test_vendored_directories_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, f"{CORE_SRC}/.venv/lib/thing.py", "from google.cloud import firestore\n")
    write(tmp_path, f"{CORE_SRC}/__pycache__/cached.py", "import fastapi\n")

    assert check(tmp_path) == []


def test_multiple_violations_are_all_reported(tmp_path: Path) -> None:
    write(
        tmp_path,
        f"{FLEET_SRC}/callbacks/guard.py",
        "import fastapi\nimport uvicorn\nfrom control_plane import deps\n",
    )

    assert len(check(tmp_path)) == 3


def test_violation_renders_with_path_and_line(tmp_path: Path) -> None:
    write(tmp_path, f"{FLEET_SRC}/orchestrator.py", "import os\nimport fastapi\n")

    rendered = check(tmp_path)[0].render(tmp_path)

    assert rendered.startswith(f"{FLEET_SRC}/orchestrator.py:2:")
    assert "fastapi" in rendered


def test_main_exits_nonzero_on_violation(tmp_path: Path) -> None:
    write(tmp_path, f"{FLEET_SRC}/orchestrator.py", "import fastapi\n")

    assert main(["check_layering.py", str(tmp_path)]) == 1


def test_main_exits_zero_on_clean_tree(tmp_path: Path) -> None:
    write(tmp_path, f"{CORE_SRC}/__init__.py", "")

    assert main(["check_layering.py", str(tmp_path)]) == 0


# ---------------------------------------------------------------------------------
# Phase 0 findings, encoded as checks. Each cost a diagnose-fix-rerun cycle once.
# ---------------------------------------------------------------------------------


class TestBundleFilenames:
    def test_app_py_in_fleet_is_a_violation(self, tmp_path: Path) -> None:
        """The Agent Runtime container has its own top-level `app` package."""
        write(tmp_path, f"{FLEET_SRC}/app.py", "x = 1\n")

        violations = check(tmp_path)

        assert len(violations) == 1
        assert "forbidden filename" in violations[0].reason
        assert "cloudpickle" in violations[0].reason

    def test_app_py_in_runtime_service_is_a_violation(self, tmp_path: Path) -> None:
        write(tmp_path, "services/runtime/app.py", "x = 1\n")

        assert any("forbidden filename" in v.reason for v in check(tmp_path))

    def test_app_py_outside_a_bundle_is_fine(self, tmp_path: Path) -> None:
        """control-plane is not shipped to Agent Runtime."""
        write(tmp_path, f"{CONTROL_PLANE_SRC}/app.py", "x = 1\n")

        assert check(tmp_path) == []


class TestModelConfiguration:
    def test_model_literal_outside_config_is_a_violation(self, tmp_path: Path) -> None:
        write(tmp_path, f"{FLEET_SRC}/agents/intake.py", 'MODEL = "gemini-3.5-flash"\n')

        violations = check(tmp_path)

        assert len(violations) == 1
        assert "model strings live only in attestor_platform.config" in violations[0].reason

    def test_model_literal_inside_config_is_fine(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "packages/attestor-platform/src/attestor_platform/config.py",
            'REASONING_MODEL = "gemini-3.7-flash"\n',
        )

        assert check(tmp_path) == []

    def test_model_literal_in_a_comment_is_not_a_violation(self, tmp_path: Path) -> None:
        """Prose about the rule must not trip the rule."""
        write(tmp_path, f"{FLEET_SRC}/agents/intake.py", "# we default to gemini-3.5-flash here\n")

        assert check(tmp_path) == []

    def test_constructing_gemini_outside_the_factory_is_a_violation(self, tmp_path: Path) -> None:
        write(tmp_path, f"{FLEET_SRC}/agents/legal.py", "m = Gemini(model=X)\n")

        violations = check(tmp_path)

        assert len(violations) == 1
        assert "constructed only by" in violations[0].reason

    def test_client_kwargs_outside_the_factory_is_a_violation(self, tmp_path: Path) -> None:
        write(
            tmp_path, f"{FLEET_SRC}/agents/legal.py", 'm = build(client_kwargs={"location": "x"})\n'
        )

        violations = check(tmp_path)

        assert len(violations) == 1
        assert "client_kwargs is set only" in violations[0].reason

    def test_agent_engine_client_location_is_not_flagged(self, tmp_path: Path) -> None:
        """The reasoningEngine resource is regional even though the model is not.

        Flagging this would be a false positive that trains people to ignore the check.
        """
        write(
            tmp_path,
            "services/runtime/deploy.py",
            'c = agentplatform.Client(location="us-central1")\n',
        )

        assert check(tmp_path) == []
