#!/usr/bin/env python3
"""Mechanically enforce Attestor's dependency invariant.

    attestor_core      ->  stdlib + pydantic only
    attestor_platform  ->  attestor_core (+ google/GCP SDKs)
    attestor_fleet     ->  attestor_core, attestor_platform (+ google-adk)
                           MUST NOT import fastapi/uvicorn or anything under services/
    services/*         ->  packages/*   ·  MUST NOT import another service
    packages/*         ->  MUST NOT import any service

The load-bearing rule is the ``attestor_fleet`` one. That package is bundled and
shipped to Agent Runtime; if it transitively pulls in a web framework the deploy
fails in ways that are painful to diagnose. So it is enforced here, in CI, rather
than being a convention someone remembers.

Imports are read from the AST, not from regex over source text.

Usage:
    python tools/check_layering.py [ROOT]

Exits 0 when clean, 1 when any rule is violated.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------------------
# The map from directory to zone. Order matters: first matching prefix wins.
# --------------------------------------------------------------------------------------

CORE = "attestor_core"
PLATFORM = "attestor_platform"
FLEET = "attestor_fleet"

#: Relative source roots -> the zone name owning the code beneath them.
ZONE_ROOTS: tuple[tuple[str, str], ...] = (
    ("packages/attestor-core/src", CORE),
    ("packages/attestor-platform/src", PLATFORM),
    ("packages/attestor-fleet/src", FLEET),
    ("services/control-plane", "service:control-plane"),
    ("services/dispatcher", "service:dispatcher"),
    ("services/runtime", "service:runtime"),
)

#: Top-level module names each service exposes, so a cross-service import is detectable.
SERVICE_MODULES: dict[str, frozenset[str]] = {
    "service:control-plane": frozenset({"control_plane"}),
    "service:dispatcher": frozenset({"dispatcher"}),
    # Deliberately no "app": the Agent Runtime container already has a top-level `app`
    # package, and a bundle module of that name breaks the deploy at startup.
    "service:runtime": frozenset({"deploy", "runtime_app"}),
}

#: Our own package modules, in layer order.
PACKAGE_MODULES: frozenset[str] = frozenset({CORE, PLATFORM, FLEET})

#: Which of *our* modules each zone may import.
ALLOWED_INTERNAL: dict[str, frozenset[str]] = {
    CORE: frozenset(),
    PLATFORM: frozenset({CORE}),
    FLEET: frozenset({CORE, PLATFORM}),
}

#: Third-party modules ``attestor_core`` may import. Deliberately tiny -- core is pure
#: domain logic and must stay unit-testable with no cloud, no credentials, no network.
CORE_ALLOWED_THIRD_PARTY: frozenset[str] = frozenset({"pydantic", "pydantic_core"})

#: Web/server machinery that must never reach a package. For ``attestor_fleet`` this is
#: the rule that keeps the Agent Runtime bundle deployable.
FORBIDDEN_IN_PACKAGES: frozenset[str] = frozenset(
    {
        "fastapi",
        "uvicorn",
        "starlette",
        "flask",
        "django",
        "gunicorn",
        "hypercorn",
        "quart",
        "sanic",
        "tornado",
        "aiohttp",
    }
)

SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        ".next",
    }
)


@dataclass(frozen=True)
class Violation:
    """One broken rule, located precisely enough to fix without hunting."""

    path: Path
    line: int
    zone: str
    imported: str
    reason: str

    def render(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - path outside root
            shown = self.path.as_posix()
        return f"{shown}:{self.line}: [{self.zone}] imports '{self.imported}' -- {self.reason}"


def _zone_for(path: Path, root: Path) -> str | None:
    """Return the zone owning ``path``, or None if the file is outside every zone."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return None
    for prefix, zone in ZONE_ROOTS:
        if rel == prefix or rel.startswith(prefix + "/"):
            return zone
    return None


