"""Control plane endpoints: uploads, rounds, approvals, reads, and the event stream.

A composition root. Every decision it appears to make is made somewhere else —
`core.state.transition` owns legality, `core.policy` owns escalation, the dispatcher owns
execution. What lives here is wiring, and it is kept that way deliberately: this is the
only service a browser can reach, so any logic that drifts in here is logic that has to
be re-implemented before it can ever run asynchronously.

## Uploads never transit this service

A 40MB questionnaire uploaded through the API would occupy a Cloud Run instance for the
duration of the transfer, count against its memory, and gain nothing. The browser gets a
**signed URL** and PUTs to GCS directly; the control plane is told the object exists
afterwards. The service handles kilobytes of JSON, never megabytes of spreadsheet.

## Nothing here drives the agent

`POST /reviews/{id}/rounds` publishes an `intake_document` envelope and returns. It does
not wait, does not poll, and does not call the fleet. That is the Phase 4 property: the
review advances because messages are delivered, and the only synchronous thing in the
system is the human clicking approve.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from attestor_core.domain import Review, Round
from attestor_core.domain.enums import Framework, Residency, ReviewState
from attestor_core.errors import AttestorError, IllegalTransition
from attestor_core.protocol import WorkEnvelope, WorkKind
from attestor_core.state import transition
from attestor_platform.firestore import (
    AnswerRepository,
    ArmorEventRepository,
    AuditEventRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
)
from attestor_platform.pubsub import WorkPublisher
from attestor_platform.storage import StorageClient
from control_plane.streaming import RunEventStream

logger = logging.getLogger(__name__)

router = APIRouter()

#: Read caps. A questionnaire is 312 questions and a run emits ~1,100 audit events, so
#: these are generous for the UI and still bounded.
MAX_ROWS = 1000


# ---------------------------------------------------------------------------------
# Request models. Validation at the edge, so a malformed call fails here with a field
# name rather than three services away.
# ---------------------------------------------------------------------------------


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CreateReviewRequest(BaseModel):
    customer: str = Field(min_length=1, max_length=200)
    framework: Framework = Framework.CAIQ
    residency: Residency = Residency.US


class CreateRoundRequest(BaseModel):
    gcs_uri: str = Field(min_length=5)
    ordinal: int = Field(default=1, ge=1)


class ApprovalRequest(BaseModel):
    approved: bool
    resolved_by: str = Field(min_length=1, max_length=200)
    edited_text: str | None = None


# ---------------------------------------------------------------------------------
# Dependencies, built once per instance.
# ---------------------------------------------------------------------------------

_singletons: dict[str, Any] = {}


def _get(name: str, factory: Any) -> Any:
    if name not in _singletons:
        _singletons[name] = factory()
    return _singletons[name]


def reviews() -> ReviewRepository:
    return _get("reviews", ReviewRepository)  # type: ignore[no-any-return]


def rounds() -> RoundRepository:
    return _get("rounds", RoundRepository)  # type: ignore[no-any-return]


def questions() -> QuestionRepository:
    return _get("questions", QuestionRepository)  # type: ignore[no-any-return]


def answers() -> AnswerRepository:
    return _get("answers", AnswerRepository)  # type: ignore[no-any-return]


def audit() -> AuditEventRepository:
    return _get("audit", AuditEventRepository)  # type: ignore[no-any-return]


def armor_events() -> ArmorEventRepository:
    return _get("armor", ArmorEventRepository)  # type: ignore[no-any-return]


def publisher() -> WorkPublisher:
    return _get("publisher", WorkPublisher)  # type: ignore[no-any-return]


def storage() -> StorageClient:
    return _get("storage", StorageClient)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------------


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(body: UploadRequest) -> dict[str, Any]:
    """Mint a signed URL so the browser PUTs the questionnaire straight to GCS."""
    object_name = f"questionnaires/{uuid.uuid4().hex}/{body.filename}"
    url, gcs_uri, expires_at = storage().signed_upload_url(object_name, body.content_type)
    return {
        "upload_url": url,
        "gcs_uri": gcs_uri,
        "expires_at": expires_at.isoformat(),
        "method": "PUT",
        "headers": {"Content-Type": body.content_type},
    }


# ---------------------------------------------------------------------------------
# Reviews and rounds
# ---------------------------------------------------------------------------------


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def create_review(body: CreateReviewRequest) -> dict[str, Any]:
    review = Review(
        review_id=f"rev-{uuid.uuid4().hex[:12]}",
        customer=body.customer,
        framework=body.framework,
        residency=body.residency,
        current_round=0,
        state=ReviewState.INTAKE,
    )
    reviews().put(review)
    return review.model_dump(mode="json")


@router.post("/reviews/{review_id}/rounds", status_code=status.HTTP_202_ACCEPTED)
def create_round(review_id: str, body: CreateRoundRequest) -> dict[str, Any]:
    """Register a round and publish the work that starts it.

    **202, not 201.** The round exists; the answers do not, and will not for twelve
    minutes. A 201 would imply the caller can read the result, and this endpoint returns
    long before any agent has run.
    """
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    if not storage().exists(body.gcs_uri):
        # Checked here rather than in the handler: a missing object is the caller's
        # mistake and should fail at the call site, not as a dead letter minutes later.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"no object at {body.gcs_uri}")

    round_id = f"{review_id}-r{body.ordinal}"
    run_id = f"run-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    rounds().put(
        Round(
            round_id=round_id,
            review_id=review_id,
            ordinal=body.ordinal,
            state=ReviewState.INTAKE,
        )
    )

    follow_up = body.ordinal > 1
    envelope = WorkEnvelope.for_work(
        message_id=f"{run_id}-start",
        review_id=review_id,
        run_id=run_id,
        round_id=round_id,
        kind=WorkKind.OPEN_FOLLOW_UP if follow_up else WorkKind.INTAKE_DOCUMENT,
        payload=(
            {"gcs_uri": body.gcs_uri, "round_ordinal": body.ordinal}
            if follow_up
            else {"gcs_uri": body.gcs_uri, "original_filename": body.gcs_uri.rsplit("/", 1)[-1]}
        ),
    )
    publisher().publish(envelope)

    return {
        "review_id": review_id,
        "round_id": round_id,
        "run_id": run_id,
        "kind": envelope.kind.value,
        "dedup_key": envelope.dedup_key,
        "stream": f"/runs/{run_id}/events",
    }


@router.post("/reviews/{review_id}/state", status_code=status.HTTP_200_OK)
def move_state(review_id: str, target: ReviewState) -> dict[str, Any]:
    """Move a review by hand. Every path goes through `core.state.transition`.

    Exists for operations, not for the happy path -- the dispatcher moves reviews. An
    illegal move is a 409 rather than a 400: the request was well-formed, the review is
    simply not in a state where it makes sense.
    """
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    try:
        new_state = transition(review.state, target, review_id=review_id)
    except IllegalTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    reviews().put(review.model_copy(update={"state": new_state}))
    return {"review_id": review_id, "state": new_state.value}


# ---------------------------------------------------------------------------------
# Human in the loop
# ---------------------------------------------------------------------------------


@router.post("/rounds/{round_id}/answers/{question_id}/approval")
def approve(round_id: str, question_id: str, body: ApprovalRequest) -> dict[str, Any]:
    """Record a human decision and publish the resume.

    The decision is applied by the dispatcher, not here, so that a resume behaves
    identically whether it came from this endpoint or from a redelivered message.
    """
    answer = answers().get(round_id, question_id)
    if answer is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"no answer for {question_id!r} in {round_id!r}"
        )

    review_id = round_id.rsplit("-r", 1)[0]
    run_id = f"resume-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    envelope = WorkEnvelope.for_work(
        message_id=f"{run_id}-{question_id}",
        review_id=review_id,
        run_id=run_id,
        round_id=round_id,
        question_id=question_id,
        kind=WorkKind.RESUME_AFTER_HUMAN,
        payload={
            "approved": body.approved,
            "resolved_by": body.resolved_by,
            "edited_text": body.edited_text,
        },
    )
    publisher().publish(envelope)

    audit().append_safe(
        {
            "kind": "human_decision",
            "review_id": review_id,
            "run_id": run_id,
            "question_id": question_id,
            "actor": body.resolved_by,
            "detail": {
                "approved": body.approved,
                "edited": body.edited_text is not None,
                "round_id": round_id,
            },
        }
    )
    return {"accepted": True, "dedup_key": envelope.dedup_key, "run_id": run_id}


# ---------------------------------------------------------------------------------
# Reads for the Phase 6 UI
# ---------------------------------------------------------------------------------


@router.get("/reviews")
def list_reviews(limit: int = 50) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in reviews().list_all(limit=min(limit, MAX_ROWS))]


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> dict[str, Any]:
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    return {
        **review.model_dump(mode="json"),
        "rounds": [r.model_dump(mode="json") for r in rounds().for_review(review_id)],
    }


@router.get("/rounds/{round_id}/questions")
def list_questions(round_id: str) -> list[dict[str, Any]]:
    return [q.model_dump(mode="json") for q in questions().for_round(round_id)]


@router.get("/rounds/{round_id}/answers")
def list_answers(round_id: str) -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in answers().for_round(round_id)]


@router.get("/reviews/{review_id}/audit")
def list_audit(review_id: str, limit: int = 500) -> list[dict[str, Any]]:
    return audit().for_review(review_id, limit=min(limit, MAX_ROWS))


@router.get("/reviews/{review_id}/armor")
def list_armor(review_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Guardrail events. Their own endpoint because they are their own video beat."""
    return armor_events().for_review(review_id, limit=min(limit, MAX_ROWS))


