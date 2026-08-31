"""Firestore repositories.

Two rules hold across this module:

1. **The event collections are append-only, structurally.** `AuditEventRepository` and
   `ArmorEventRepository` have no `update` and no `delete` method. Not "should not be
   called" -- not present. An audit trail you can edit is not an audit trail, and the
   audit trail is the deliverable here.

2. **Every call has a timeout and a defined failure path.** Firestore's client retries
   internally and will otherwise happily block a Cloud Run request until the instance
   is culled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore

from attestor_core.domain import Answer, Commitment, Question, Review, Round
from attestor_core.domain.enums import AnswerStatus
from attestor_core.errors import AttestorError, ContextUnavailable
from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

#: Every external call gets an explicit deadline.
DEFAULT_TIMEOUT_SECONDS = 20.0

REVIEWS = "reviews"
ROUNDS = "rounds"
QUESTIONS = "questions"
ANSWERS = "answers"
COMMITMENTS = "commitments"
AUDIT_EVENTS = "audit_events"
ARMOR_EVENTS = "armor_events"


def _client(project: str | None = None) -> firestore.Client:
    return firestore.Client(project=project or project_id())


class _Repository:
    """Shared plumbing. Not a base class with behaviour -- just construction."""

    def __init__(
        self,
        client: firestore.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._db = client if client is not None else _client()
        self._timeout = timeout


def _count(query: Any, timeout: float) -> int:
    """One Firestore COUNT aggregation, as an int.

    Its own function because the result shape is three levels of nesting deep and reads
    like a mistake at every call site. Degrades to 0 only when the aggregation returns
    nothing at all, which is not the same as a failed read: a `GoogleAPIError` propagates,
    because "this round has no answers" and "the count could not be taken" must not be the
    same value.
    """
    try:
        result = query.count().get(timeout=timeout)
    except gexc.GoogleAPIError as exc:
        # Raised, never returned as 0. A board that showed "0 held" because an aggregation
        # failed would send somebody past the review that is waiting on them -- the exact
        # empty-impersonating-failure shape this codebase has now found nine times.
        raise ContextUnavailable(f"a Firestore count could not be taken: {exc}") from exc
    for row in result:
        for item in row:
            return int(item.value)
    return 0


class ReviewRepository(_Repository):
    """Reviews. Mutable: a review's state legitimately changes."""

    def get(self, review_id: str) -> Review | None:
        snap = self._db.collection(REVIEWS).document(review_id).get(timeout=self._timeout)
        return Review.model_validate(snap.to_dict()) if snap.exists else None

    def set_auto_send(self, review_id: str, *, enabled: bool, actor: str, at: str) -> None:
        """Set the auto-send flag without rewriting the rest of the document.

        `put` writes a whole `Review`, so a caller holding a copy read minutes ago silently
        reverts every field changed since. This flag is set from the console while the
        dispatcher is moving the same review through its states, which is exactly that race
        -- and it was measured losing: enabled at 05:39:15, gone by 05:42:19.
        """
        self._db.collection(REVIEWS).document(review_id).update(
            {
                "auto_send": enabled,
                "auto_send_enabled_by": actor if enabled else "",
                "auto_send_enabled_at": at if enabled else "",
            },
            timeout=self._timeout,
        )

    def put(self, review: Review) -> None:
        self._db.collection(REVIEWS).document(review.review_id).set(
            review.model_dump(mode="json"), timeout=self._timeout
        )

    def list_all(self, limit: int = 50) -> list[Review]:
        query = self._db.collection(REVIEWS).order_by(
            "created_at", direction=firestore.Query.DESCENDING
        )
        return [
            Review.model_validate(doc.to_dict())
            for doc in query.limit(limit).stream(timeout=self._timeout)
        ]


