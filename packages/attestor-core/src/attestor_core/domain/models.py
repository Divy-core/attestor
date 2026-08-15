"""Domain models.

Pydantic v2. Frozen where the thing is semantically immutable: a citation records what
retrieval returned at a moment in time and must never be edited afterwards, whereas a
review's state changes by definition.

Zero cloud imports. Zero I/O. ``tools/check_layering.py`` enforces that mechanically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attestor_core.domain.enums import (
    AnswerStatus,
    Confidence,
    Department,
    Framework,
    Residency,
)
from attestor_core.domain.ids import make_question_id
from attestor_core.errors import EvidenceMissing

#: A 16-char lowercase hex id as produced by `domain.ids`.
ContentId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]

#: Retrieval scores are normalised to 0..1 by the search adapter.
Score = Annotated[float, Field(ge=0.0, le=1.0)]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Frozen(BaseModel):
    """Base for immutable records."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class _Mutable(BaseModel):
    """Base for records whose state legitimately changes."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class SourceRef(_Frozen):
    """Where a question physically came from in the uploaded document.

    Kept even though IDs are content-derived: the human reviewing an answer needs to
    find the row in the spreadsheet they were sent.
    """

    sheet: str | None = None
    row: int | None = None
    page: int | None = None
    cell: str | None = None


class Citation(_Frozen):
    """A provenance record: which document supports an answer, and where.

    Frozen. A citation is a claim about what the corpus said at retrieval time; if the
    corpus later changes, that is a new citation, not an edit to this one.
    """

    document_uri: str
    document_title: str
    section: str | None = None
    snippet: str
    retrieval_score: Score
    retrieved_at: datetime = Field(default_factory=_utcnow)


class Evidence(_Frozen):
    """A retrieval result, before anything decides it supports an answer.

    Distinct from `Citation` on purpose. Evidence is what search returned; a citation
    is evidence an agent chose to stand behind. Collapsing the two would mean every
    retrieved chunk looks like a claim we have made.
    """

    document_uri: str
    document_title: str
    section: str | None = None
    content: str
    score: Score
    department: Department
    retrieved_at: datetime = Field(default_factory=_utcnow)

    def to_citation(self, snippet: str | None = None) -> Citation:
        """Promote this evidence to a citation once an agent relies on it."""
        return Citation(
            document_uri=self.document_uri,
            document_title=self.document_title,
            section=self.section,
            snippet=snippet if snippet is not None else self.content,
            retrieval_score=self.score,
            retrieved_at=self.retrieved_at,
        )


class Question(_Frozen):
    """One question from a questionnaire.

    ``question_id`` is derived from ``text``, never from position -- see
    ``domain.ids.make_question_id`` for why that is load-bearing.
    """

    question_id: ContentId
    #: Normalised, human-readable question text.
    text: str
    #: Exactly as extracted, including numbering and any oddities. Kept because Model
    #: Armor screens the raw cell, and an injection may live in what normalisation strips.
    raw_text: str
    department: Department = Department.UNASSIGNED
    source_ref: SourceRef | None = None
    #: e.g. "CC6.1", "A.9.2.3" -- a hint from the sheet, not authoritative.
    framework_hint: str | None = None

    @classmethod
    def from_text(
        cls,
        raw_text: str,
        *,
        text: str | None = None,
        department: Department = Department.UNASSIGNED,
        source_ref: SourceRef | None = None,
        framework_hint: str | None = None,
    ) -> Question:
        """Build a question, deriving its id from its content.

        This is the only sanctioned way to construct a `Question`; it guarantees the
        id and the text cannot drift apart.
        """
        resolved = text if text is not None else raw_text
        return cls(
            question_id=make_question_id(resolved),
            text=resolved,
            raw_text=raw_text,
            department=department,
            source_ref=source_ref,
            framework_hint=framework_hint,
        )


class Answer(_Mutable):
    """A drafted answer to one question in one round.

    Provenance is structural. See the validator: an answer may carry zero citations
    only when it is explicitly flagged as unsupported or quarantined. Enforcing this in
    the type system means it cannot be violated by a prompt regression, a retrieval
    failure, or a tired human on day 27.
    """

    question_id: ContentId
    round_id: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    status: AnswerStatus = AnswerStatus.DRAFT
    #: Which agent authored it, e.g. "SecurityAgent". Part of the audit trail.
    authored_by: str
    created_at: datetime = Field(default_factory=_utcnow)

    #: Statuses that may legitimately carry no citations. Everything else must cite.
    _CITATION_EXEMPT = frozenset(
        {
            AnswerStatus.FLAGGED_NO_EVIDENCE,
            AnswerStatus.QUARANTINED,
        }
    )

    @model_validator(mode="after")
    def _citations_are_mandatory(self) -> Self:
        """Reject an unsupported answer that has not admitted it is unsupported."""
        if not self.citations and self.status not in self._CITATION_EXEMPT:
            raise EvidenceMissing(
                "answer has no citations but is not flagged as unsupported; "
                f"status={self.status.value!r} must be one of "
                f"{sorted(s.value for s in self._CITATION_EXEMPT)} to omit citations",
                question_id=self.question_id,
                round_id=self.round_id,
            )
        return self


class Commitment(_Frozen):
    """A durable statement made to a customer in a prior round.

    This is what Memory Bank stores and what round 2 is checked against. Frozen: you
    cannot retroactively edit what you told a customer in July.
    """

    commitment_id: ContentId
    review_id: str
    round_id: str
    question_id: ContentId
    #: The commitment as a standalone claim, readable without its question.
    #: e.g. "Northwind does not offer on-premises or self-hosted deployment."
    statement: str
    made_at: datetime = Field(default_factory=_utcnow)


class Round(_Mutable):
    """One round of a review. Reviews come back; each return is a new round."""

    round_id: str
    review_id: str
    #: 1-based. Round 1 is the initial questionnaire.
    ordinal: int = Field(ge=1)
    received_at: datetime = Field(default_factory=_utcnow)
    closed_at: datetime | None = None
    #: The review state at which this round sits. Typed as str to avoid a circular
    #: import with `state`; the state machine owns validity.
    state: str


class Review(_Mutable):
    """A vendor security review, spanning rounds over weeks."""

    review_id: str
    customer: str
    framework: Framework = Framework.BESPOKE
    residency: Residency = Residency.ANY
    created_at: datetime = Field(default_factory=_utcnow)
    #: Ordinal of the round currently in flight.
    current_round: int = Field(default=1, ge=1)
    state: str
    #: Set when state == blocked, so the machine can return to where it came from.
    blocked_from: str | None = None
