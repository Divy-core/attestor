"""The write endpoints, driven through FastAPI with fakes underneath.

## Why this file exists, in one sentence

`POST /reviews` raised a `ValidationError` on every call from Phase 2 until Phase 6.5 — it
passed `current_round=0` into a model whose field is `Field(ge=1)` — and nothing caught it,
because nothing called it. Every review in the project had been created by a tool publishing
`intake_document` to Pub/Sub directly. The endpoint type-checked, linted, and was dead.

`tools/verify_journey.py` found it against the deployed service. These tests are what stop it
coming back without needing a deploy, and they cover the same shape of risk on the other write
paths: an endpoint that is *only* reachable from a browser is an endpoint no other test in this
repo exercises.

## What is faked and what is not

The repositories, the publisher and the storage client are fakes. `core.state.transition`,
`WorkEnvelope.for_work`, `guard.require_write_token` and `guard.require_capacity` are the real
things — those are where the decisions are, and faking them would leave these tests asserting
that FastAPI can route.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from attestor_core.domain import Answer, AnswerStatus, Citation, Confidence, Review, Round
from attestor_core.domain.enums import Framework, Residency, ReviewState
from control_plane import api

TOKEN = "test-write-token"  # noqa: S105 - a fixture, not a credential
HEADERS = {"X-Attestor-Token": TOKEN}


class _Reviews:
    def __init__(self) -> None:
        self.store: dict[str, Review] = {}

    def get(self, review_id: str) -> Review | None:
        return self.store.get(review_id)

    def put(self, review: Review) -> None:
        self.store[review.review_id] = review

    def list_all(self, limit: int = 50) -> list[Review]:
        return list(self.store.values())[:limit]


class _Rounds:
    def __init__(self) -> None:
        self.store: dict[str, Round] = {}

    def get(self, round_id: str) -> Round | None:
        return self.store.get(round_id)

    def put(self, round_: Round) -> None:
        self.store[round_.round_id] = round_

    def for_review(self, review_id: str) -> list[Round]:
        return sorted(
            (r for r in self.store.values() if r.review_id == review_id),
            key=lambda r: r.ordinal,
        )


class _Answers:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], Answer] = {}

    def get(self, round_id: str, question_id: str) -> Answer | None:
        return self.store.get((round_id, question_id))

    def put(self, answer: Answer) -> None:
        self.store[(answer.round_id, answer.question_id)] = answer

    def for_round(self, round_id: str) -> list[Answer]:
        return [a for a in self.store.values() if a.round_id == round_id]


class _Questions:
    def for_round(self, round_id: str) -> list[Any]:
        return []


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_safe(self, event: dict[str, Any]) -> str:
        self.events.append(event)
        return "event-1"

    def for_review(self, review_id: str, limit: int = 500) -> list[dict[str, Any]]:
        return list(self.events)


class _Publisher:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, envelope: Any) -> None:
        self.published.append(envelope)


class _Storage:
    """Signs nothing and stores nothing; records what it was asked for."""

    def __init__(self, *, present: bool = True) -> None:
        self.present = present
        self.signed: list[tuple[str, str]] = []

    def signed_upload_url(self, object_name: str, content_type: str) -> tuple[str, str, Any]:
        from datetime import UTC, datetime, timedelta

        self.signed.append((object_name, content_type))
        return (
            f"https://storage.googleapis.com/signed/{object_name}",
            f"gs://attestor-test-uploads/{object_name}",
            datetime.now(UTC) + timedelta(minutes=30),
        )

    def exists(self, gcs_uri: str) -> bool:
        return self.present


class _Sources:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def put(self, round_id: str, gcs_uri: str, *, original_filename: str = "") -> None:
        self.store[round_id] = gcs_uri

    def get(self, round_id: str) -> str | None:
        return self.store.get(round_id)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", TOKEN)
    parts = {
        "reviews": _Reviews(),
        "rounds": _Rounds(),
        "answers": _Answers(),
        "questions": _Questions(),
        "audit": _Audit(),
        "publisher": _Publisher(),
        "storage": _Storage(),
        "sources": _Sources(),
    }
    monkeypatch.setattr(api, "reviews", lambda: parts["reviews"])
    monkeypatch.setattr(api, "rounds", lambda: parts["rounds"])
    monkeypatch.setattr(api, "answers", lambda: parts["answers"])
    monkeypatch.setattr(api, "questions", lambda: parts["questions"])
    monkeypatch.setattr(api, "audit", lambda: parts["audit"])
    monkeypatch.setattr(api, "publisher", lambda: parts["publisher"])
    monkeypatch.setattr(api, "storage", lambda: parts["storage"])
    monkeypatch.setattr(api, "round_sources", lambda: parts["sources"])

    app = FastAPI()
    app.include_router(api.build_app_routes())
    parts["client"] = TestClient(app)
    return type("Wired", (), parts)()


def _review(state: ReviewState, review_id: str = "rev-existing") -> Review:
    return Review(
        review_id=review_id,
        customer="Existing Customer",
        framework=Framework.CAIQ,
        residency=Residency.US,
        current_round=1,
        state=state,
    )


# ---------------------------------------------------------------------------------


class TestCreateReview:
    def test_it_actually_works(self, wired: Any) -> None:
        """The regression this whole file exists for.

        `current_round=0` against `Field(ge=1)` made this a 500 on every call for four phases.
        """
        response = wired.client.post(
            "/reviews",
            json={"customer": "Northwind Traders", "framework": "caiq", "residency": "eu"},
            headers=HEADERS,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["customer"] == "Northwind Traders"
        assert body["state"] == "intake"
        # Round 1 is the initial questionnaire, per the model's own comment.
        assert body["current_round"] == 1
        # And it round-trips through the model, which is what the 0 broke.
        assert Review.model_validate(body).review_id == body["review_id"]

    def test_the_defaults_are_usable(self, wired: Any) -> None:
        # The dialog always sends all three, but a caller that sends only a customer must not
        # get a 422 -- the request model's defaults exist to be used.
        response = wired.client.post("/reviews", json={"customer": "Minimal"}, headers=HEADERS)
        assert response.status_code == 201, response.text

    def test_no_token_is_refused_and_nothing_is_created(self, wired: Any) -> None:
        response = wired.client.post("/reviews", json={"customer": "Nope"})
        assert response.status_code == 401
        assert wired.reviews.store == {}

    def test_the_capacity_ceiling_refuses_the_fourth(self, wired: Any) -> None:
        for index in range(3):
            wired.reviews.put(_review(ReviewState.DRAFTING, f"rev-busy-{index}"))
        response = wired.client.post("/reviews", json={"customer": "Fourth"}, headers=HEADERS)
        assert response.status_code == 429
        assert "rev-busy-0" in response.json()["detail"]

    def test_delivered_reviews_do_not_count_against_the_ceiling(self, wired: Any) -> None:
        for index in range(5):
            wired.reviews.put(_review(ReviewState.DELIVERED, f"rev-done-{index}"))
        response = wired.client.post("/reviews", json={"customer": "Sixth"}, headers=HEADERS)
        assert response.status_code == 201, response.text


class TestUploads:
    def test_it_signs_for_the_content_type_it_was_given(self, wired: Any) -> None:
        response = wired.client.post(
            "/uploads",
            json={"filename": "caiq.xlsx", "content_type": "application/pdf"},
            headers=HEADERS,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        # The browser must PUT with exactly this, or the signature does not match.
        assert body["headers"]["Content-Type"] == "application/pdf"
        assert body["method"] == "PUT"
        assert wired.storage.signed[0][1] == "application/pdf"

    def test_an_unguarded_signed_url_minter_is_refused(self, wired: Any) -> None:
        # A signed URL is a 30-minute write grant into our own bucket. Minting must be guarded
        # even though the endpoint itself writes nothing.
        assert wired.client.post("/uploads", json={"filename": "x.xlsx"}).status_code == 401
        assert wired.storage.signed == []


class TestStartARound:
    def test_it_publishes_and_records_the_source(self, wired: Any) -> None:
        wired.reviews.put(_review(ReviewState.INTAKE, "rev-start"))
        response = wired.client.post(
            "/reviews/rev-start/rounds",
            json={"gcs_uri": "gs://attestor-test-uploads/q/caiq.xlsx", "ordinal": 1},
            headers=HEADERS,
        )
        # 202: the round exists, the answers will not for minutes.
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["kind"] == "intake_document"
        assert body["stream"] == f"/runs/{body['run_id']}/events"
        assert len(wired.publisher.published) == 1
        # The export needs the questionnaire this round came from, and this is the only moment
        # the system knows it.
        assert wired.sources.get(body["round_id"]) == "gs://attestor-test-uploads/q/caiq.xlsx"

    def test_a_missing_object_fails_at_the_call_site(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wired.reviews.put(_review(ReviewState.INTAKE, "rev-start"))
        monkeypatch.setattr(api, "storage", lambda: _Storage(present=False))
        response = wired.client.post(
            "/reviews/rev-start/rounds",
            json={"gcs_uri": "gs://attestor-test-uploads/gone.xlsx"},
            headers=HEADERS,
        )
        # 400 here rather than a dead letter minutes later.
        assert response.status_code == 400
        assert wired.publisher.published == []

    def test_a_review_in_flight_may_still_start_a_later_round(self, wired: Any) -> None:
        """The `starting=` exclusion, which a naive ceiling would get wrong.

        Three reviews in flight and one of them is the one opening round 2. Counting it against
        its own ceiling would make follow-up rounds impossible exactly when the system is busy.
        """
        wired.reviews.put(_review(ReviewState.FOLLOW_UP, "rev-start"))
        wired.reviews.put(_review(ReviewState.DRAFTING, "rev-other-1"))
        wired.reviews.put(_review(ReviewState.DRAFTING, "rev-other-2"))
        response = wired.client.post(
            "/reviews/rev-start/rounds",
            json={"gcs_uri": "gs://attestor-test-uploads/r2.xlsx", "ordinal": 2},
            headers=HEADERS,
        )
        assert response.status_code == 202, response.text
        assert wired.publisher.published[0].kind.value == "open_follow_up"

    def test_the_ceiling_still_refuses_a_new_review_at_the_limit(self, wired: Any) -> None:
        wired.reviews.put(_review(ReviewState.INTAKE, "rev-start"))
        for index in range(3):
            wired.reviews.put(_review(ReviewState.DRAFTING, f"rev-busy-{index}"))
        response = wired.client.post(
            "/reviews/rev-start/rounds",
            json={"gcs_uri": "gs://attestor-test-uploads/q.xlsx"},
            headers=HEADERS,
        )
        assert response.status_code == 429


class TestApproval:
    def _pending(self, wired: Any) -> None:
        wired.reviews.put(_review(ReviewState.AWAITING_HUMAN, "rev-hold"))
        wired.answers.put(
            Answer(
                question_id="a" * 16,
                round_id="rev-hold-r1",
                text="Held for a human.",
                citations=[
                    Citation(
                        document_uri="gs://corpus/policy.md",
                        document_title="Policy",
                        snippet="A control exists.",
                        retrieval_score=0.5,
                    )
                ],
                confidence=Confidence.LOW,
                status=AnswerStatus.NEEDS_HUMAN,
                authored_by="SecurityAgent",
            )
        )

    def test_it_publishes_a_resume_and_names_the_operator(self, wired: Any) -> None:
        self._pending(wired)
        response = wired.client.post(
            f"/rounds/rev-hold-r1/answers/{'a' * 16}/approval",
            json={"approved": True, "resolved_by": "divy@kestreldata.example"},
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] is True
        # Applied by the dispatcher, not here, so a redelivery is idempotent rather than
        # usually-fine.
        assert wired.answers.get("rev-hold-r1", "a" * 16).status is AnswerStatus.NEEDS_HUMAN
        assert len(wired.publisher.published) == 1
        decision = [e for e in wired.audit.events if e["kind"] == "human_decision"]
        assert decision[0]["actor"] == "divy@kestreldata.example"

    def test_approval_is_not_blocked_by_the_capacity_ceiling(self, wired: Any) -> None:
        """Approving is how a review LEAVES the in-flight set.

        Refusing it because too many reviews are in flight would deadlock the very thing the
        ceiling exists to keep bounded.
        """
        self._pending(wired)
        for index in range(4):
            wired.reviews.put(_review(ReviewState.DRAFTING, f"rev-busy-{index}"))
        response = wired.client.post(
            f"/rounds/rev-hold-r1/answers/{'a' * 16}/approval",
            json={"approved": True, "resolved_by": "divy@kestreldata.example"},
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text

    def test_an_unknown_answer_is_a_404_and_publishes_nothing(self, wired: Any) -> None:
        response = wired.client.post(
            f"/rounds/rev-hold-r1/answers/{'b' * 16}/approval",
            json={"approved": True, "resolved_by": "someone"},
            headers=HEADERS,
        )
        assert response.status_code == 404
        assert wired.publisher.published == []


class TestReadsAreNotGuarded:
    @pytest.mark.parametrize("path", ["/reviews", "/reviews?limit=5"])
    def test_a_read_needs_no_token(self, wired: Any, path: str) -> None:
        assert wired.client.get(path).status_code == 200
