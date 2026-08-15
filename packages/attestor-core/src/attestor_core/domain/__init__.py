"""Domain model: the things a vendor security review is made of."""

from attestor_core.domain.enums import (
    AnswerStatus,
    ArmorDecision,
    Confidence,
    ContradictionVerdict,
    Department,
    Framework,
    Residency,
    ToolDecision,
)
from attestor_core.domain.ids import make_dedup_key, make_question_id, normalize_question_text
from attestor_core.domain.models import (
    Answer,
    Citation,
    Commitment,
    Evidence,
    Question,
    Review,
    Round,
    SourceRef,
)

__all__ = [
    "Answer",
    "AnswerStatus",
    "ArmorDecision",
    "Citation",
    "Commitment",
    "Confidence",
    "ContradictionVerdict",
    "Department",
    "Evidence",
    "Framework",
    "Question",
    "Residency",
    "Review",
    "Round",
    "SourceRef",
    "ToolDecision",
    "make_dedup_key",
    "make_question_id",
    "normalize_question_text",
]
