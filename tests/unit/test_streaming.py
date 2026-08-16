"""SSE fan-out: the open flush, the heartbeat, and the fallback that must self-arm.

The exit criterion is specific, and it is the reason this file exists: the polling
fallback has to engage when the listener is *disabled*, not only when it errors. A
realtime listener that stops delivering while reporting nothing is the failure that
actually happens — a dropped watch, an expired stream, a callback thread that died — and
a fallback wired to an exception handler will sit there for the whole review.

No network and no `pytest-asyncio`: `@async_test` runs each coroutine with
`asyncio.run`, and the timers are shrunk so a 45-second staleness window is exercised in
milliseconds.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

from control_plane.streaming import RunEventStream, format_sse, heartbeat_frame, open_frame


def async_test(fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    """Run an async test body. Cheaper than a dependency for the handful of tests here."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(fn(*args, **kwargs))

    return wrapper


class _Events:
    """Stands in for `AuditEventRepository`."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, listener: bool = True) -> None:
        self.rows = list(rows or [])
        self.polls = 0
        self.watch_calls = 0
        self._listener_supported = listener
        self._callback: Any = None

    def for_run_since(self, run_id: str, since_seq: int, limit: int = 500) -> list[dict[str, Any]]:
        del run_id, limit
        self.polls += 1
        return self.rows[since_seq:]

    def watch_run(self, run_id: str, on_event: Any) -> Any:
        del run_id
        self.watch_calls += 1
        if not self._listener_supported:
            raise RuntimeError("listener unavailable")
        self._callback = on_event
        return type("Watch", (), {"unsubscribe": lambda self: None})()

    def deliver(self, event: dict[str, Any]) -> None:
        """Simulate the Firestore callback firing."""
        if self._callback is not None:
            self._callback(event)


def _event(n: int) -> dict[str, Any]:
    return {"event_id": f"e{n}", "kind": "answer_drafted", "question_id": f"q{n}"}


async def _collect(stream: RunEventStream, frames: int, timeout: float = 5.0) -> list[str]:
    """Pull a fixed number of frames, then stop."""
    out: list[str] = []

    async def _pump() -> None:
        async for frame in stream.__aiter__():
            out.append(frame)
            if len(out) >= frames:
                return

    await asyncio.wait_for(_pump(), timeout=timeout)
    return out


async def _drain_for(stream: RunEventStream, seconds: float) -> None:
    """Run the stream for a while and stop, ignoring the frames."""

    async def _pump() -> None:
        async for _ in stream.__aiter__():
            pass

    task = asyncio.create_task(_pump())
    await asyncio.sleep(seconds)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


class TestFraming:
    def test_the_first_frame_is_an_immediate_open(self) -> None:
        """Buffering proxies hold a response until the first bytes arrive. Without this
        the browser shows nothing until the first real event, a minute into triage."""
        assert open_frame() == ": open\n\n"

    def test_an_event_frame_carries_its_sequence_as_the_id(self) -> None:
        """`id:` is what lets a reconnecting browser send Last-Event-ID and be resumed
        rather than replayed from the beginning."""
        frame = format_sse({"kind": "answer_drafted"}, 41)
        assert frame.startswith("id: 41\n")
        assert "event: answer_drafted\n" in frame
        assert '"seq": 41' in frame

    def test_a_heartbeat_is_a_comment_not_an_event(self) -> None:
        """A client parsing frames must not mistake heartbeats for data."""
        assert heartbeat_frame().startswith(": heartbeat")


class TestBackfill:
    @async_test
    async def test_events_already_written_are_delivered_first(self) -> None:
        """A client connecting mid-run gets what it missed, not only what happens next."""
        stream = RunEventStream("run-1", _Events([_event(1), _event(2)]))

        frames = await _collect(stream, frames=3)

        assert frames[0] == ": open\n\n"
        assert '"question_id": "q1"' in frames[1]
        assert '"question_id": "q2"' in frames[2]

    @async_test
    async def test_reconnecting_with_a_sequence_skips_what_was_seen(self) -> None:
        events = _Events([_event(1), _event(2), _event(3)])
        stream = RunEventStream("run-1", events, since_seq=2)

        frames = await _collect(stream, frames=2)

        assert '"question_id": "q3"' in frames[1]

    @async_test
    async def test_sequence_numbers_are_monotonic_so_gaps_are_detectable(self) -> None:
        stream = RunEventStream("run-1", _Events([_event(1), _event(2), _event(3)]))

        frames = await _collect(stream, frames=4)

        assert [f.split("\n")[0] for f in frames[1:]] == ["id: 1", "id: 2", "id: 3"]


class TestTheFallbackSelfArms:
    """The exit criterion, and the reason the fallback is on a staleness timer."""

    @staticmethod
    def _stream(events: _Events, **kw: Any) -> RunEventStream:
        return RunEventStream(
            "run-1",
            events,
            fallback_after_seconds=kw.pop("fallback_after_seconds", 0.05),
            poll_interval_seconds=0.01,
            heartbeat_seconds=100.0,
            **kw,
        )

    @async_test
    async def test_it_engages_when_the_listener_is_disabled(self) -> None:
        """Disabled, not errored. Nothing raises anywhere in this test."""
        events = _Events()
        stream = self._stream(events, use_listener=False)

        await _drain_for(stream, 0.4)

        assert stream.fallback_engaged is True, "the poller never took over"
        assert events.watch_calls == 0, "the listener should not have been attached"
        assert events.polls > 1, "engaged but never polled"

    @async_test
    async def test_a_silent_listener_still_arms_the_poller(self) -> None:
        """The failure that actually happens: `watch_run` succeeds and then delivers
        nothing, ever. No exception, so an error-triggered fallback never fires."""
        events = _Events()
        stream = self._stream(events)

        await _drain_for(stream, 0.4)

        assert events.watch_calls == 1, "the listener was attached"
        assert stream.fallback_engaged is True, "silence alone must arm the poller"

    @async_test
    async def test_a_listener_that_raises_also_arms_the_poller(self) -> None:
        """The easy case, kept because it is still a case."""
        events = _Events(listener=False)
        stream = self._stream(events)

        await _drain_for(stream, 0.4)

        assert stream.fallback_engaged is True

    @async_test
    async def test_a_delivering_listener_does_not_arm_the_poller(self) -> None:
        """The fallback must not become the normal path -- that would poll Firestore for
        every open stream for the length of every review."""
        events = _Events()
        stream = self._stream(events, fallback_after_seconds=0.5)

        async def _pump() -> None:
            async for _ in stream.__aiter__():
                pass

        task = asyncio.create_task(_pump())
        for n in range(1, 6):
            await asyncio.sleep(0.04)
            events.deliver(_event(n))
        await asyncio.sleep(0.04)
        task.cancel()

        assert stream.fallback_engaged is False


class TestDeduplication:
    @async_test
    async def test_an_event_seen_by_both_sources_is_delivered_once(self) -> None:
        """Both sources seeing the same event is the point of having two, so the merge
        dedupes rather than double-reporting."""
        events = _Events([_event(1)])
        stream = RunEventStream(
            "run-1", events, fallback_after_seconds=0.05, poll_interval_seconds=0.01
        )

        async def _pump() -> None:
            async for _ in stream.__aiter__():
                pass

        task = asyncio.create_task(_pump())
        await asyncio.sleep(0.1)
        events.deliver(_event(1))
        await asyncio.sleep(0.2)
        task.cancel()

        # Sequence numbers are assigned on delivery, so a duplicate shows up as a second
        # increment.
        assert stream._seq == 1