class RoundRepository(_Repository):
    def get(self, round_id: str) -> Round | None:
        snap = self._db.collection(ROUNDS).document(round_id).get(timeout=self._timeout)
        return Round.model_validate(snap.to_dict()) if snap.exists else None

    def put(self, round_: Round) -> None:
        self._db.collection(ROUNDS).document(round_.round_id).set(
            round_.model_dump(mode="json"), timeout=self._timeout
        )

    def for_review(self, review_id: str) -> list[Round]:
        query = self._db.collection(ROUNDS).where("review_id", "==", review_id)
        rounds = [Round.model_validate(d.to_dict()) for d in query.stream(timeout=self._timeout)]
        return sorted(rounds, key=lambda r: r.ordinal)


class QuestionRepository(_Repository):
    """Questions are keyed by their content-derived id, scoped under a round."""

    def _doc_id(self, round_id: str, question_id: str) -> str:
        return f"{round_id}__{question_id}"

    def put_many(self, round_id: str, questions: list[Question]) -> int:
        batch = self._db.batch()
        for question in questions:
            ref = self._db.collection(QUESTIONS).document(
                self._doc_id(round_id, question.question_id)
            )
            payload = question.model_dump(mode="json")
            payload["round_id"] = round_id
            batch.set(ref, payload)
        batch.commit(timeout=self._timeout)
        return len(questions)

    def for_round(self, round_id: str) -> list[Question]:
        query = self._db.collection(QUESTIONS).where("round_id", "==", round_id)
        return [
            Question.model_validate(
                {k: v for k, v in (d.to_dict() or {}).items() if k != "round_id"}
            )
            for d in query.stream(timeout=self._timeout)
        ]

    def count_for_round(self, round_id: str) -> int:
        """How many questions this round has, without reading any of them.

        A server-side aggregation. The reviews board needs this figure for every visible
        review, and streaming 312 documents per row to compute a length would make a list
        page read the whole answers collection.
        """
        query = self._db.collection(QUESTIONS).where("round_id", "==", round_id)
        return _count(query, self._timeout)


class AnswerRepository(_Repository):
    def _doc_id(self, round_id: str, question_id: str) -> str:
        return f"{round_id}__{question_id}"

    def put(self, answer: Answer) -> None:
        self._db.collection(ANSWERS).document(
            self._doc_id(answer.round_id, answer.question_id)
        ).set(answer.model_dump(mode="json"), timeout=self._timeout)

    def get(self, round_id: str, question_id: str) -> Answer | None:
        snap = (
            self._db.collection(ANSWERS)
            .document(self._doc_id(round_id, question_id))
            .get(timeout=self._timeout)
        )
        return Answer.model_validate(snap.to_dict()) if snap.exists else None

    def for_round(self, round_id: str) -> list[Answer]:
        query = self._db.collection(ANSWERS).where("round_id", "==", round_id)
        return [Answer.model_validate(d.to_dict()) for d in query.stream(timeout=self._timeout)]

    def count_for_round(self, round_id: str, status: AnswerStatus | None = None) -> int:
        """How many answers this round has, optionally in one status. Server-side."""
        query = self._db.collection(ANSWERS).where("round_id", "==", round_id)
        if status is not None:
            query = query.where("status", "==", status.value)
        return _count(query, self._timeout)


class CommitmentRepository(_Repository):
    """Durable statements made to a customer.

    Written once when a round closes and read at the start of every later round. There
    is no update method: you cannot retroactively change what you told a customer in
    July, and the whole consistency guarantee rests on that.
    """

    def put(self, commitment: Commitment) -> None:
        self._db.collection(COMMITMENTS).document(commitment.commitment_id).set(
            commitment.model_dump(mode="json"), timeout=self._timeout
        )

    def for_review(self, review_id: str) -> list[Commitment]:
        query = self._db.collection(COMMITMENTS).where("review_id", "==", review_id)
        commitments = [
            Commitment.model_validate(d.to_dict()) for d in query.stream(timeout=self._timeout)
        ]
        return sorted(commitments, key=lambda c: c.made_at)

    def count_for_review(self, review_id: str) -> int:
        return len(self.for_review(review_id))


