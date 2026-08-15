"""The review state machine. Illegal transitions raise."""

from attestor_core.state.machine import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    ReviewState,
    is_legal,
    legal_targets,
    transition,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "ReviewState",
    "is_legal",
    "legal_targets",
    "transition",
]
