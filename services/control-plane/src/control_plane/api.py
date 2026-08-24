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

## Every write is guarded, as of Phase 6.5

The browser can now start work, which turned a read-only public endpoint into a public
endpoint that spends money. `guard.require_write_token` and `guard.require_capacity` run
before anything that publishes. See `guard.py` for what that is and, more importantly, what
it is not — it is a demo guard, not an auth system, and the residual exposure is stated in
`PROGRESS.md` rather than glossed.

## The export is the deliverable

`GET /reviews/{id}/export` returns the customer's own workbook with the answers written into
it, or a PDF evidence pack. Everything upstream of it produces answers in Firestore, which is
where the work is done and not where it is delivered. A vendor security review ends when the
completed spreadsheet goes back.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from attestor_core.domain import Review, Round
from attestor_core.domain.enums import AnswerStatus, Framework, Residency, ReviewState
from attestor_core.errors import AttestorError, IllegalTransition
from attestor_core.protocol import Actor, WorkEnvelope, WorkKind
from attestor_core.state import transition
from attestor_platform.export import (
    RELEASE_RULE,
    build_bundle,
    build_evidence_pack,
    fill_workbook,
)
from attestor_platform.firestore import (
    AnswerRepository,
    ArmorEventRepository,
    ArtifactRepository,
    AuditEventRepository,
    InboxStateRepository,
    QuestionRepository,
    ReviewRepository,
    RoundRepository,
    RoundSourceRepository,
)
from attestor_platform.pubsub import WorkPublisher
from attestor_platform.storage import StorageClient
from attestor_platform.thread import (
    Command,
    CommandAction,
    answer_from_trail,
    build_thread,
    parse_command,
    resolve_reference,
)
from control_plane import dispatcher_link
from control_plane.guard import require_capacity, require_write_token
from control_plane.streaming import RunEventStream

logger = logging.getLogger(__name__)

router = APIRouter()

#: Read caps. A questionnaire is 312 questions and a run emits ~1,100 audit events, so
#: these are generous for the UI and still bounded.
MAX_ROWS = 1000

#: How many audit events one thread read may take in.
#:
#: A 312-question round writes roughly 1,200. The ceiling is above that on purpose: a
#: thread built from the first thousand of twelve hundred events would describe part of a
#: run as all of it, and the counts a post quotes would silently disagree with the grid.
#: When the ceiling *is* hit the projection is told, and the thread says so rather than
#: rendering a confident half-story.
MAX_THREAD_EVENTS = 4000


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
    #: `Actor` rather than `str` with a length: a bare `min_length=1` accepts three spaces,
    #: which reaches the audit trail looking like a name and identifying nobody.
    resolved_by: Actor
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


def round_sources() -> RoundSourceRepository:
    return _get("round_sources", RoundSourceRepository)  # type: ignore[no-any-return]


def inbox_state() -> InboxStateRepository:
    return _get("inbox_state", InboxStateRepository)  # type: ignore[no-any-return]


def artifacts() -> ArtifactRepository:
    return _get("artifacts", ArtifactRepository)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------------


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(body: UploadRequest, request: Request) -> dict[str, Any]:
    """Mint a signed URL so the browser PUTs the questionnaire straight to GCS.

    Guarded even though it writes nothing itself: an unguarded signed-URL minter is a
    30-minute write grant into our own bucket, handed to anyone who asks.
    """
    require_write_token(request)
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
def create_review(body: CreateReviewRequest, request: Request) -> dict[str, Any]:
    """Create the review record. Costs nothing; starts nothing.

    Capacity is checked here as well as on `rounds`, so a person filling in the New review
    form is refused *before* they have uploaded a 40MB spreadsheet rather than after.
    """
    require_write_token(request)
    require_capacity(reviews())
    review = Review(
        review_id=f"rev-{uuid.uuid4().hex[:12]}",
        customer=body.customer,
        framework=body.framework,
        residency=body.residency,
        # 1, not 0. `Review.current_round` is `Field(ge=1)` -- "round 1 is the initial
        # questionnaire" -- so the 0 this used to pass made the endpoint raise a
        # ValidationError on every call. It had done so since Phase 2 and nobody noticed,
        # because nothing called it: every review in the project was created by a tool
        # publishing `intake_document` directly. Found by `tools/verify_journey.py`, whose
        # entire reason for existing is that the product surface and the pipeline are
        # different surfaces.
        current_round=1,
        state=ReviewState.INTAKE,
    )
    reviews().put(review)
    return review.model_dump(mode="json")