class _AppendOnlyEventRepository(_Repository):
    """Append-only event log.

    Deliberately exposes only `append` and readers. No `update`, no `delete`, no
    `set` on an existing id -- the absence is the guarantee.
    """

    _collection: str

    def append(self, event: dict[str, Any]) -> str:
        """Append one event. Returns its generated id.

        Non-fatal by contract: callers use `append_safe` on hot paths.
        """
        payload = dict(event)
        payload.setdefault("occurred_at", datetime.now(UTC).isoformat())
        _, ref = self._db.collection(self._collection).add(payload, timeout=self._timeout)
        return str(ref.id)

    def append_safe(self, event: dict[str, Any]) -> str | None:
        """Append, logging loudly on failure but never raising.

        A failed audit write must never block a run. Losing one audit line is bad;
        failing a 312-question review because the audit collection had a blip is worse,
        and the run itself is still reconstructable from Cloud Trace.
        """
        try:
            return self.append(event)
        except (gexc.GoogleAPIError, AttestorError, OSError) as exc:
            logger.error(
                "audit write FAILED (run continues): collection=%s error=%s",
                self._collection,
                exc,
                exc_info=True,
                extra={
                    "collection": self._collection,
                    **{k: event.get(k) for k in ("review_id", "run_id")},
                },
            )
            return None

    def for_review(self, review_id: str, limit: int = 500) -> list[dict[str, Any]]:
        query = self._db.collection(self._collection).where("review_id", "==", review_id)
        return [dict(d.to_dict() or {}) for d in query.limit(limit).stream(timeout=self._timeout)]

    def auto_send_enabled(self, review_id: str) -> tuple[bool, str, str]:
        """Whether auto-send is on for this review, from the append-only record.

        Returns `(enabled, who_set_it, when)`. The review *document* also carries the flag
        and is the fast read for the console, but it is written whole on every state
        transition -- so a toggle set while a run is in flight is reliably lost, which is
        what two live tests showed before this existed. `audit_events` is append-only;
        nothing rewrites it.

        The most recent `auto_send_changed` wins, so turning it off is honoured the same way
        turning it on is. No event means off, which is the safe direction: a review nobody
        has spoken about does not email a customer by itself.
        """
        changes = [
            event
            for event in self.for_review(review_id)
            if event.get("kind") == "auto_send_changed"
        ]
        if not changes:
            return (False, "", "")
        latest = max(changes, key=lambda event: str(event.get("occurred_at") or ""))
        detail = latest.get("detail") or {}
        return (
            bool(detail.get("enabled")),
            str(latest.get("actor") or ""),
            str(detail.get("at") or latest.get("occurred_at") or ""),
        )

    def for_run(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        query = self._db.collection(self._collection).where("run_id", "==", run_id)
        return [dict(d.to_dict() or {}) for d in query.limit(limit).stream(timeout=self._timeout)]

    def for_run_since(self, run_id: str, since_seq: int, limit: int = 500) -> list[dict[str, Any]]:
        """Events for one run after a sequence point, oldest first.

        Ordered by `created_at` rather than by a stored sequence: `seq` is assigned by
        the SSE stream at delivery time, in one place, so that two control-plane
        instances streaming the same run cannot disagree about numbering.
        """
        query = (
            self._db.collection(self._collection)
            .where("run_id", "==", run_id)
            .order_by("created_at")
        )
        rows = [dict(d.to_dict() or {}, event_id=d.id) for d in query.stream(timeout=self._timeout)]
        return rows[since_seq : since_seq + limit]

    def watch_run(self, run_id: str, on_event: Callable[[dict[str, Any]], None]) -> Any:
        """Attach a Firestore snapshot listener for one run.

        Returns the watch handle so the caller can unsubscribe. The callback fires on a
        Firestore-owned thread, so the caller is responsible for getting back onto its
        own loop -- doing that here would couple this repository to asyncio.
        """
        query = self._db.collection(self._collection).where("run_id", "==", run_id)

        def _on_snapshot(documents: Any, changes: Any, read_time: Any) -> None:
            del documents, read_time
            for change in changes:
                if change.type.name != "ADDED":
                    continue
                snapshot = change.document
                on_event(dict(snapshot.to_dict() or {}, event_id=snapshot.id))

        return query.on_snapshot(_on_snapshot)


class AuditEventRepository(_AppendOnlyEventRepository):
    """Compliance observability: "why did we answer yes to Q112?", six months later.

    Distinct from Cloud Trace on purpose. Trace is *engineering* observability --
    latency, token cost, tool spans, ~30-day retention. This is the immutable,
    queryable, exportable record an auditor reads. Different consumers, different
    retention, different schemas. Conflating them is the common mistake.
    """

    _collection = AUDIT_EVENTS


class ArmorEventRepository(_AppendOnlyEventRepository):
    """Every Model Armor verdict, including the allows.

    Recording only blocks would make the `/armor` page look like nothing ever happens
    until something does, and would make "how often does this fire?" unanswerable.
    """

    _collection = ARMOR_EVENTS


class RoundSourceRepository(_Repository):
    """Where a round's questionnaire came from, so the export can hand it back.

    ## Why this is not a field on `Round`

    `Round` is a strict pydantic model with `extra="forbid"`, and it is one of the shapes
    the type generator emits into `services/web/lib/types/generated.ts`. Adding a field to
    it is an edit to the frozen protocol and would put an infrastructure detail — a GCS URI
    — into the domain vocabulary and into the browser's type contract.

    The dispatcher already learned this the hard way in the opposite direction: writing
    `drafted_partitions` onto the round document made every read fail with
    `extra_forbidden`, which is why the drafting join lives in its own `round_progress`
    collection. This is the same shape of fact — bookkeeping the services need and the
    domain does not — so it gets the same treatment.

    ## Why the export needs it at all

    Returning the customer's own workbook means re-opening the file they uploaded. The
    control plane knows that URI at the moment it starts a round and validates the object
    exists; nothing else in the system remembers it in a queryable place. Reviews started
    before this collection existed are covered by an audit-trail fallback in the export
    handler, labelled as such.
    """

    _collection = "round_sources"

    def put(self, round_id: str, gcs_uri: str, *, original_filename: str = "") -> None:
        self._db.collection(self._collection).document(round_id).set(
            {
                "round_id": round_id,
                "gcs_uri": gcs_uri,
                "original_filename": original_filename or gcs_uri.rsplit("/", 1)[-1],
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            timeout=self._timeout,
        )

    def get(self, round_id: str) -> str | None:
        snap = self._db.collection(self._collection).document(round_id).get(timeout=self._timeout)
        if not snap.exists:
            return None
        uri = (snap.to_dict() or {}).get("gcs_uri")
        return str(uri) if uri else None


class InboxStateRepository(_Repository):
    """Where the mailbox watch got to, and which thread belongs to which review.

    Two facts, one collection, because both are singleton-ish bookkeeping about one
    mailbox and neither belongs in the domain vocabulary -- same reasoning as
    `RoundSourceRepository`.

    ## The history cursor

    A Gmail notification says only "this mailbox changed, and here is the new history id".
    Getting from that to "these messages arrived" requires the *previous* id, so it is
    stored. Advanced only after the messages of a delta have been published, so a crash
    between the two redelivers them -- at-least-once, which the dedup key already handles,
    rather than at-most-once, which would lose an email silently.

    ## The thread index

    A reply on a thread Attestor already knows is a follow-up round on an existing review,
    and `threadId` is the only durable link between the two. Without this a customer's
    reply three weeks later would create a second, unrelated review and the commitments
    from round one would never be loaded -- which is precisely the cross-round consistency
    guarantee, defeated at the front door.
    """

    _collection = "gmail_state"
    _cursor_doc = "watch"

    def cursor(self) -> dict[str, Any]:
        snap = (
            self._db.collection(self._collection)
            .document(self._cursor_doc)
            .get(timeout=self._timeout)
        )
        return dict(snap.to_dict() or {}) if snap.exists else {}

    def record_watch(
        self, history_id: str, expiration_ms: int, topic: str, address: str = ""
    ) -> None:
        """Record a registration. `address` is stored so the control plane can name the
        watched mailbox without holding the Gmail credential itself -- a read-only service
        that has to read a refresh token to render a status line is a worse trade than a
        string in Firestore."""
        self._db.collection(self._collection).document(self._cursor_doc).set(
            {
                "history_id": history_id,
                "expiration_ms": expiration_ms,
                "topic": topic,
                "address": address,
                "registered_at": datetime.now(UTC).isoformat(),
            },
            merge=True,
        )

    def advance(self, history_id: str) -> None:
        self._db.collection(self._collection).document(self._cursor_doc).set(
            {"history_id": history_id, "advanced_at": datetime.now(UTC).isoformat()},
            merge=True,
        )

    def review_for_thread(self, thread_id: str) -> str | None:
        snap = (
            self._db.collection(self._collection)
            .document(f"thread-{thread_id}")
            .get(timeout=self._timeout)
        )
        if not snap.exists:
            return None
        review_id = (snap.to_dict() or {}).get("review_id")
        return str(review_id) if review_id else None

    def bind_thread(
        self, thread_id: str, review_id: str, *, customer: str = "", sender: str = ""
    ) -> None:
        self._db.collection(self._collection).document(f"thread-{thread_id}").set(
            {
                "thread_id": thread_id,
                "review_id": review_id,
                "customer": customer,
                "sender": sender,
                "bound_at": datetime.now(UTC).isoformat(),
            },
            timeout=self._timeout,
        )

    def thread_for_review(self, review_id: str) -> dict[str, Any] | None:
        """The reverse lookup, for replying to the thread a review came in on."""
        query = self._db.collection(self._collection).where("review_id", "==", review_id).limit(1)
        for doc in query.stream(timeout=self._timeout):
            return dict(doc.to_dict() or {})
        return None


class ArtifactRepository(_Repository):
    """Every file a review produced, and where it went.

    ## Why a collection and not a field on `Review`

    Same reasoning as `RoundSourceRepository`: `Review` is a strict model that the type
    generator emits into the browser's contract, and a growing list of Drive file ids is
    bookkeeping the services need rather than domain vocabulary. The dispatcher already
    learned this in the other direction, when `drafted_partitions` on the round document made
    every read fail with `extra_forbidden`.

    ## Append-only in practice, and deliberately not enforced as such

    Unlike the audit repositories, this one has no structural guarantee against rewriting a
    row -- and that is honest rather than lax. An artifact record is a *pointer*, and a
    pointer to a file that has been replaced should be updated, not duplicated. What must
    never be edited is the audit event that says the pack was sent, which lives in the
    append-only collection where it cannot be.
    """

    _collection = "artifacts"

    def _doc_id(self, review_id: str, kind: str, round_id: str) -> str:
        return f"{review_id}__{round_id}__{kind}"

    def put(
        self,
        review_id: str,
        round_id: str,
        kind: str,
        *,
        file_id: str,
        name: str,
        mime_type: str,
        link: str,
        size_bytes: int = 0,
        produced_by: str = "Dispatcher",
    ) -> None:
        self._db.collection(self._collection).document(self._doc_id(review_id, kind, round_id)).set(
            {
                "review_id": review_id,
                "round_id": round_id,
                "kind": kind,
                "file_id": file_id,
                "name": name,
                "mime_type": mime_type,
                "link": link,
                "size_bytes": size_bytes,
                "produced_by": produced_by,
                "produced_at": datetime.now(UTC).isoformat(),
            },
            timeout=self._timeout,
        )

    def for_review(self, review_id: str) -> list[dict[str, Any]]:
        query = self._db.collection(self._collection).where("review_id", "==", review_id)
        rows = [dict(d.to_dict() or {}) for d in query.stream(timeout=self._timeout)]
        return sorted(rows, key=lambda r: str(r.get("produced_at") or ""))
