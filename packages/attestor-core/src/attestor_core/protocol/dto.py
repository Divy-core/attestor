"""API DTOs for the control-plane routes.

Request/response shapes only. No behaviour. FROZEN after Phase 1 -- the UI generates
its types from these, so a change here is a change to a published contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from attestor_core.domain.enums import (
    AnswerStatus,
    Confidence,
    Department,
    Framework,
    Residency,
)

ContentId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- health -----------------------------------------------------------------------


class HealthResponse(_Dto):
    status: str
    version: str


class ReadyResponse(_Dto):
    status: str
    version: str
    firestore: str
    error: str | None = None


# ---- uploads ----------------------------------------------------------------------


class UploadUrlRequest(_Dto):
    filename: str
    content_type: str
    size_bytes: int = Field(ge=1)


class UploadUrlResponse(_Dto):
    """A signed URL the browser PUTs to directly.

    The document never passes through the control plane -- it goes straight to GCS,
    which keeps a 40MB questionnaire off the request path of a scale-to-zero service.
    """

    upload_url: str
    gcs_uri: str
    expires_at: datetime


# ---- reviews ----------------------------------------------------------------------


class CreateReviewRequest(_Dto):
    customer: str
    framework: Framework = Framework.BESPOKE
    residency: Residency = Residency.ANY
    #: GCS URI of the already-uploaded questionnaire.
    gcs_uri: str


class ReviewSummary(_Dto):
    review_id: str
    customer: str
    framework: Framework
    residency: Residency
    state: str
    current_round: int = Field(ge=1)
    created_at: datetime
    question_count: int = Field(default=0, ge=0)
    answered_count: int = Field(default=0, ge=0)
    flagged_count: int = Field(default=0, ge=0)


class CitationDto(_Dto):
    document_uri: str
    document_title: str
    section: str | None = None
    snippet: str
    retrieval_score: float = Field(ge=0.0, le=1.0)


class AnswerDto(_Dto):
    question_id: ContentId
    round_id: str
    text: str
    citations: list[CitationDto] = Field(default_factory=list)
    confidence: Confidence
    status: AnswerStatus
    authored_by: str
    created_at: datetime


class QuestionDto(_Dto):
    question_id: ContentId
    text: str
    department: Department
    framework_hint: str | None = None
    answer: AnswerDto | None = None


class ReviewDetail(_Dto):
    review: ReviewSummary
    questions: list[QuestionDto] = Field(default_factory=list)


# ---- human approval ---------------------------------------------------------------


class ApprovalRequest(_Dto):
    question_id: ContentId
    approved: bool
    #: Present when the human edited the text before approving.
    edited_text: str | None = None
    resolved_by: str


class ApprovalResponse(_Dto):
    question_id: ContentId
    status: AnswerStatus
    resumed: bool


# ---- registry ---------------------------------------------------------------------


class RegistryAgentDto(_Dto):
    """One agent as read from the live Agent Registry API.

    Not a mock catalogue -- `platform.registry` reads the real service and the UI
    layers department ownership on top.
    """

    agent_id: str
    display_name: str
    resource_name: str | None = None
    department: Department = Department.UNASSIGNED
    identity_type: str | None = None
    effective_identity: str | None = None
    agent_framework: str | None = None
    #: Corpus prefixes this agent may read, e.g. ["corpus/security"].
    scopes: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


# ---- armor ------------------------------------------------------------------------


class ArmorEventDto(_Dto):
    event_id: str
    review_id: str
    run_id: str
    question_id: ContentId | None = None
    surface: str
    decision: str
    matched_filters: list[str] = Field(default_factory=list)
    chunk_index: int | None = None
    excerpt: str | None = None
    occurred_at: datetime


# ---- traces -----------------------------------------------------------------------


class SpanDto(_Dto):
    span_id: str
    parent_span_id: str | None = None
    name: str
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    attributes: dict[str, str] = Field(default_factory=dict)


class TraceDto(_Dto):
    trace_id: str
    run_id: str
    review_id: str
    spans: list[SpanDto] = Field(default_factory=list)