@router.post("/reviews/{review_id}/rounds", status_code=status.HTTP_202_ACCEPTED)
def create_round(review_id: str, body: CreateRoundRequest, request: Request) -> dict[str, Any]:
    """Register a round and publish the work that starts it.

    **202, not 201.** The round exists; the answers do not, and will not for twelve
    minutes. A 201 would imply the caller can read the result, and this endpoint returns
    long before any agent has run.

    This is the expensive call — everything after it runs on the deployed engines — so it is
    the one the capacity ceiling exists for.
    """
    require_write_token(request)
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    require_capacity(reviews(), starting=review_id)
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
    # Recorded now, because this is the only moment the system knows which file this round
    # came from, and the export has to hand that same file back. See
    # `RoundSourceRepository` for why it is not a field on `Round`.
    round_sources().put(round_id, body.gcs_uri)

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
def move_state(review_id: str, target: ReviewState, request: Request) -> dict[str, Any]:
    """Move a review by hand. Every path goes through `core.state.transition`.

    Exists for operations, not for the happy path -- the dispatcher moves reviews. An
    illegal move is a 409 rather than a 400: the request was well-formed, the review is
    simply not in a state where it makes sense.
    """
    require_write_token(request)
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
def approve(
    round_id: str, question_id: str, body: ApprovalRequest, request: Request
) -> dict[str, Any]:
    """Record a human decision and publish the resume.

    The decision is applied by the dispatcher, not here, so that a resume behaves
    identically whether it came from this endpoint or from a redelivered message.

    No capacity check: approving is how a review *leaves* the in-flight set, and refusing it
    because too many reviews are in flight would deadlock the very thing the ceiling exists
    to keep bounded.
    """
    require_write_token(request)
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
def list_reviews(limit: int = 50, include_archived: bool = True) -> list[dict[str, Any]]:
    """Every review, archived ones included by default.

    The default is `True` and the filtering happens in the browser, deliberately. The
    landing page has to render "Show archived (8)" with a real count, and a server that
    had already filtered them out could not supply one without a second call. The flag
    exists for callers that genuinely only want the working set — the capacity check reads
    `guard.active_reviews`, not this.
    """
    rows = reviews().list_all(limit=min(limit, MAX_ROWS))
    if not include_archived:
        rows = [r for r in rows if not r.archived]
    return [r.model_dump(mode="json") for r in rows]


#: How many reviews the board enriches with counts.
#:
#: Each row costs three server-side aggregations, which are cheap but not free, and a
#: board is a working set rather than an archive. Beyond this the rows still appear --
#: with their counts absent and *marked* absent, never as zeros, because "no answers
#: yet" and "not counted" are different facts and one of them is a lie.
BOARD_ENRICHED = 24

#: How many cards are built at once. Bounded well under `BOARD_ENRICHED`: the point is to
#: collapse the latency of independent round trips, not to open fifty concurrent Firestore
#: streams from a service running `--max-instances 4`.
BOARD_WORKERS = 8


@router.get("/reviews/board")
def review_board(limit: int = 50, include_archived: bool = False) -> list[dict[str, Any]]:
    """Every review with the state a person needs to decide which one to open.

    ## Why this is not `GET /reviews` with more fields

    A flat list of rows is a directory, and a directory makes a person open five reviews to
    find the one waiting on them. The card needs progress and a held count, and those are
    properties of the *answers*, not of the review document -- so this endpoint costs reads
    that a plain listing should not.

    ## The counts are aggregations, and they are taken in parallel

    `count_for_round` is a Firestore COUNT run server-side; streaming the answers to take a
    length would be 312 documents per row. Even so, thirteen reviews at four sequential
    round trips each is fifty-two round trips, which measured at 28 seconds against a
    laptop and would time out the page. They are independent, so they are fanned out over a
    small pool -- Firestore's client is thread-safe and FastAPI already runs this handler
    off the event loop.

    ## An uncounted row says so

    `counted: false` when the aggregation failed, with the counts left null. A card showing
    `0 held` because a read failed would send somebody past the review that is waiting on
    them, which is the one thing this surface exists to prevent.
    """
    rows = reviews().list_all(limit=min(limit, MAX_ROWS))
    if not include_archived:
        rows = [row for row in rows if not row.archived]
    if not rows:
        return []

    with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as pool:
        return list(
            pool.map(
                # Archived reviews are never counted. The counts exist to answer "what
                # needs attention", and a review taken out of the working set needs none --
                # so eight of this project's thirteen rows cost three aggregations each for
                # a figure nobody reads. Their cards say "not counted", which is true.
                lambda pair: _board_card(
                    pair[1], enrich=not pair[1].archived and pair[0] < BOARD_ENRICHED
                ),
                enumerate(rows),
            )
        )


