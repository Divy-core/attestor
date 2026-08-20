"""Firestore repositories. Event collections are append-only by construction."""

from attestor_platform.firestore.claims import (
    Claim,
    ClaimOutcome,
    ClaimState,
    WorkClaimRepository,
)
from attestor_platform.firestore.repositories import (
    AnswerRepository,
    ArmorEventRepository,
    AuditEventRepository,
    CommitmentRepository,
    InboxStateRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
    RoundSourceRepository,
)

__all__ = [
    "AnswerRepository",
    "ArmorEventRepository",
    "AuditEventRepository",
    "Claim",
    "ClaimOutcome",
    "ClaimState",
    "CommitmentRepository",
    "InboxStateRepository",
    "QuestionRepository",
    "ReviewRepository",
    "RoundRepository",
    "RoundSourceRepository",
    "WorkClaimRepository",
]
