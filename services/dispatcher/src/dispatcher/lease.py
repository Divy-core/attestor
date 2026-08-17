"""Keeping a claim alive while its handler is genuinely still working.

A claim's lease exists so that work abandoned by a dead instance can be taken over. That
requires guessing how long a handler might legitimately take, and a guess is only as good
as the slowest day.

Measured at 312 questions: the longest drafting partition is **269s** against a **900s**
lease and a **600s** Pub/Sub ack deadline. Comfortable — but only because triage spread
the questions across three departments. Concentrate them in one and the partition is
~682s, and the lease margin falls from 3.3x to 1.3x.

So rather than trusting the estimate, a running handler pushes its own lease forward on a
timer. The estimate then only has to cover *one heartbeat interval*, not one whole
partition.

## Why the lease still exceeds the ack deadline

Pub/Sub redelivers after 600s whether or not the handler has finished. Because the lease
runs to 900s and is being extended, that redelivery finds a **live claim** and is refused
with 409 rather than starting a second copy of the same drafting work. The heartbeat is
the belt; the 900s-over-600s ordering is the braces, and it is what protects the run if
the heartbeat thread itself is starved by eight concurrent drafting workers.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)

#: How often a running handler pushes its lease forward. Comfortably shorter than the
#: lease so several consecutive heartbeats can fail before the claim is at risk.
HEARTBEAT_SECONDS = 60.0


class LeaseKeeper:
    """Context manager that extends a claim's lease until the handler returns.

    The thread is a daemon and every failure is swallowed with a log line: an instance
    must never be kept alive, or a handler failed, because lease bookkeeping had a blip.
    Losing a heartbeat is survivable -- the lease still has minutes on it.
    """

    def __init__(
        self,
        claims: Any,
        dedup_key: str,
        *,
        interval: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._claims = claims
        self._dedup_key = dedup_key
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Counted so a long run can report how much lease extension it actually needed.
        self.heartbeats = 0

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                expires_at = self._claims.extend(self._dedup_key)
            except Exception as exc:
                # Deliberately not fatal. The lease has minutes left; a failed heartbeat
                # is worth a line, not an aborted handler.
                logger.warning(
                    "lease heartbeat failed: %s", exc, extra={"dedup_key": self._dedup_key}
                )
                continue
            self.heartbeats += 1
            logger.debug(
                "lease extended",
                extra={"dedup_key": self._dedup_key, "expires_at": expires_at.isoformat()},
            )

    def __enter__(self) -> LeaseKeeper:
        self._thread = threading.Thread(
            target=self._run, name=f"lease-{self._dedup_key[:8]}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            # Bounded join: the thread is a daemon and is only ever sleeping on the
            # stop event, so this returns immediately in practice.
            self._thread.join(timeout=5.0)
        if self.heartbeats:
            logger.info(
                "handler outlived %d lease heartbeat(s)",
                self.heartbeats,
                extra={"dedup_key": self._dedup_key},
            )