@router.get("/registry")
def list_registry() -> list[dict[str, Any]]:
    """The fleet as the platform has catalogued it.

    An unreachable registry is a 503, never an empty list. "No agents are registered" is
    a claim, and rendering it because a call failed would be a lie told in a demo.
    """
    from attestor_platform.registry import AgentRegistry

    try:
        return [a.model_dump(mode="json") for a in AgentRegistry().list_agents()]
    except AttestorError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


# ---------------------------------------------------------------------------------
# The stream
# ---------------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request, use_listener: bool = True) -> Any:
    """SSE for one run.

    `use_listener=false` disables the realtime listener so the polling fallback can be
    exercised deliberately. The exit criterion asks for the fallback to engage when the
    listener is *disabled*, not only when it errors -- because the failure that actually
    happens is a listener that stops delivering while reporting nothing at all.
    """
    since = 0
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        since = int(last_event_id)

    stream = RunEventStream(run_id, audit(), since_seq=since, use_listener=use_listener)
    return StreamingResponse(
        stream.__aiter__(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Nginx and several managed proxies buffer by default, which would hold every
            # frame until the response closed -- twelve minutes of nothing.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def build_app_routes() -> APIRouter:
    """Exposed for `main.py` to mount, and for tests to mount without the app."""
    return router


PROJECT_ID = os.environ.get("PROJECT_ID", "")
