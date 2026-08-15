"""The decision layer: deny/ask/allow, confidence, escalation, residency."""

from attestor_core.policy.decisions import (
    AnswerFacts,
    ArmorVerdict,
    ConfidenceSignals,
    compute_confidence,
    decide_on_armor_verdict,
    decide_tool,
    requires_human,
    residency_permits,
)

__all__ = [
    "AnswerFacts",
    "ArmorVerdict",
    "ConfidenceSignals",
    "compute_confidence",
    "decide_on_armor_verdict",
    "decide_tool",
    "requires_human",
    "residency_permits",
]