def _iter_python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _top_level_imports(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Yield (top-level module name, lineno) for every absolute import in the tree.

    Relative imports (``from . import x``) stay inside their own package by
    construction, so they carry no layering information and are skipped.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                yield node.module.split(".")[0], node.lineno


def _check_import(zone: str, module: str, path: Path, line: int) -> Violation | None:
    """Apply every rule that governs ``zone`` to a single imported module."""
    is_service_zone = zone.startswith("service:")

    # --- our own service modules -------------------------------------------------
    for owner, modules in SERVICE_MODULES.items():
        if module not in modules:
            continue
        if not is_service_zone:
            return Violation(
                path,
                line,
                zone,
                module,
                f"packages must never import a service ({owner}); "
                "shared code moves down into a package, never sideways",
            )
        if owner != zone:
            return Violation(
                path,
                line,
                zone,
                module,
                f"services must never import another service ({owner}); "
                "route through Pub/Sub or a shared package instead",
            )
        return None

    # --- our own package modules -------------------------------------------------
    if module in PACKAGE_MODULES:
        if is_service_zone:
            return None  # services may import any package
        if module == zone:
            return None  # a package importing itself absolutely is fine
        allowed = ALLOWED_INTERNAL.get(zone, frozenset())
        if module not in allowed:
            permitted = ", ".join(sorted(allowed)) or "nothing"
            return Violation(
                path,
                line,
                zone,
                module,
                f"{zone} may only import: {permitted}",
            )
        return None

    # --- third party -------------------------------------------------------------
    if is_service_zone:
        return None  # services are composition roots; they may depend on anything

    if module in FORBIDDEN_IN_PACKAGES:
        extra = (
            " -- attestor_fleet is bundled to Agent Runtime and must stay free of web frameworks"
            if zone == FLEET
            else ""
        )
        return Violation(
            path, line, zone, module, f"web/server framework is forbidden in packages{extra}"
        )

    if zone == CORE:
        if module in sys.stdlib_module_names or module in CORE_ALLOWED_THIRD_PARTY:
            return None
        return Violation(
            path,
            line,
            zone,
            module,
            "attestor_core is restricted to stdlib + pydantic (no cloud, no network)",
        )

    return None


# --------------------------------------------------------------------------------------
# Phase 0 findings, encoded as mechanical checks.
#
# Each of these cost a diagnose-fix-rerun cycle once. None of them may cost one again,
# and none of them is the kind of thing a comment reliably prevents.
# --------------------------------------------------------------------------------------

#: Zones whose files are bundled and shipped to Agent Runtime.
BUNDLED_ZONES: frozenset[str] = frozenset({FLEET, "service:runtime"})

#: The Agent Runtime container has its own top-level ``app`` package at
#: /code/app/__init__.py. cloudpickle serialises tool functions BY MODULE REFERENCE, so
#: a bundle module named app.py unpickles against Google's package instead of ours and
#: the engine dies at startup with:
#:   Can't get attribute 'get_review_count' on <module 'app' from '/code/app/__init__.py'>
#: create() appears to succeed for several minutes first, then reports only the generic
#: "failed to start and cannot serve traffic".
BANNED_BUNDLE_FILENAMES: frozenset[str] = frozenset({"app.py"})

#: The single module permitted to name a model or construct a model client.
MODEL_CONFIG_MODULE = "packages/attestor-platform/src/attestor_platform/config.py"

#: Every Gemini 3.x model is served ONLY from location "global"; a regional call 404s
#: in a way that reads as an entitlement problem. Model strings live in config.py so
#: the location pin cannot be bypassed by constructing a client elsewhere.
_MODEL_LITERAL = re.compile(r"\bgemini-[0-9]+(?:\.[0-9]+)?-[a-z0-9-]+\b")

#: Callables that construct a Gemini/genai client. Constructing one outside the factory
#: is how the location pin gets bypassed. Note `agentplatform.Client(location=...)` is
#: NOT in this set: that is the Agent Engine control-plane client, which legitimately
#: targets us-central1 -- the reasoningEngine resource is regional even though the model
#: it calls is not. Conflating the two is exactly the confusion this check exists to stop.
_GEMINI_CONSTRUCTORS: frozenset[str] = frozenset({"Gemini", "GenerativeModel"})

#: Passing client_kwargs anywhere but the factory means someone is hand-rolling the pin.
_PINNING_KWARGS: frozenset[str] = frozenset({"client_kwargs"})

#: Exempt from the model-string and constructor rules.
#: `services/runtime/runtime_app.py` is the Phase 0 proof-of-life probe. It deliberately
#: pins its own model because the bundle stays minimal and does not ship
#: attestor-platform. Phase 5 replaces it with the real fleet, and this exemption is
#: removed at that point.
_MODEL_LITERAL_EXEMPT: frozenset[str] = frozenset(
    {
        MODEL_CONFIG_MODULE,
        "services/runtime/runtime_app.py",
    }
)


def _check_bundle_filenames(root: Path) -> list[Violation]:
    """Fail if a bundled zone contains a file whose name breaks cloudpickle."""
    violations: list[Violation] = []
    for path in _iter_python_files(root):
        zone = _zone_for(path, root)
        if zone not in BUNDLED_ZONES:
            continue
        if path.name in BANNED_BUNDLE_FILENAMES:
            violations.append(
                Violation(
                    path,
                    0,
                    zone or "?",
                    path.name,
                    f"{path.name!r} is a forbidden filename in a bundle shipped to Agent "
                    "Runtime: the container already has a top-level 'app' package and "
                    "cloudpickle resolves tools by module reference, so the engine dies "
                    "at startup. Rename it (e.g. runtime_app.py)",
                )
            )
    return violations


def _check_model_configuration(root: Path) -> list[Violation]:
    """Fail if a model string or a location pin appears outside ``config.py``."""
    violations: list[Violation] = []
    for path in _iter_python_files(root):
        zone = _zone_for(path, root)
        if zone is None:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in _MODEL_LITERAL_EXEMPT:
            continue

        source = path.read_text(encoding="utf-8")

        for lineno, text in enumerate(source.splitlines(), start=1):
            stripped = text.strip()
            if stripped.startswith("#"):
                continue  # prose about the rule is not a violation of it
            match = _MODEL_LITERAL.search(text)
            if match:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        zone,
                        match.group(0),
                        "model strings live only in attestor_platform.config; import "
                        "REASONING_MODEL / TRIAGE_MODEL instead of writing the literal",
                    )
                )

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue  # reported by check()

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else None
                )
                if name in _GEMINI_CONSTRUCTORS:
                    violations.append(
                        Violation(
                            path,
                            node.lineno,
                            zone,
                            f"{name}(...)",
                            "Gemini clients are constructed only by "
                            "attestor_platform.config.gemini_model(), which pins "
                            "location='global'. Every Gemini 3.x model is served ONLY "
                            "from 'global'; a regional call 404s as if unentitled",
                        )
                    )
            if isinstance(node, ast.keyword) and node.arg in _PINNING_KWARGS:
                violations.append(
                    Violation(
                        path,
                        node.value.lineno,
                        zone,
                        f"{node.arg}=",
                        "client_kwargs is set only in attestor_platform.config; "
                        "hand-pinning the model location elsewhere defeats the factory",
                    )
                )
    return violations


def check(root: Path) -> list[Violation]:
    """Parse every Python file under ``root`` and return all layering violations."""
    violations: list[Violation] = []
    for path in _iter_python_files(root):
        zone = _zone_for(path, root)
        if zone is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(
                Violation(path, exc.lineno or 0, zone, "<unparseable>", f"syntax error: {exc.msg}")
            )
            continue
        for module, line in _top_level_imports(tree):
            violation = _check_import(zone, module, path, line)
            if violation is not None:
                violations.append(violation)

    violations.extend(_check_bundle_filenames(root))
    violations.extend(_check_model_configuration(root))
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    violations = check(root)
    if not violations:
        print(f"layering: OK -- no violations under {root}")
        return 0
    print(f"layering: {len(violations)} violation(s) under {root}\n", file=sys.stderr)
    for violation in violations:
        print("  " + violation.render(root), file=sys.stderr)
    print(
        "\nThe dependency invariant is:\n"
        "  attestor_core      -> stdlib + pydantic only\n"
        "  attestor_platform  -> attestor_core (+ google/GCP SDKs)\n"
        "  attestor_fleet     -> attestor_core, attestor_platform (+ google-adk)\n"
        "  services/*         -> packages/*, never another service\n"
        "  packages/*         -> never a service",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
