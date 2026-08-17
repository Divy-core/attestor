"""The shared transient-failure classifier and backoff helper.

No network, no sleeping of consequence. What is pinned here is the whitelist's *shape* --
that it retries what it should, refuses what it should not, and never converts a failure
into a value.

The last of those is the one that matters. Four places in this codebase had grown their own
classifier and three of them were subtly different; consolidating them means one bug here
is a bug in every client, so the boundary conditions get tests rather than trust.
"""

from __future__ import annotations

import pytest

from attestor_platform.retry import TRANSIENT_MARKERS, is_transient, retrying


class _Dropped(Exception):
    """Named to carry the whole signal in the type, as httpx's own error does."""


class TestClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "429 RESOURCE_EXHAUSTED Quota exceeded for quota metric 'Query Reasoning Engine'",
            "503 Service Unavailable",
            "504 Deadline Exceeded",
            "500 Internal error encountered",
            "502 Bad Gateway",
            "peer closed connection without sending complete message body",
            "incomplete chunked read",
            "ConnectionReset by peer",
            "Timeout waiting for response",
        ],
    )
    def test_transient_failures_are_retried(self, message: str) -> None:
        assert is_transient(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "403 PERMISSION_DENIED discoveryengine.servingConfigs.search",
            "404 reasoningEngine not found",
            "400 INVALID_ARGUMENT malformed request",
            "the corpus contains no matching document",
        ],
    )
    def test_permanent_failures_are_not(self, message: str) -> None:
        assert is_transient(RuntimeError(message)) is False

    def test_the_type_name_is_matched_as_well_as_the_message(self) -> None:
        """`RemoteProtocolError` renders a message naming neither a status nor a transport.

        Matching only `str(exc)` was how the dropped-stream family went unhandled on the
        engine path for a full session.
        """
        assert is_transient(_Dropped("nope")) is False
        assert is_transient(type("RemoteProtocolError", (Exception,), {})("nope")) is True

    def test_matching_is_case_insensitive(self) -> None:
        """The SDKs are not consistent: `RESOURCE_EXHAUSTED` and `resource_exhausted` both
        occur, and a case-sensitive list catches whichever one it was written against."""
        assert is_transient(RuntimeError("Resource_Exhausted")) is True
        assert all(marker == marker.lower() for marker in TRANSIENT_MARKERS)


class TestRetrying:
    def test_a_transient_failure_is_waited_out(self) -> None:
        attempts: list[int] = []

        def call() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return "ok"

        assert retrying(call, attempts=4, backoff_seconds=0.0) == "ok"
        assert len(attempts) == 3

    def test_a_permanent_failure_raises_on_the_first_attempt(self) -> None:
        attempts: list[int] = []

        def call() -> str:
            attempts.append(1)
            raise RuntimeError("403 PERMISSION_DENIED")

        with pytest.raises(RuntimeError, match="403"):
            retrying(call, attempts=4, backoff_seconds=0.0)
        assert len(attempts) == 1

    def test_the_last_failure_is_re_raised_unchanged(self) -> None:
        """Callers wrap this in their own typed error and need the original to wrap.

        A helper that swallowed it, or returned a default, would be the
        failure-impersonating-empty bug installed in a shared location where every client
        inherits it.
        """
        original = RuntimeError("503 Service Unavailable")

        def call() -> str:
            raise original

        with pytest.raises(RuntimeError) as caught:
            retrying(call, attempts=2, backoff_seconds=0.0)
        assert caught.value is original

    def test_one_attempt_disables_retrying(self) -> None:
        attempts: list[int] = []

        def call() -> str:
            attempts.append(1)
            raise RuntimeError("503 Service Unavailable")

        with pytest.raises(RuntimeError):
            retrying(call, attempts=1, backoff_seconds=0.0)
        assert len(attempts) == 1

    def test_a_first_attempt_that_succeeds_costs_nothing(self) -> None:
        assert retrying(lambda: 7, attempts=4, backoff_seconds=99.0) == 7