def _board_card(review: Review, *, enrich: bool) -> dict[str, Any]:
    """One card. Never raises: a review that could not be counted is still a review."""
    card: dict[str, Any] = {
        **review.model_dump(mode="json"),
        "round_id": None,
        "questions": None,
        "answered": None,
        "held": None,
        "counted": False,
        "opened_at": None,
        "closed_at": None,
    }
    try:
        review_rounds = rounds().for_review(review.review_id)
    except AttestorError as exc:
        logger.warning("could not read rounds for %s: %s", review.review_id, exc)
        return card

    latest = max(review_rounds, key=lambda item: item.ordinal, default=None)
    if latest is None:
        return card
    card["round_id"] = latest.round_id
    card["opened_at"] = latest.received_at.isoformat()
    card["closed_at"] = latest.closed_at.isoformat() if latest.closed_at else None
    if not enrich:
        return card

    try:
        card["questions"] = questions().count_for_round(latest.round_id)
        card["answered"] = answers().count_for_round(latest.round_id)
        card["held"] = answers().count_for_round(latest.round_id, AnswerStatus.NEEDS_HUMAN)
        card["counted"] = True
    except AttestorError as exc:
        logger.warning("could not count %s for the board: %s", review.review_id, exc)
    return card


class ArchiveRequest(BaseModel):
    archived: bool = True
    #: Why, recorded on the audit trail. Not stored on the review: the review carries the
    #: flag, the immutable log carries the justification and who gave it.
    reason: str = Field(default="", max_length=500)
    actor: str = Field(default="operator", min_length=1, max_length=200)


@router.post("/reviews/{review_id}/archive")
def archive_review(review_id: str, body: ArchiveRequest, request: Request) -> dict[str, Any]:
    """Take a review out of the working set, or put it back.

    Not a delete, and not a state transition. `failed` is a terminal *state* and remains
    true of the eight dead runs from the quota work; archiving is an assertion about
    attention, which is a different thing and does not go through `core.state.transition`
    for exactly that reason — there is no legal move from `failed` and there should not be.
    """
    require_write_token(request)
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    reviews().put(review.model_copy(update={"archived": body.archived}))
    audit().append_safe(
        {
            "kind": "review_archived" if body.archived else "review_unarchived",
            "review_id": review_id,
            "actor": body.actor,
            "detail": {"reason": body.reason, "state_at_archive": review.state.value},
        }
    )
    return {"review_id": review_id, "archived": body.archived, "state": review.state.value}


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
    """One review's compliance plane.

    Capped at `MAX_THREAD_EVENTS` rather than at `MAX_ROWS`, and the difference matters: a
    312-question round writes roughly twelve hundred events, so a thousand-row ceiling
    returned an arbitrary 1,000 of them -- `for_review` applies no ordering, so it was not
    even the newest thousand -- under a footer that read "1000 of 1000 events". The page was
    truthful about what it had and silent about what it did not.
    """
    return audit().for_review(review_id, limit=min(limit, MAX_THREAD_EVENTS))


