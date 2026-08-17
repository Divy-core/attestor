"""The front door's lock, and the two ceilings.

What is asserted here is the *refusal*, not the configuration. A guard that reads its token
correctly and then lets the request through is the shape of bug that only shows up as a
credit bill, so every test here checks that something was actually stopped.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from attestor_core.domain import Review
from attestor_core.domain.enums import Framework, Residency, ReviewState
from control_plane.guard import (
    TOKEN_HEADER,
    active_reviews,
    require_capacity,
    require_write_token,
)

TOKEN = "s3cret-demo-token"  # noqa: S105 - a fixture, not a credential


class _Request:
    """Just enough of `starlette.Request` for the guard: case-insensitive headers."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        # Starlette's Headers is case-insensitive; a plain dict is not, so the guard's
        # lookup has to be matched here or the test would pass for the wrong reason.
        self.headers = _CaseInsensitive(self.headers)


class _CaseInsensitive(dict[str, str]):
    def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
        return super().get(key.lower(), default)


class _Reviews:
    """A `ReviewRepository` stand-in that returns a fixed list."""

    def __init__(self, states: list[ReviewState]) -> None:
        self._reviews = [
            Review(
                review_id=f"rev-{index:04d}",
                customer=f"Customer {index}",
                framework=Framework.CAIQ,
                residency=Residency.US,
                current_round=1,
                state=state,
            )
            for index, state in enumerate(states)
        ]

    def list_all(self, limit: int = 50) -> list[Review]:
        return self._reviews[:limit]


# ---------------------------------------------------------------------------------


class TestTheWriteToken:
    def test_an_unconfigured_guard_refuses_rather_than_passing_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole reason this is fail-closed.

        A guard that disables itself when its environment variable is missing protects
        nothing in exactly the situation where protection was wanted: a deploy that forgot to
        set it. 503 rather than 401, because the fault is ours and not the caller's.
        """
        monkeypatch.delenv("ATTESTOR_WRITE_TOKEN", raising=False)
        with pytest.raises(HTTPException) as raised:
            require_write_token(_Request({TOKEN_HEADER: TOKEN}))  # type: ignore[arg-type]
        assert raised.value.status_code == 503
        assert "ATTESTOR_WRITE_TOKEN" in str(raised.value.detail)

    def test_a_missing_header_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", TOKEN)
        with pytest.raises(HTTPException) as raised:
            require_write_token(_Request())  # type: ignore[arg-type]
        assert raised.value.status_code == 401

    def test_a_wrong_token_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", TOKEN)
        with pytest.raises(HTTPException) as raised:
            require_write_token(_Request({TOKEN_HEADER: "not-the-token"}))  # type: ignore[arg-type]
        assert raised.value.status_code == 401

    def test_a_prefix_of_the_token_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `compare_digest` rather than `==` is what makes this uninteresting to time, but the
        # behaviour still has to be correct.
        monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", TOKEN)
        with pytest.raises(HTTPException):
            require_write_token(_Request({TOKEN_HEADER: TOKEN[:-1]}))  # type: ignore[arg-type]

    def test_the_right_token_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", TOKEN)
        require_write_token(_Request({TOKEN_HEADER: TOKEN}))  # type: ignore[arg-type]

    def test_surrounding_whitespace_in_the_environment_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `gcloud run deploy --set-env-vars` and shell heredocs both leave trailing newlines
        # in values often enough that this is worth pinning rather than discovering on deploy.
        monkeypatch.setenv("ATTESTOR_WRITE_TOKEN", f"  {TOKEN}\n")
        require_write_token(_Request({TOKEN_HEADER: TOKEN}))  # type: ignore[arg-type]


class TestTheConcurrentReviewCeiling:
    def test_a_delivered_review_does_not_count_against_capacity(self) -> None:
        reviews = _Reviews([ReviewState.DELIVERED] * 10)
        assert active_reviews(reviews) == []  # type: ignore[arg-type]
        require_capacity(reviews)  # type: ignore[arg-type]

    def test_awaiting_human_does_count(self) -> None:
        """The state a forgotten review sits in, so it is the state that must count.

        A round parked on a human holds its questions open indefinitely. Excluding it would
        make the ceiling trivially bypassable by starting three reviews and walking away.
        """
        reviews = _Reviews([ReviewState.AWAITING_HUMAN] * 3)
        with pytest.raises(HTTPException) as raised:
            require_capacity(reviews)  # type: ignore[arg-type]
        assert raised.value.status_code == 429

    def test_the_refusal_names_the_ceiling_and_the_offenders(self) -> None:
        reviews = _Reviews([ReviewState.DRAFTING, ReviewState.TRIAGING, ReviewState.INTAKE])
        with pytest.raises(HTTPException) as raised:
            require_capacity(reviews)  # type: ignore[arg-type]
        detail = str(raised.value.detail)
        assert "3" in detail
        assert "rev-0000" in detail

    def test_a_review_already_in_flight_is_not_blocked_by_its_own_existence(self) -> None:
        # Starting round 2 on a review that is itself one of the three in flight must work.
        reviews = _Reviews([ReviewState.DRAFTING, ReviewState.DELIVERED, ReviewState.DELIVERED])
        require_capacity(reviews, starting="rev-0000")  # type: ignore[arg-type]

    def test_two_in_flight_is_under_the_ceiling(self) -> None:
        reviews = _Reviews([ReviewState.DRAFTING, ReviewState.AWAITING_HUMAN])
        assert len(require_capacity(reviews)) == 2  # type: ignore[arg-type]

    def test_the_ceiling_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTESTOR_MAX_ACTIVE_REVIEWS", "1")
        reviews = _Reviews([ReviewState.DRAFTING])
        with pytest.raises(HTTPException):
            require_capacity(reviews)  # type: ignore[arg-type]
