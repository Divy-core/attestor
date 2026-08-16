"""Typed error hierarchy for Attestor.

Every error carries structured context rather than only a message. The Production Bar
requires correlation IDs (`review_id`, `question_id`, `run_id`) on every log line, and
the cheapest way to guarantee that is to make the exception carry them, so a handler
can log the context without having to reconstruct it from the call site.

Pure stdlib. No pydantic, no cloud, no logging configuration.
"""

from __future__ import annotations

from typing import Any


class AttestorError(Exception):
    """Base for every error Attestor raises deliberately.

    Anything that is not an ``AttestorError`` escaping our code is a bug or a genuinely
    unexpected failure from a dependency, and the two should be distinguishable.
    """

    def __init__(
        self,
        message: str,
        *,
        review_id: str | None = None,
        round_id: str | None = None,
        question_id: str | None = None,
        run_id: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.review_id = review_id
        self.round_id = round_id
        self.question_id = question_id
        self.run_id = run_id
        self.extra: dict[str, Any] = extra

    @property
    def context(self) -> dict[str, Any]:
        """Structured context, ready to splat into a structured log record."""
        ctx: dict[str, Any] = {
            "review_id": self.review_id,
            "round_id": self.round_id,
            "question_id": self.question_id,
            "run_id": self.run_id,
        }
        ctx = {k: v for k, v in ctx.items() if v is not None}
        ctx.update(self.extra)
        return ctx

    def __str__(self) -> str:
        ctx = self.context
        if not ctx:
            return self.message
        rendered = " ".join(f"{k}={v!r}" for k, v in sorted(ctx.items()))
        return f"{self.message} [{rendered}]"


class IllegalTransition(AttestorError):
    """A review state transition that the state machine does not permit.

    Raised, never warned. A silently-allowed illegal transition corrupts the audit
    trail, and the audit trail is the deliverable.
    """


class PolicyViolation(AttestorError):
    """An action the deny/ask/allow policy layer refused.

    Notably a cross-department corpus access: SecurityAgent reaching for legal.
    """


class EvidenceMissing(AttestorError):
    """An answer was constructed without provenance and without being flagged.

    "Every answer carries provenance, or it is flagged" is enforced structurally in
    ``domain``; this is what that enforcement raises.
    """


class ArmorBlocked(AttestorError):
    """Model Armor returned a blocking verdict on screened content."""


class BudgetExceeded(AttestorError):
    """A turn, token, or per-review cost ceiling was hit.

    A runaway loop is the only realistic way to burn the credit budget, so this is a
    hard stop rather than a warning.
    """


class ContractViolation(AttestorError):
    """A wire contract was violated -- a malformed envelope or unknown event type."""


class ContextUnavailable(AttestorError):
    """A read that would otherwise return "nothing" could not be performed at all.

    The distinction this exists to preserve is the one this project has now got wrong
    four separate times, in four different services:

    * Discovery Engine returning ``[]`` under a 429 -- "the corpus has no answer".
    * Model Armor denying under a timeout -- "this passage is poisoned".
    * Embeddings degrading under quota exhaustion -- "these scores are cosines".
    * Firestore or Memory Bank failing on a commitment read -- **"this customer has no
      prior commitments"**, which silently disables the consistency check and lets round
      two contradict round one while the run reports success.

    Every one of them is a failure wearing an empty result's clothes, and every one is
    invisible: no exception, no dead letter, a green run and a smaller number.

    So a read that *cannot be performed* raises this. A read that was performed and found
    nothing returns an empty collection. The caller may still choose to degrade -- but it
    has to choose, in code, where the choice is reviewable.
    """