@router.get("/reviews/{review_id}/armor")
def list_armor(review_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Guardrail events. Their own endpoint because they are their own video beat."""
    return armor_events().for_review(review_id, limit=min(limit, MAX_ROWS))


# ---------------------------------------------------------------------------------
# Export -- the actual deliverable
# ---------------------------------------------------------------------------------

#: Content types, and the extension each download lands with.
_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    "pdf": ("application/pdf", "pdf"),
}


def _origin() -> str:
    """Which deployment produced this file, for the cover page and the PDF footer.

    Built from Cloud Run's own injected variables rather than a configured URL, so it cannot
    claim to be a revision it is not. Locally it says so.
    """
    service = os.environ.get("K_SERVICE")
    if not service:
        return "attestor (local)"
    revision = os.environ.get("K_REVISION", "unknown-revision")
    project = os.environ.get("PROJECT_ID", "unknown-project")
    region = os.environ.get("REGION", "unknown-region")
    return f"{service} {revision} · {project} · {region}"


def _source_uri(review_id: str, round_id: str) -> tuple[str, str]:
    """Find the questionnaire this round was started from.

    Returns:
        `(gcs_uri, provenance)` — the URI and where the URI itself came from, which is
        surfaced on the response so a reader can tell a recorded fact from a reconstructed
        one.

    Two sources, in order of trust:

    1. `round_sources`, written by `create_round` at the moment it validated the object
       exists. Exact.
    2. The audit trail. Reviews started by `tools/run_review.py` publish `intake_document`
       directly and never touch this service, so nothing wrote a source record for them —
       and those are the reviews the demo artefacts were measured on. The intake stage event
       records the `gcs_uri` it parsed, which is the same fact arrived at from the other
       end. Labelled as reconstructed because for a multi-round review the match is by
       round id in the stage detail, and the very first runs did not record it.
    """
    recorded = round_sources().get(round_id)
    if recorded:
        return recorded, "round_sources"

    candidates: list[str] = []
    for event in audit().for_review(review_id, limit=MAX_ROWS):
        detail = event.get("detail") or {}
        if not isinstance(detail, dict):
            continue
        if detail.get("stage") not in {"intake_document", "open_follow_up"}:
            continue
        uri = detail.get("gcs_uri")
        if not isinstance(uri, str) or not uri:
            continue
        if detail.get("round_id") in (None, round_id):
            candidates.append(uri)
    if candidates:
        # The most recent matching intake wins: a re-run of intake on the same round parsed
        # a later file, and that is the one whose rows the answers correspond to.
        return candidates[-1], "audit trail (reconstructed)"

    raise HTTPException(
        status.HTTP_409_CONFLICT,
        f"no questionnaire is recorded for round {round_id!r}, so the customer's own "
        "workbook cannot be filled in. The evidence pack does not need it.",
    )


@router.get("/reviews/{review_id}/export")
def export_review(review_id: str, format: str = "xlsx", round_id: str | None = None) -> Response:
    """Return the completed questionnaire.

    A read, so it is not behind the write token: it exposes exactly what
    `/rounds/{id}/answers` already does, in a format a person can use.

    `format=xlsx` re-opens the customer's uploaded workbook and writes the answers into it.
    `format=pdf` renders the evidence pack, which needs no source file — so a review whose
    upload has expired can still produce its provenance record.
    """
    if format not in _EXPORT_FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"format must be one of {sorted(_EXPORT_FORMATS)}, not {format!r}",
        )

    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")

    available = rounds().for_review(review_id)
    if not available:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"review {review_id!r} has no rounds")
    target = next((r for r in available if r.round_id == round_id), None) if round_id else None
    if target is None:
        if round_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no round {round_id!r} on review {review_id!r}"
            )
        target = available[-1]

    bundle = build_bundle(
        review,
        target,
        questions().for_round(target.round_id),
        answers().for_round(target.round_id),
        origin=_origin(),
    )
    if not bundle.rows:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"round {target.round_id!r} has no questions yet; intake has not finished.",
        )

    media_type, extension = _EXPORT_FORMATS[format]
    if format == "pdf":
        payload = build_evidence_pack(bundle)
        provenance = "not required"
    else:
        gcs_uri, provenance = _source_uri(review_id, target.round_id)
        from attestor_platform.storage.gcs import download_to_temp

        local = download_to_temp(gcs_uri, storage())
        payload = fill_workbook(local, bundle)

    audit().append_safe(
        {
            "kind": "export_produced",
            "review_id": review_id,
            "actor": "ControlPlane",
            "detail": {
                "format": format,
                "round_id": target.round_id,
                "rows": len(bundle.rows),
                "answered": bundle.answered,
                "cited": bundle.cited,
                "sendable": bundle.sendable,
                "human_approved": bundle.human_approved,
                "bytes": len(payload),
                "source": provenance,
            },
        }
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{bundle.filename(extension)}"',
            "Cache-Control": "no-store",
            # Read by the UI so the download control can state what a person is about to
            # get before they click, and so the count in the button cannot disagree with
            # the count in the file.
            "X-Attestor-Rows": str(len(bundle.rows)),
            "X-Attestor-Sendable": str(bundle.sendable),
            "X-Attestor-Source": provenance,
        },
    )


@router.get("/reviews/{review_id}/export/manifest")
def export_manifest(review_id: str, round_id: str | None = None) -> dict[str, Any]:
    """What an export would contain, without producing it.

    The download control needs to say what is in the file *before* the click, and a button
    that promises a file the system cannot produce is worse than a disabled one that says why.
    """
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")
    available = rounds().for_review(review_id)
    if not available:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"review {review_id!r} has no rounds")
    target = next((r for r in available if r.round_id == round_id), available[-1])

    bundle = build_bundle(
        review,
        target,
        questions().for_round(target.round_id),
        answers().for_round(target.round_id),
        origin=_origin(),
    )
    try:
        _, provenance = _source_uri(review_id, target.round_id)
        workbook_available = True
    except HTTPException:
        # The PDF still works without the original file, so this is a partial capability
        # rather than a failure -- and the UI must be able to show that difference.
        provenance = "not recorded"
        workbook_available = False

    return {
        "review_id": review_id,
        "round_id": target.round_id,
        "questions": len(bundle.rows),
        "answered": bundle.answered,
        "cited": bundle.cited,
        "sendable": bundle.sendable,
        "human_approved": bundle.human_approved,
        "by_release_state": {str(k): v for k, v in bundle.counts.items()},
        "workbook_available": workbook_available,
        "source": provenance,
        "release_rule": RELEASE_RULE,
    }


@router.get("/inbox")
def inbox_status() -> dict[str, Any]:
    """Whether the mailbox is actually being watched, and how recently work arrived.

    Rendered on the fleet page because a lapsed watch is invisible from the outside: Gmail
    expires `users.watch` after seven days without warning, and a mailbox that has stopped
    notifying looks exactly like a mailbox nobody has emailed. `expires_in_hours` going
    negative is the only signal there is, so it is on screen rather than in a log.

    Reads Firestore only. The control plane deliberately holds no Gmail credential -- the
    watched address is recorded beside the history cursor at registration time precisely so
    that this service does not need one. Registering or stopping a watch *does* need the
    credential, and goes through `dispatcher_link` to the service that has it.
    """
    cursor = inbox_state().cursor()
    expiration_ms = int(cursor.get("expiration_ms") or 0)
    expires_at = datetime.fromtimestamp(expiration_ms / 1000, tz=UTC) if expiration_ms else None
    return {
        "watching": bool(expiration_ms),
        "address": cursor.get("address") or "",
        "topic": cursor.get("topic") or "",
        "history_id": cursor.get("history_id") or "",
        "registered_at": cursor.get("registered_at") or "",
        "expires_at": expires_at.isoformat() if expires_at else "",
        "expires_in_hours": (
            round((expires_at - datetime.now(UTC)).total_seconds() / 3600, 1)
            if expires_at
            else None
        ),
        "expired": bool(expires_at and expires_at < datetime.now(UTC)),
    }


# ---------------------------------------------------------------------------------
# Connections -- the page that replaced a CLI command printed in the product
# ---------------------------------------------------------------------------------


@router.get("/connections")
def connections(probe: bool = True) -> dict[str, Any]:
    """What this deployment is connected to, and whether it can be changed from here.

    ## Two sources, and the poorer one always answers

    The mailbox watch state is in Firestore, which this service reads directly, so the page
    can always say whether email is arriving. Everything else -- whether a consent document
    exists at all, whether Gmail can actually publish to the topic, what Drive is scoped to
    -- lives with the dispatcher, which is the only service holding the credential.

    So the dispatcher is asked, and when it cannot be reached the answer is
    `manageable: false` **with the reason**, on top of the Firestore state that is still
    true. What is never returned is "not connected" because a call failed. A Connections
    page reporting a disconnection caused by a service scaling from zero is the
    failure-impersonating-empty shape this codebase has found nine times, on the one page
    whose entire job is reporting whether something is connected.
    """
    local = inbox_status()
    payload: dict[str, Any] = {
        "gmail": {
            "connected": bool(local["watching"]) and not local["expired"],
            "address": local["address"],
            "topic": local["topic"],
            "history_id": local["history_id"],
            "registered_at": local["registered_at"],
            "expires_at": local["expires_at"],
            "expires_in_hours": local["expires_in_hours"],
            "expired": local["expired"],
            "scopes": [],
            "refusal": "",
        },
        "drive": {"connected": False, "scopes": [], "shares_consent_with": "gmail"},
        "manageable": False,
        "unavailable": "",
    }

    if not probe:
        # The fast path, for a first paint. Firestore only, no cross-service call, and
        # `manageable` stays false because nothing has been asked yet -- the page fills the
        # rest in from a second call it makes itself. Probing costs an IAM policy read, a
        # subscription list and a Secret Manager read, which is four round trips more than a
        # page should hold a person's first frame for.
        payload["unavailable"] = ""
        return payload

    try:
        remote = dispatcher_link.call("/connections")
    except dispatcher_link.DispatcherUnreachable as exc:
        payload["unavailable"] = (
            "The service holding the mailbox credential could not be reached, so this "
            f"connection cannot be changed from here right now. {exc}"
        )
        return payload
    except dispatcher_link.DispatcherResponse as exc:
        payload["unavailable"] = f"The connection service answered {exc.status}."
        return payload

    if isinstance(remote, dict):
        # The dispatcher's view wins on everything it knows, because it can see the consent
        # and the topic. The Firestore-derived fields above are the floor, not a preference.
        payload["gmail"] = {**payload["gmail"], **(remote.get("gmail") or {})}
        payload["drive"] = {**payload["drive"], **(remote.get("drive") or {})}
        payload["manageable"] = True
    return payload


@router.post("/connections/gmail")
def connect_gmail(request: Request) -> dict[str, Any]:
    """Connect the mailbox: register the watch that turns inbound email into work.

    This endpoint is the whole point of the Connections page. Before it, the only way to
    start the inbound path was `tools/gmail_watch.py --apply`, and that string was printed
    inside the product as an instruction to the reader.

    A refusal comes back as a **409 carrying the reason**. Gmail will register a watch
    against a topic nobody is subscribed to, return a history id, and drop every
    notification for seven days -- so the dispatcher checks first, and "why not" is the
    most useful thing this call can return.
    """
    require_write_token(request)
    try:
        return dict(dispatcher_link.call("/connections/gmail/watch", method="POST", body={}))
    except dispatcher_link.DispatcherResponse as exc:
        raise HTTPException(exc.status, exc.payload().get("refusal", exc.detail)) from exc
    except dispatcher_link.DispatcherUnreachable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.delete("/connections/gmail")
def disconnect_gmail(request: Request) -> dict[str, Any]:
    """Stop the watch. No further email starts a review until it is registered again."""
    require_write_token(request)
    try:
        return dict(dispatcher_link.call("/connections/gmail/stop", method="POST", body={}))
    except dispatcher_link.DispatcherResponse as exc:
        raise HTTPException(exc.status, exc.payload().get("refusal", exc.detail)) from exc
    except dispatcher_link.DispatcherUnreachable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


class DeliverRequest(BaseModel):
    """Who is authorising the send, and what they want said.

    `approved_by` has no default and cannot be blank. This endpoint is the only one in the
    service whose effect leaves the building, and a default actor -- even one as honest as
    "operator" -- would mean an unattributed email to a customer was one omitted field away.
    """

    approved_by: Actor
    note: str = Field(default="", max_length=2000)


@router.post("/reviews/{review_id}/deliver", status_code=status.HTTP_202_ACCEPTED)
def deliver(review_id: str, body: DeliverRequest, request: Request) -> dict[str, Any]:
    """Send the finished pack back to the customer, in the thread it arrived on.

    **202, and the work is done by the dispatcher.** Not because this could not build a
    workbook -- it does, for `GET /export` -- but because sending is irreversible and
    everything irreversible in this system goes through the durable transport, where it has
    a claim, a lease, a retry policy and a dead-letter path. An email sent inline from a
    request handler has none of those, and a timeout on the client would leave nobody able
    to say whether it went.

    The human gate is in the protocol rather than here: `DeliverPackPayload.approved_by` is
    `min_length=1`, so an envelope without a named person cannot be constructed. This
    endpoint's job is to refuse to make one up.
    """
    require_write_token(request)
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id!r}")

    available = rounds().for_review(review_id)
    if not available:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"review {review_id!r} has no rounds")
    target = available[-1]

    thread = inbox_state().thread_for_review(review_id)
    if not thread or not thread.get("thread_id"):
        # Refused here as well as in the handler, because a 409 a person can read is better
        # than a dead letter they have to go looking for -- and this is the one case the UI
        # can render before the button is pressed.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"review {review_id!r} did not arrive by email, so there is no thread to reply "
            "on. Download the pack from the export instead.",
        )

    run_id = f"deliver-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    envelope = WorkEnvelope.for_work(
        message_id=f"{run_id}-{review_id}",
        review_id=review_id,
        run_id=run_id,
        round_id=target.round_id,
        kind=WorkKind.DELIVER_PACK,
        payload={"approved_by": body.approved_by, "note": body.note},
    )
    publisher().publish(envelope)
    audit().append_safe(
        {
            "kind": "delivery_authorised",
            "review_id": review_id,
            "run_id": run_id,
            "actor": body.approved_by,
            "detail": {
                "round_id": target.round_id,
                "thread_id": thread["thread_id"],
                "to": thread.get("sender"),
                "note": body.note,
                "dedup_key": envelope.dedup_key,
            },
        }
    )
    return {
        "accepted": True,
        "review_id": review_id,
        "round_id": target.round_id,
        "run_id": run_id,
        "dedup_key": envelope.dedup_key,
        "to": thread.get("sender"),
    }


@router.get("/reviews/{review_id}/artifacts")
def list_artifacts(review_id: str) -> list[dict[str, Any]]:
    """Every file this review produced, and where it went.

    A read, so it is open like the other reads. It exposes Drive file ids and links, which
    are not secrets -- the files themselves are not shared with anyone, and a link nobody has
    access to is a string.
    """
    return artifacts().for_review(review_id)


# ---------------------------------------------------------------------------------
# The thread -- the audit trail read back as the conversation that produced it
# ---------------------------------------------------------------------------------

#: What one thread read needs: the review, its rounds, the target round's questions and
#: answers, the audit trail, and whether that trail was cut short.
ThreadInputs = tuple[Review, list[Round], list[Any], list[Any], list[dict[str, Any]], bool]


def _thread_inputs(review_id: str, round_id: str | None) -> ThreadInputs:
    """Everything the projection reads, and whether the audit read hit its ceiling.

    One place, because the thread endpoint and the ask endpoint need exactly the same
    five reads and a drifting pair of them would mean a reply grounded in a different
    round from the one on screen.
    """
    review = reviews().get(review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no review {review_id}")

    all_rounds = rounds().for_review(review_id)
    target = None
    if round_id is not None:
        target = next((r for r in all_rounds if r.round_id == round_id), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no round {round_id}")
    elif all_rounds:
        target = max(all_rounds, key=lambda r: r.ordinal)

    round_questions = questions().for_round(target.round_id) if target else []
    round_answers = answers().for_round(target.round_id) if target else []
    events = audit().for_review(review_id, limit=MAX_THREAD_EVENTS)
    truncated = len(events) >= MAX_THREAD_EVENTS
    return review, all_rounds, round_questions, round_answers, events, truncated


@router.get("/reviews/{review_id}/thread")
def get_thread(review_id: str, round_id: str | None = None) -> dict[str, Any]:
    """The review, as a conversation between the agents that worked it.

    A read over records that already existed. Nothing is written to serve this, and no
    model is called to compose it -- see `attestor_platform.thread` for why both of those
    are load-bearing rather than incidental.

    Aggregated **here** rather than in the browser. Twelve hundred audit events serialised
    into a page payload so the client can pick a dozen summaries out of them is half a
    megabyte of JSON to render fifteen lines, and it is the same mistake as the 312-row
    grid in a different place.
    """
    review, all_rounds, round_questions, round_answers, events, truncated = _thread_inputs(
        review_id, round_id
    )
    thread = build_thread(
        review=review,
        rounds=all_rounds,
        questions=round_questions,
        answers=round_answers,
        events=events,
        artifacts=artifacts().for_review(review_id),
        truncated=truncated,
    )
    return thread.as_dict()


class AskRequest(BaseModel):
    """A question a person typed into the thread."""

    question: str = Field(min_length=1, max_length=1000)
    #: `Actor` rather than a bare string, for the same reason approvals use it: this ends
    #: up in the append-only record as the person who asked, and three spaces is not a
    #: person.
    asked_by: Actor


@router.post("/reviews/{review_id}/ask", status_code=status.HTTP_201_CREATED)
def ask(review_id: str, body: AskRequest, request: Request) -> dict[str, Any]:
    """Answer a question about this review out of its own audit trail.

    ## Why this is a write

    Both halves are appended to `audit_events`: `human_asked` with the person's name on it,
    and `orchestrator_answered` with the reply and the blocks it was built from. A
    conversation about a compliance decision belongs in the compliance record, and storing
    the reply whole is what makes the thread reproducible -- recomposing it on every read
    would let the same question answer differently in June from how it answered in January.

    ## Why it is guarded but not capacity-checked

    `require_write_token` applies because this writes. `require_capacity` does not, because
    the ceiling it enforces is on *fleet* work and this spends nothing: no model call, no
    retrieval, no engine. It reads five collections and runs string templates over them.
    """
    require_write_token(request)
    review, _all_rounds, round_questions, round_answers, events, _truncated = _thread_inputs(
        review_id, None
    )

    run_id = next(
        (str(e["run_id"]) for e in events if e.get("run_id")),
        f"ask-{uuid.uuid4().hex[:8]}",
    )
    round_id = next((str(e["round_id"]) for e in events if e.get("round_id")), None)

    composed = answer_from_trail(
        body.question,
        review=review,
        questions=round_questions,
        answers=round_answers,
        events=events,
    )

    asked_at = datetime.now(UTC).isoformat()
    audit().append_safe(
        {
            "kind": "human_asked",
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": composed.question_id,
            "actor": body.asked_by,
            "occurred_at": asked_at,
            "detail": {"question": body.question},
        }
    )
    audit().append_safe(
        {
            "kind": "orchestrator_answered",
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": composed.question_id,
            "actor": "Orchestrator",
            # One microsecond after the question, so the sort in the projection cannot put
            # the answer above the question it answers. Two `datetime.now()` calls in the
            # same request can return the same value, and a thread that renders the reply
            # first is a thread nobody trusts.
            "occurred_at": _just_after(asked_at),
            "detail": composed.as_detail(),
        }
    )
    return {"asked_at": asked_at, **composed.as_detail()}


class MessageRequest(BaseModel):
    """A line a person typed into the thread. It is either an instruction or a question."""

    text: str = Field(min_length=1, max_length=1000)
    #: `Actor` rather than a bare string, for the same reason approvals use it: this lands
    #: in the append-only record as the person who said it.
    actor: Actor
    #: Set on the second call, carrying the action the first call asked about. An
    #: irreversible command dispatches only when this matches what was recognised.
    confirm: str = ""
    #: Optional covering line, used by `send_pack`.
    note: str = Field(default="", max_length=2000)


@router.post("/reviews/{review_id}/message", status_code=status.HTTP_200_OK)
def message(review_id: str, body: MessageRequest, request: Request) -> dict[str, Any]:
    """One line in, one of three things out: an answer, a confirmation, or dispatched work.

    ## Why one endpoint rather than two

    The client would otherwise have to decide whether a line is a question or an
    instruction, which means shipping the command grammar to the browser and keeping two
    copies of it in step. The grammar lives in `attestor_platform.thread.commands`, it is
    literal rather than fuzzy, and the server is the only thing that reads it.

    ## The three shapes

    * `answered` -- nothing matched a command pattern, so the line went to the audit trail
      answerer. No model call; see `attestor_platform.thread.answering`.
    * `confirm` -- an irreversible command was recognised. Nothing has happened yet. The
      client re-posts with `confirm` set to the action.
    * `dispatched` -- a `WorkEnvelope` is on the bus. The trail carries `human_commanded`
      with the person and the text they typed, so the thread shows the instruction and the
      work it produced next to each other.
    """
    require_write_token(request)
    review, all_rounds, round_questions, round_answers, events, _truncated = _thread_inputs(
        review_id, None
    )
    target = max(all_rounds, key=lambda item: item.ordinal, default=None)

    command = parse_command(
        body.text,
        resolve_question=lambda line: resolve_reference(line, round_questions),
    )
    if command is None:
        composed = answer_from_trail(
            body.text,
            review=review,
            questions=round_questions,
            answers=round_answers,
            events=events,
        )
        asked_at = _record_exchange(review_id, events, body.actor, body.text, composed)
        return {"kind": "answered", "asked_at": asked_at, **composed.as_detail()}

    if command.irreversible and body.confirm != command.action.value:
        # Nothing is written here. A confirmation prompt is not an event.
        return {
            "kind": "confirm",
            "action": command.action.value,
            "prompt": command.prompt,
            "text": command.text,
        }

    if target is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"review {review_id!r} has no rounds")

    return {"kind": "dispatched", **_dispatch_command(review, target, command, body)}


def _record_exchange(
    review_id: str,
    events: list[dict[str, Any]],
    actor: str,
    text: str,
    composed: Any,
) -> str:
    """Append both halves of a question and its answer. Returns when it was asked."""
    run_id = next(
        (str(e["run_id"]) for e in events if e.get("run_id")),
        f"ask-{uuid.uuid4().hex[:8]}",
    )
    round_id = next((str(e["round_id"]) for e in events if e.get("round_id")), None)
    asked_at = datetime.now(UTC).isoformat()
    audit().append_safe(
        {
            "kind": "human_asked",
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": composed.question_id,
            "actor": actor,
            "occurred_at": asked_at,
            "detail": {"question": text},
        }
    )
    audit().append_safe(
        {
            "kind": "orchestrator_answered",
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": composed.question_id,
            "actor": "Orchestrator",
            "occurred_at": _just_after(asked_at),
            "detail": composed.as_detail(),
        }
    )
    return asked_at


#: A recognised command to the envelope it publishes. `export` is the one that publishes
#: nothing: the workbook is built on demand by `GET /export`, so the command opens the
#: panel and the trail records that somebody asked for it.
_COMMAND_KINDS: dict[CommandAction, WorkKind | None] = {
    CommandAction.SEND_PACK: WorkKind.DELIVER_PACK,
    CommandAction.REDRAFT: WorkKind.DRAFT_ANSWER,
    CommandAction.EXPORT: None,
}


def _dispatch_command(
    review: Review, target: Round, command: Command, body: MessageRequest
) -> dict[str, Any]:
    """Publish the work a recognised command asks for, and record who asked for it.

    `human_commanded` is appended before the publish, with the name and the line typed
    verbatim. Of the two orderings, a record of an instruction whose publish then failed is
    the one that can be investigated; a publish with no record of who asked is not.
    """
    kind = _COMMAND_KINDS[command.action]
    run_id = f"cmd-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    payload: dict[str, Any] = {}

    if command.action is CommandAction.SEND_PACK:
        thread = inbox_state().thread_for_review(review.review_id)
        if not thread or not thread.get("thread_id"):
            # Refused here rather than in the handler, so a person reading the thread gets
            # a sentence instead of a dead letter they have to go looking for.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"review {review.review_id!r} did not arrive by email, so there is no "
                "thread to reply on.",
            )
        payload = {"approved_by": body.actor, "note": body.note}

    envelope = None
    if kind is not None:
        envelope = WorkEnvelope.for_work(
            message_id=f"{run_id}-{review.review_id}",
            review_id=review.review_id,
            run_id=run_id,
            round_id=target.round_id,
            # A redraft names its question on the ENVELOPE, not in the payload.
            # `DRAFT_ANSWER` validates against `EmptyPayload`, which forbids extras, so a
            # `question_ids` field would fail at publish -- and the envelope has carried a
            # `question_id` since Phase 1 precisely so per-question work needs no payload.
            question_id=command.question_id,
            kind=kind,
            payload=payload,
        )

    audit().append_safe(
        {
            "kind": "human_commanded",
            "review_id": review.review_id,
            "run_id": run_id,
            "round_id": target.round_id,
            "question_id": command.question_id,
            "actor": body.actor,
            "detail": {
                **command.as_detail(),
                "work": kind.value if kind is not None else "none",
                "dedup_key": envelope.dedup_key if envelope is not None else "",
            },
        }
    )
    if envelope is not None:
        publisher().publish(envelope)

    return {
        "action": command.action.value,
        "run_id": run_id,
        "round_id": target.round_id,
        "work": kind.value if kind is not None else "none",
        "dedup_key": envelope.dedup_key if envelope is not None else "",
        "question_id": command.question_id,
        "question_label": command.question_label,
    }


def _just_after(iso: str) -> str:
    """One microsecond later, as a string. See the call site for why."""
    return (datetime.fromisoformat(iso) + timedelta(microseconds=1)).isoformat()


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
