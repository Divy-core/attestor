"""Prompts. Static prefixes are byte-stable so context caching actually pays out."""

from attestor_fleet.prompts.base import build_prompt, render_list, render_mapping, split_prompt
from attestor_fleet.prompts.drafting import (
    CONSISTENCY_STATIC,
    TRIAGE_STATIC,
    consistency_prompt,
    drafting_prompt,
    drafting_static,
    format_evidence,
    is_hedged,
    triage_prompt,
)

__all__ = [
    "CONSISTENCY_STATIC",
    "TRIAGE_STATIC",
    "build_prompt",
    "consistency_prompt",
    "drafting_prompt",
    "drafting_static",
    "format_evidence",
    "is_hedged",
    "render_list",
    "render_mapping",
    "split_prompt",
    "triage_prompt",
]
