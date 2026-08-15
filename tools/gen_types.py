#!/usr/bin/env python3
"""Generate TypeScript types from `attestor_core.protocol`.

pydantic -> JSON Schema -> `services/web/lib/types/generated.ts`.

The UI must not hand-maintain a second copy of the domain types. The SSE event union is
the most drift-prone surface in the system, a stale type is a silent production bug, and
"the contract is generated from a single source of truth" is a sentence worth being able
to write in the submission.

    python tools/gen_types.py            # write
    python tools/gen_types.py --check    # fail if the committed file is stale
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from attestor_core.protocol import dto, envelope, events

OUTPUT = Path("services/web/lib/types/generated.ts")

#: Models exported to TypeScript. Ordered for readability of the emitted file.
_EXPORTED: tuple[tuple[str, Any], ...] = (
    ("WorkEnvelope", envelope.WorkEnvelope),
    ("AttestorEvent", events.EventEnvelope),
    ("HealthResponse", dto.HealthResponse),
    ("ReadyResponse", dto.ReadyResponse),
    ("UploadUrlRequest", dto.UploadUrlRequest),
    ("UploadUrlResponse", dto.UploadUrlResponse),
    ("CreateReviewRequest", dto.CreateReviewRequest),
    ("ReviewSummary", dto.ReviewSummary),
    ("ReviewDetail", dto.ReviewDetail),
    ("QuestionDto", dto.QuestionDto),
    ("AnswerDto", dto.AnswerDto),
    ("CitationDto", dto.CitationDto),
    ("ApprovalRequest", dto.ApprovalRequest),
    ("ApprovalResponse", dto.ApprovalResponse),
    ("RegistryAgentDto", dto.RegistryAgentDto),
    ("ArmorEventDto", dto.ArmorEventDto),
    ("TraceDto", dto.TraceDto),
    ("SpanDto", dto.SpanDto),
)

_HEADER = """\
// GENERATED FILE -- DO NOT EDIT BY HAND.
//
// Source of truth: packages/attestor-core/src/attestor_core/protocol/
// Regenerate:      make types-gen      (or: python tools/gen_types.py)
// CI fails if this file is stale; see `make types-check`.
//
// The SSE event union below is the most drift-prone surface in the system. It exists
// once, in Python, and is generated here rather than maintained twice.

"""


def _ts_name(ref: str) -> str:
    """Turn a JSON Schema $ref into a TypeScript identifier."""
    return ref.rsplit("/", 1)[-1]


def _render(schema: dict[str, Any], indent: int = 0) -> str:
    """Render one JSON Schema node as a TypeScript type expression."""
    pad = "  " * indent

    if "$ref" in schema:
        return _ts_name(schema["$ref"])

    if "const" in schema:
        return repr(schema["const"]).replace("'", '"')

    if "enum" in schema:
        return " | ".join(repr(v).replace("'", '"') for v in schema["enum"])

    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [_render(s, indent) for s in schema[key]]
            # Collapse pydantic's `T | null` into `T | null` (TS accepts it directly).
            return " | ".join(dict.fromkeys(parts))

    kind = schema.get("type")

    if kind == "array":
        return f"{_render(schema.get('items', {}), indent)}[]"

    if kind == "object" or "properties" in schema:
        props: dict[str, Any] = schema.get("properties", {})
        if not props:
            return "Record<string, unknown>"
        required = set(schema.get("required", []))
        lines = ["{"]
        for name, sub in props.items():
            optional = "" if name in required else "?"
            rendered = _render(sub, indent + 1)
            description = sub.get("description")
            if description:
                lines.append(f"{pad}  /** {description.strip().splitlines()[0]} */")
            lines.append(f"{pad}  {name}{optional}: {rendered};")
        lines.append(f"{pad}}}")
        return "\n".join(lines)

    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(kind), "unknown")


def generate() -> str:
    """Build the complete TypeScript source."""
    chunks: list[str] = [_HEADER]
    emitted: set[str] = set()

    for name, model in _EXPORTED:
        schema = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")

        for def_name, def_schema in sorted(schema.pop("$defs", {}).items()):
            if def_name in emitted:
                continue
            emitted.add(def_name)
            description = def_schema.get("description")
            if description:
                chunks.append(f"/** {description.strip().splitlines()[0]} */")
            chunks.append(f"export type {def_name} = {_render(def_schema)};\n")

        if name in emitted:
            continue
        emitted.add(name)
        chunks.append(f"export type {name} = {_render(schema)};\n")

    return "\n".join(chunks).rstrip() + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed file differs from what would be generated",
    )
    args = parser.parse_args(argv[1:])

    generated = generate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    if args.check:
        if not OUTPUT.exists():
            print(f"types: {OUTPUT} does not exist; run `make types-gen`", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != generated:
            print(f"types: {OUTPUT} is STALE; run `make types-gen` and commit", file=sys.stderr)
            tmp = OUTPUT.with_suffix(".ts.expected")
            tmp.write_text(generated, encoding="utf-8")
            subprocess.run(["git", "--no-pager", "diff", "--no-index", str(OUTPUT), str(tmp)])
            tmp.unlink(missing_ok=True)
            return 1
        print(f"types: {OUTPUT} is up to date")
        return 0

    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"types: wrote {OUTPUT} ({len(generated.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
