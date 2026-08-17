"""One answer to "is this failure worth another attempt?", for every client that asks.

Four places in this codebase had independently grown a transient-failure classifier by the
end of Phase 5: the Model Armor client (on HTTP status codes), the Discovery Engine search
client, the embedding scorer, and the deployed-engine drafting path. They agreed on 429 and
503 and disagreed on everything else, which is the failure mode of a duplicated list — the
one that learns something new does not teach the others.

The dropped-stream family is the case that proved it. `stream_query` holds a chunked HTTP
response open for the whole of a drafting call, and a long-lived stream is a thing that gets
cut:

    RemoteProtocolError: peer closed connection without sending complete message body
    (incomplete chunked read)

That was found on the engine path and fixed there. Memory Bank writes go over the same
transport to the same service and had none of it, so `close_round` exhausted five delivery
attempts writing 60 commitments and left the review at `assembling` — the correct failure
(it raised rather than reporting success) and still a gap.

## Why substrings and not exception types

The Google SDKs wrap httpx, grpc and google-api-core errors at several layers, and the type
that surfaces is not stable across versions: an `isinstance` check written against one
release silently stops matching after a dependency bump, and the symptom is a retry that no
longer happens rather than an error. Matching the rendered message is coarser and
considerably harder to break.

## Why a whitelist and not a blacklist

Anything unrecognised fails on its first attempt. A 403 or a malformed request is not going
to succeed on the fourth try, and backing off four times to arrive at the same denial burns
four ack deadlines — which, on a Pub/Sub-driven partition, is the difference between a fast
dead-letter someone can read and a slow one nobody notices.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Substrings that mark a failure as worth trying again. Three families.
#:
#: **Rate limits.** Expected load, not an outage: eight drafting workers embedding
#: concurrently on a 312-question run will see 429s, and Agent Runtime enforces a
#: per-minute, per-region cap on engine queries that a full-scale run runs straight into.
#:
#: **Dropped streams.** The one that was nearly missed. See the module docstring.
#:
#: **Server-side unavailability and timeouts.** 500 is included deliberately: Vertex
#: returns it for transient backend faults, and the request that gets one is usually fine
#: on the next attempt.
TRANSIENT_MARKERS: tuple[str, ...] = (
    # rate limits
    "429",
    "resource_exhausted",
    # dropped streams and severed connections
    "remoteprotocolerror",
    "incomplete chunked read",
    "peer closed connection",
    "serverdisconnected",
    "connectionreset",
    "connectionerror",
    "broken pipe",
    # server-side unavailability
    "503",
    "unavailable",
    "500",
    "internal",
    "502",
    "bad gateway",
    # timeouts
    "504",
    "deadline_exceeded",
    "timeout",
)


def is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` is the kind of failure that a second attempt might survive.

    The exception's *type name* is matched as well as its message, because some wrappers
    carry the whole signal in the class (`RemoteProtocolError` renders a message that names
    neither a status code nor a transport).
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


def retrying[T](
    call: Callable[[], T],
    *,
    attempts: int,
    backoff_seconds: float,
    jitter_seconds: float = 0.0,
    description: str = "call",
) -> T:
    """Run ``call``, retrying transient failures with exponential backoff.

    Args:
        call: The thing to attempt. Called up to ``attempts`` times.
        attempts: Total attempts, including the first. `1` disables retrying.
        backoff_seconds: Base delay; attempt *n* waits ``backoff * 2**n``.
        jitter_seconds: Upper bound on a uniform random addition to each delay. Worth
            setting whenever several threads can fail together: workers that back off in
            lockstep re-collide on the same second and reproduce the burst that rate-limited
            them.
        description: Named in the log line, so a retry storm says which client is in it.

    Returns:
        Whatever ``call`` returned.

    Raises:
        BaseException: The last failure, re-raised unchanged. This helper never converts a
            failure into a value — every caller wraps the raised error in its own typed
            error, and a helper that returned a default here would be the
            failure-impersonating-empty bug in a shared location.
    """
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if not is_transient(exc) or attempt == attempts - 1:
                raise
            # S311: jitter spreads a retry burst, it does not protect anything. A CSPRNG
            # here would be cost with no benefit.
            delay = backoff_seconds * (2**attempt) + random.uniform(0, jitter_seconds)  # noqa: S311
            logger.warning(
                "%s: transient failure (attempt %d/%d), retrying in %.1fs: %s: %s",
                description,
                attempt + 1,
                attempts,
                delay,
                type(exc).__name__,
                exc,
            )
            time.sleep(delay)

    # Unreachable: the loop either returns or raises. Present so the function has a
    # provably non-None return type under `mypy --strict`.
    raise AssertionError(f"{description}: retry loop exited without returning or raising")
