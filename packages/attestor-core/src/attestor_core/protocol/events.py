"""The SSE event union.

A discriminated union on ``type``. This is the most drift-prone surface in the system:
the UI renders it, the control plane emits it, and `tools/gen_types.py` generates the
TypeScript from it. A hand-maintained second copy on the TS side would silently rot,
so there is exactly one definition and the other side is generated.

FROZEN after Phase 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from attestor_core.domain.enums import (
    AnswerStatus,
    ArmorDecision,
    Confidence,
    Department,
    ToolDecision,
)


class EventType(StrEnum):
    """Discriminator values for the SSE union."""

    RUN_STARTED = "run_started"
    QUESTION_TRIAGED = "question_triaged"
    ANSWER_DRAFTED = "answer_drafted"
    CITATION_ADDED = "citation_added"
    ARMOR_BLOCKED = "armor_blocked"
    TOOL_DENIED = "tool_denied"
    AWAITING_HUMAN = "awaiting_human"
    HUMAN_RESOLVED = "human_resolved"
    ROUND_CLOSED = "round_closed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    HEARTBEAT = "heartbeat"


class _BaseEvent(BaseModel):
    """Fields every event carries.

    ``run_id`` and ``review_id`` on every event is what lets the UI attach a stream to
    a page without a side-channel, and what makes the audit log correlatable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_id: str
    run_id: str
    #: Monotonic per run. The UI uses it to detect a gap after a reconnect.
    seq: int = Field(ge=0)
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunStarted(_BaseEvent):
    type: Literal[EventType.RUN_STARTED] = EventType.RUN_STARTED
    round_id: str
    ordinal: int = Field(ge=1)
    question_count: int = Field(ge=0)


class QuestionTriaged(_BaseEvent):
    type: Literal[EventType.QUESTION_TRIAGED] = EventType.QUESTION_TRIAGED
    question_id: str
    department: Department
    #: Which model made the call, so the cheap-triage cost story is visible in the UI.
    model: str


class AnswerDrafted(_BaseEvent):
    type: Literal[EventType.ANSWER_DRAFTED] = EventType.ANSWER_DRAFTED
    question_id: str
    authored_by: str
    status: AnswerStatus
    confidence: Confidence
    citation_count: int = Field(ge=0)
    #: Present only for short answers; the UI fetches the full text separately.
    preview: str | None = None


class CitationAdded(_BaseEvent):
    type: Literal[EventType.CITATION_ADDED] = EventType.CITATION_ADDED
    question_id: str
    document_uri: str
    document_title: str
    section: str | None = None
    retrieval_score: float = Field(ge=0.0, le=1.0)


class ArmorBlocked(_BaseEvent):
    """Model Armor refused content. Rendered in red; this is a video beat."""

    type: Literal[EventType.ARMOR_BLOCKED] = EventType.ARMOR_BLOCKED
    decision: ArmorDecision
    #: Where the content came from: "question", "tool_output", "draft_answer".
    surface: str
    question_id: str | None = None
    #: Which filters matched, e.g. ["prompt_injection"].
    matched_filters: list[str] = Field(default_factory=list)
    #: Chunk index within a long document, so the UI can point at *where* it was.
    chunk_index: int | None = None
    #: The offending excerpt, already truncated. Never the full payload.
    excerpt: str | None = None


class ToolDenied(_BaseEvent):
    """A cross-department access attempt was refused. Also a video beat."""

    type: Literal[EventType.TOOL_DENIED] = EventType.TOOL_DENIED
    agent: str
    agent_department: Department
    tool_name: str
    resource_ref: str | None = None
    decision: ToolDecision
    reason: str


class AwaitingHuman(_BaseEvent):
    type: Literal[EventType.AWAITING_HUMAN] = EventType.AWAITING_HUMAN
    question_id: str
    reason: str
    confidence: Confidence


class HumanResolved(_BaseEvent):
    type: Literal[EventType.HUMAN_RESOLVED] = EventType.HUMAN_RESOLVED
    question_id: str
    approved: bool
    #: Who resolved it. Part of the audit trail.
    resolved_by: str
    edited: bool = False


class RoundClosed(_BaseEvent):
    type: Literal[EventType.ROUND_CLOSED] = EventType.ROUND_CLOSED
    round_id: str
    ordinal: int = Field(ge=1)
    answered: int = Field(ge=0)
    flagged: int = Field(ge=0)
    commitments_recorded: int = Field(ge=0)


class RunCompleted(_BaseEvent):
    type: Literal[EventType.RUN_COMPLETED] = EventType.RUN_COMPLETED
    duration_ms: int = Field(ge=0)
    answered: int = Field(ge=0)
    flagged: int = Field(ge=0)
    blocked: int = Field(ge=0)


class RunFailed(_BaseEvent):
    type: Literal[EventType.RUN_FAILED] = EventType.RUN_FAILED
    error_type: str
    message: str


class Heartbeat(_BaseEvent):
    """Keeps buffering proxies from closing an idle SSE stream.

    Emitted every 15s. Learned the hard way on a previous project: without it a proxy
    silently drops a quiet connection and the UI looks frozen rather than idle.
    """

    type: Literal[EventType.HEARTBEAT] = EventType.HEARTBEAT


#: The discriminated union. Pydantic validates the right variant from ``type`` alone,
#: and `gen_types.py` emits the matching TypeScript union.
AttestorEvent = Annotated[
    RunStarted
    | QuestionTriaged
    | AnswerDrafted
    | CitationAdded
    | ArmorBlocked
    | ToolDenied
    | AwaitingHuman
    | HumanResolved
    | RoundClosed
    | RunCompleted
    | RunFailed
    | Heartbeat,
    Field(discriminator="type"),
]


class EventEnvelope(BaseModel):
    """Wrapper so the union has a named root for schema generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: AttestorEvent
