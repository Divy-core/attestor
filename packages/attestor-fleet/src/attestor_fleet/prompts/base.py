"""Prompt assembly with a byte-stable static prefix.

Gemini context caching only pays out when the *static prefix* of a prompt is
byte-identical across turns. Anything that varies breaks the cache silently: it does not
error, it just costs more and gets slower, which is the worst kind of regression because
nothing tells you.

So the rules, enforced by `tests/unit/test_prompt_stability.py`:

* No timestamps, no dates, no "as of" text.
* No counts that change between turns ("you have 312 questions to answer").
* No dict or set iteration -- anything collection-shaped is explicitly sorted.
* No randomness, no UUIDs.
* Dynamic content goes AFTER the static prefix, never interleaved into it.

`build_prompt` is the only sanctioned way to assemble one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Separates the cacheable static prefix from the per-request dynamic tail. Chosen to be
#: visually obvious in a trace, and stable.
DYNAMIC_MARKER = "\n\n=== REQUEST ===\n"


def render_static(*sections: str) -> str:
    """Join static prompt sections into the cacheable prefix.

    Sections are joined verbatim in the order given -- the caller controls order, and
    that order must not depend on a dict or set.
    """
    return "\n\n".join(section.strip() for section in sections if section.strip())


def render_list(items: Iterable[str], bullet: str = "-") -> str:
    """Render a sorted bullet list.

    **Sorted**, always. A list built from a set or a dict's keys would come out in a
    different order between processes, which breaks the prefix without any visible
    symptom.
    """
    return "\n".join(f"{bullet} {item}" for item in sorted(items))


def render_mapping(mapping: Mapping[str, str], bullet: str = "-") -> str:
    """Render a mapping as a sorted bullet list of `key: value`."""
    return "\n".join(f"{bullet} {key}: {mapping[key]}" for key in sorted(mapping))


def build_prompt(static_prefix: str, dynamic: str) -> str:
    """Assemble a full prompt from its cacheable prefix and its per-request tail."""
    return f"{static_prefix.rstrip()}{DYNAMIC_MARKER}{dynamic.strip()}"


def split_prompt(prompt: str) -> tuple[str, str]:
    """Recover (static_prefix, dynamic) from an assembled prompt.

    Used by the stability test to assert the prefix is byte-identical across turns.
    """
    prefix, _, dynamic = prompt.partition(DYNAMIC_MARKER)
    return prefix, dynamic
