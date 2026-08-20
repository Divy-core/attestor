"""The front door's lock. A demo guard, described as one.

Phase 6.5 gave the interface an entrance: the browser can now mint a signed URL, create a
review, and start 312 questions of work on the deployed engines. Until this module, that
entrance was open to anyone who found the `.run.app` URL, because the control plane runs
`--allow-unauthenticated` by a Phase 1 scope decision (multi-tenant auth is out of scope for
this build).

**What this is not.** It is not authentication. There are no users, no sessions, no per-
tenant isolation, and a single shared secret cannot provide any of those. Calling it auth in
`PROGRESS.md` would be the kind of overclaim this repo spends its time avoiding.

**What it is.** Three cheap properties that together make the credit-burn surface bounded:

1. A shared token, held server-side in the Next.js route handler and never shipped to the
   browser, required on every write. Someone who finds the control plane URL cannot start
   work with it; someone who finds the *web* URL can, which is the intended demo behaviour.
2. A ceiling on concurrently active reviews. The expensive thing is drafting, and drafting
   is what a review in flight is doing.
3. A ceiling on questions per round, enforced at intake in the dispatcher because nothing
   before the parse knows how many questions a file contains.

## Why unset means refuse

`require_write_token` refuses when `ATTESTOR_WRITE_TOKEN` is absent rather than passing
through. A guard that disables itself when its configuration is missing protects nothing in
exactly the situation where protection was wanted — a deploy that forgot the variable. The
refusal is a 503 naming the variable, so the failure is diagnosable in one read of the log
rather than looking like a client bug.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Final

from fastapi import HTTPException, Request, status

from attestor_core.domain.enums import ReviewState
from attestor_platform.config import max_active_reviews
from attestor_platform.firestore import ReviewRepository

logger = logging.getLogger(__name__)

#: The header the web service sends. Not `Authorization`, deliberately: this is not a
#: credential identifying anyone, and dressing it as a bearer token would invite it to be
#: read as one.
TOKEN_HEADER: Final = "X-Attestor-Token"  # noqa: S105 - a header name, not a secret

#: States that are not consuming fleet capacity. Everything else counts as in flight —
#: including `awaiting_human`, which holds a round open indefinitely and is precisely the
#: state a forgotten review sits in.
_SETTLED: Final = frozenset({ReviewState.DELIVERED, ReviewState.FAILED})


def write_token() -> str:
    return os.environ.get("ATTESTOR_WRITE_TOKEN", "").strip()


def require_write_token(request: Request) -> None:
    """Gate a write. Raises rather than returning a verdict, so it cannot be ignored.

    Raises:
        HTTPException: 503 when the guard is not configured, 401 when the token is absent
            or wrong.
    """
    expected = write_token()
    if not expected:
        logger.error("write refused: ATTESTOR_WRITE_TOKEN is not set on this service")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This deployment has no write token configured, so writes are refused. "
            "Set ATTESTOR_WRITE_TOKEN on the control plane service.",
        )
    presented = request.headers.get(TOKEN_HEADER, "")
    # Constant-time, because a length-or-prefix-sensitive comparison on a shared secret is
    # a byte-at-a-time oracle, and the cost of doing it correctly is one function call.
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"This endpoint requires a valid {TOKEN_HEADER} header.",
        )


def active_reviews(reviews: ReviewRepository, limit: int = 200) -> list[str]:
    """Which reviews are currently consuming fleet capacity.

    Archived reviews do not count, and that is not a cosmetic exemption. An archived
    review is one an operator has declared finished with; a dead run stuck in `drafting`
    holds its state forever, and eight of them would have consumed the ceiling three
    times over and refused every new review with a 429 that named the wrong problem.
    """
    return [
        r.review_id
        for r in reviews.list_all(limit=limit)
        if r.state not in _SETTLED and not r.archived
    ]


def require_capacity(reviews: ReviewRepository, *, starting: str | None = None) -> list[str]:
    """Refuse to start work when too many reviews are already in flight.

    Args:
        reviews: The repository to count from.
        starting: The review about to be started, excluded from the count so that
            re-starting a round on a review already in flight is not blocked by its own
            existence.

    Returns:
        The review ids counted as active, for the audit detail.

    Raises:
        HTTPException: 429 with the ceiling and the offending ids named. A 429 rather than a
            403: the request is well-formed and would be honoured later, which is exactly
            what "too many requests" means.
    """
    ceiling = max_active_reviews()
    in_flight = [r for r in active_reviews(reviews) if r != starting]
    if len(in_flight) >= ceiling:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"{len(in_flight)} reviews are already in flight and this deployment allows "
            f"{ceiling}. Wait for one to be delivered, or approve what is held for a "
            f"human: {', '.join(sorted(in_flight)[:5])}.",
        )
    return in_flight
