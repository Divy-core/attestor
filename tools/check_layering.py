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
    "service:runtime": frozenset({"app", "deploy", "runtime_app"}),
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
