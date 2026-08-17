"""SSE fan-out: the control plane streams events that other processes produced.

The work happens in dispatcher instances. The browser holds an SSE connection to a
*control plane* instance. Those are different containers, so the control plane learns
about events the only way it can — by watching Firestore, where the dispatcher writes
them.

## Two sources, both required

**Primary: a Firestore snapshot listener.** Push-based, low latency, no polling cost.

**Fallback: a self-scheduling, non-overlapping poll.** Realtime listeners can stop
delivering *without erroring* — a dropped stream the client library never surfaces, a
watch that silently expires. A fallback that only engages on an explicit error will never
engage on the failure that actually happens. So the poll is armed on a **staleness
timer**: if the listener has delivered nothing for `FALLBACK_AFTER_SECONDS`, the poller
takes over regardless of whether anything reported an error.

Non-overlapping matters as much as the interval. A fixed-rate poll against a slow
Firestore query stacks requests until the instance dies; each cycle is scheduled only
after the previous one finishes.

## Why the heartbeat exists

This stream stays open for the length of a review — twelve minutes for the measured
312-question run. Idle connections are dropped by proxies, load balancers, and Cloud
Run's own request timeout, so a `: heartbeat` comment goes out every 15 seconds. The
`: open` flush on connect is separate and just as necessary: buffering proxies hold a
response until the first bytes arrive, and without it the browser shows nothing until
the first real event, which can be a minute into triage.

## Gaps are the client's job, and it can do it

Every event carries a monotonic `seq`. If the client sees 41 then 43, it asks for the
range it missed rather than the stream silently having lied. That is why `seq` is
assigned here, in one place, rather than by whoever wrote the event.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Comment frames keep proxies and Cloud Run from culling an idle stream.
HEARTBEAT_SECONDS = 15.0

#: How long the listener may deliver nothing before the poller takes over. Deliberately
#: longer than a heartbeat and shorter than a stage: triage takes ~27s and drafting
#: several minutes, so a genuinely quiet stream is normal for tens of seconds.
FALLBACK_AFTER_SECONDS = 45.0

#: Poll interval once the fallback is engaged. Each cycle is scheduled after the previous
#: one completes, so a slow query stretches the interval instead of stacking requests.
POLL_INTERVAL_SECONDS = 5.0

#: Bound on the in-memory queue between the Firestore callback and the response. A client
#: that stops reading must not grow this without limit; dropping the oldest is right
#: because the client detects the gap by `seq` and can backfill.
QUEUE_LIMIT = 1000


def format_sse(event: dict[str, Any], seq: int) -> str:
    """Render one event as an SSE frame.

    `id:` carries the sequence so a reconnecting browser can send `Last-Event-ID` and be
    resumed rather than replayed from the beginning.

    ## Data frames are deliberately UNNAMED

    An earlier version emitted `event: {kind}`, which reads well and does not work. `EventSource`
    delivers a *named* frame only to `addEventListener('that-exact-name', ...)`; it never reaches
    `onmessage`. So naming frames by audit kind means every client must enumerate, in advance,
    every kind it will ever accept — and the first time a new kind is added, a client that has not
    been updated drops it silently. On an audit stream, a category of event that is emitted and
    never received is the worst available failure: the record looks complete and is not.

    The kind is in the payload, which is where an open-ended stream's discriminator belongs. One
    catch-all handler, and a kind nobody has seen before still arrives.

    Phase 6 found this by writing the client: with named frames, `onmessage` received nothing at
    all and the page sat on its server-rendered first paint looking perfectly healthy.

    The heartbeat is the one exception and stays named — see `heartbeat_frame`. It is not an entry
    in the log, and keeping it off the data path is precisely why it can be told apart.
    """
    data = json.dumps({**event, "seq": seq}, default=str)
    return f"id: {seq}\ndata: {data}\n\n"


def open_frame() -> str:
    """The immediate flush. Sent before anything else, on every connection."""
    return ": open\n\n"


def heartbeat_frame() -> str:
    """A heartbeat the browser can actually observe, plus the comment that flushes proxies.

    Both halves are needed and they do different jobs.

    The **comment** (`: heartbeat`) keeps the connection warm through a buffering proxy. It is
    bytes on the wire and nothing else — and that is the problem, because `EventSource` does
    not deliver comment lines to `onmessage`. A client watchdog fed only by comments never
    sees a beat.

    That matters more here than it sounds. The failure this whole stream is designed against is
    a listener that stops delivering while the socket stays open and `onerror` never fires. The
    only way a browser can detect that is to notice heartbeats stopping — so a heartbeat it
    cannot see is a heartbeat that cannot do its job. Phase 6 found this by writing the
    watchdog: with comments alone, an idle review trips the staleness timer every 40 seconds and
    pins itself to the polling fallback for as long as it is open, which looks like a broken
    stream and is in fact a working one nobody can hear.

    So a real `event: heartbeat` frame goes out alongside. It carries no `seq` of its own,
    deliberately: `seq` is the monotonic position in the run's event log and a heartbeat is not
    an entry in it. Giving heartbeats sequence numbers would make the client's gap detection
    count them as missed events.
    """
    now = datetime.now(UTC).isoformat()
    payload = json.dumps({"kind": "heartbeat", "emitted_at": now})
    return f": heartbeat {now}\n\nevent: heartbeat\ndata: {payload}\n\n"


class RunEventStream:
    """Merges a Firestore listener and a staleness-armed poller into one SSE stream."""

    def __init__(
        self,
        run_id: str,
        events: Any,
        *,
        since_seq: int = 0,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        fallback_after_seconds: float = FALLBACK_AFTER_SECONDS,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        use_listener: bool = True,
    ) -> None:
        self.run_id = run_id
        self._events = events
        self._seq = since_seq
        self._heartbeat = heartbeat_seconds
        self._fallback_after = fallback_after_seconds
        self._poll_interval = poll_interval_seconds
        #: `False` disables the realtime listener entirely. Not a debug flag: it is how
        #: the fallback is *tested* -- the exit criterion asks for the poller to engage
        #: when the listener is disabled, not only when it errors.
        self._use_listener = use_listener

        #: How often the loop wakes to re-check staleness when nothing is arriving.
        #: Derived from the fallback window rather than fixed: a hard 1s tick would mean
        #: the staleness check runs at most once a second, so any fallback window shorter
        #: than that would never be evaluated at the right time. Capped at 1s so an idle
        #: stream still wakes rarely.
        self._tick = max(0.01, min(1.0, self._fallback_after / 3))

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._seen: set[str] = set()
        self._last_delivery = 0.0
        self._fallback_engaged = False
        self._watch: Any | None = None

    # -- sources ------------------------------------------------------------------------

    def _offer(self, event: dict[str, Any]) -> None:
        """Accept an event from either source, dropping ones already delivered.

        Both sources can see the same event -- that is the point of having two -- so
        deduplication happens here rather than in either of them.
        """
        marker = str(event.get("event_id") or event.get("id") or "")
        if marker and marker in self._seen:
            return
        if marker:
            self._seen.add(marker)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # The client is not reading. Drop the oldest rather than blocking the
            # Firestore callback thread, which would stall every other listener.
            with suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                self._queue.get_nowait()
                self._queue.put_nowait(event)

    def _start_listener(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the Firestore snapshot listener, if one is available."""
        if not self._use_listener:
            logger.info("listener disabled; the stream runs on the poller alone")
            return
        try:
            self._watch = self._events.watch_run(
                self.run_id,
                lambda event: loop.call_soon_threadsafe(self._offer, event),
            )
        except Exception as exc:
            # Not fatal, and deliberately not the only way the poller starts: a listener
            # that fails loudly here is the EASY case.
            logger.warning("could not attach the Firestore listener: %s", exc)

    async def _poll_once(self) -> int:
        """One non-overlapping poll cycle. Returns how many new events it found."""
        try:
            fresh = await asyncio.to_thread(self._events.for_run_since, self.run_id, self._seq)
        except Exception as exc:
            logger.warning("poll cycle failed: %s", exc)
            return 0
        for event in fresh:
            self._offer(event)
        return len(fresh)

    # -- the stream ---------------------------------------------------------------------

    async def __aiter__(self) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        self._last_delivery = loop.time()

        # Flush immediately. A buffering proxy holds the response open until the first
        # bytes arrive, so this is what makes the connection observable at all.
        yield open_frame()

        # Backfill before the listener attaches, so a client reconnecting with
        # Last-Event-ID gets the gap rather than only what happens next.
        await self._poll_once()
        self._start_listener(loop)

        last_heartbeat = loop.time()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=self._tick)
                except TimeoutError:
                    event = None

                now = loop.time()
                if event is not None:
                    self._seq += 1
                    self._last_delivery = now
                    yield format_sse(event, self._seq)
                    continue

                # THE STALENESS CHECK. Nothing has arrived. A listener that stopped
                # delivering without erroring looks exactly like a quiet review, so the
                # poller is armed on elapsed silence rather than on an exception.
                if not self._fallback_engaged and (
                    now - self._last_delivery > self._fallback_after
                ):
                    self._fallback_engaged = True
                    logger.warning(
                        "no events for %.0fs; engaging the polling fallback",
                        now - self._last_delivery,
                        extra={"run_id": self.run_id},
                    )

                if self._fallback_engaged:
                    found = await self._poll_once()
                    if found:
                        self._last_delivery = now
                    else:
                        await asyncio.sleep(self._poll_interval)

                if now - last_heartbeat >= self._heartbeat:
                    last_heartbeat = now
                    yield heartbeat_frame()
        finally:
            self.close()

    def close(self) -> None:
        if self._watch is not None:
            try:
                self._watch.unsubscribe()
            except Exception as exc:  # pragma: no cover - teardown best effort
                logger.debug("listener unsubscribe failed: %s", exc)
            self._watch = None

    @property
    def fallback_engaged(self) -> bool:
        """Whether the poller took over. Surfaced so the proof can assert on it."""
        return self._fallback_engaged
