"""Idempotency claims — the thing that makes at-least-once delivery survivable.

Pub/Sub guarantees a message is delivered *at least* once. Redelivery is normal
operation, not an error: an ack that arrives after the deadline, an instance culled
mid-handler, a network blip during the ack. Without a claim, the second delivery of
`draft_answer` re-drafts every answer in that department, doubles the spend, and writes a
second set of audit events describing work that happened once.

So the dispatcher **claims the dedup key before doing any work**, with a conditional
write. Firestore's `create()` is the conditional primitive: it fails with `AlreadyExists`
if the document is there. There is no read-then-write, so there is no window between the
check and the claim for a concurrent delivery to slip through.

## The part that is easy to get wrong

A naive claim — "if the key exists, ack and skip" — permanently loses work the first time
an instance dies mid-handler. The claim is written, the work never completes, the
instance vanishes, the redelivery sees a claim and acks. That message is now gone, the
round never advances, and nothing anywhere reports an error.

So a claim carries a **lease**. A claim in `IN_PROGRESS` whose lease has expired is
assumed to belong to a dead worker and may be taken over. Completed claims are never
retaken. That is what makes "kill the dispatcher mid-run and restart it" recoverable
rather than merely survivable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import firestore

from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

WORK_CLAIMS = "work_claims"

DEFAULT_TIMEOUT_SECONDS = 20.0

#: How long a claim is honoured before another worker may take it over. Must exceed the
#: longest handler by a comfortable margin: the slowest is `draft_answer`, measured at
#: ~11m49s for all three departments in Phase 3, so roughly 4 minutes for one partition.
#: 15 minutes leaves room for a slow model without leaving genuinely dead work parked for
#: an hour. Cloud Run's own request ceiling (60 min) is the hard upper bound.
LEASE_SECONDS = 900


class ClaimState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimOutcome(StrEnum):
    """What the caller should do about the claim it just attempted."""

    #: The claim is ours and no one has done this work. Proceed.
    CLAIMED = "claimed"
    #: A previous worker held it, died, and its lease expired. Proceed.
    RECLAIMED = "reclaimed"
    #: This work has already completed. Ack the message and do nothing.
    DUPLICATE = "duplicate"
    #: Another worker holds a live lease right now. Do not proceed, do not ack --
    #: let Pub/Sub redeliver after the other worker finishes or its lease lapses.
    HELD = "held"


@dataclass(frozen=True)
class Claim:
    """The result of attempting to claim a unit of work."""

    dedup_key: str
    outcome: ClaimOutcome
    #: How many times this key has been attempted, including the current attempt.
    attempts: int = 1
    #: Present when the claim was previously completed — used for the audit line that
    #: says which run originally did the work.
    completed_by_run: str | None = None

    @property
    def may_proceed(self) -> bool:
        return self.outcome in (ClaimOutcome.CLAIMED, ClaimOutcome.RECLAIMED)


class WorkClaimRepository:
    """Conditional claims on `dedup_key`, with leases."""

    def __init__(
        self,
        client: firestore.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self._db = client if client is not None else firestore.Client(project=project_id())
        self._timeout = timeout
        self._lease = timedelta(seconds=lease_seconds)

    def _doc(self, dedup_key: str) -> Any:
        return self._db.collection(WORK_CLAIMS).document(dedup_key)

    def claim(
        self,
        dedup_key: str,
        *,
        run_id: str,
        kind: str,
        review_id: str,
        worker: str,
    ) -> Claim:
        """Attempt to take the claim. This is the idempotency guard.

        Called **before** any side effect. The `create()` is what makes it a guard rather
        than a suggestion: two concurrent deliveries cannot both succeed, because the
        conditional write is resolved by Firestore rather than by our read ordering.
        """
        now = datetime.now(UTC)
        record = {
            "dedup_key": dedup_key,
            "state": ClaimState.IN_PROGRESS.value,
            "run_id": run_id,
            "kind": kind,
            "review_id": review_id,
            "worker": worker,
            "attempts": 1,
            "claimed_at": now.isoformat(),
            "lease_expires_at": (now + self._lease).isoformat(),
            "completed_at": None,
        }

        try:
            self._doc(dedup_key).create(record, timeout=self._timeout)
        except gexc.AlreadyExists:
            return self._contest(dedup_key, run_id=run_id, worker=worker, now=now)

        logger.info("claimed work", extra={"dedup_key": dedup_key, "kind": kind, "run_id": run_id})
        return Claim(dedup_key=dedup_key, outcome=ClaimOutcome.CLAIMED, attempts=1)

    def _contest(self, dedup_key: str, *, run_id: str, worker: str, now: datetime) -> Claim:
        """Someone got here first. Decide whether they still hold it."""
        snapshot = self._doc(dedup_key).get(timeout=self._timeout)
        existing: dict[str, Any] = snapshot.to_dict() or {}
        state = existing.get("state")
        attempts = int(existing.get("attempts", 1))

        if state == ClaimState.COMPLETED.value:
            logger.info(
                "duplicate delivery ignored",
                extra={"dedup_key": dedup_key, "completed_by_run": existing.get("run_id")},
            )
            return Claim(
                dedup_key=dedup_key,
                outcome=ClaimOutcome.DUPLICATE,
                attempts=attempts,
                completed_by_run=str(existing.get("run_id") or ""),
            )

        expires_raw = existing.get("lease_expires_at")
        expired = True
        if isinstance(expires_raw, str):
            try:
                expired = datetime.fromisoformat(expires_raw) <= now
            except ValueError:
                # An unparseable lease is treated as expired rather than as permanent.
                # A corrupt timestamp must not park a work unit forever.
                logger.warning("unparseable lease on %s: %r", dedup_key, expires_raw)

        if state == ClaimState.IN_PROGRESS.value and not expired:
            logger.info("claim held by a live worker", extra={"dedup_key": dedup_key})
            return Claim(dedup_key=dedup_key, outcome=ClaimOutcome.HELD, attempts=attempts)

        # Either the previous worker died and its lease lapsed, or the last attempt
        # failed. Take it over and count the attempt.
        self._doc(dedup_key).update(
            {
                "state": ClaimState.IN_PROGRESS.value,
                "run_id": run_id,
                "worker": worker,
                "attempts": attempts + 1,
                "claimed_at": now.isoformat(),
                "lease_expires_at": (now + self._lease).isoformat(),
            },
            timeout=self._timeout,
        )
        logger.warning(
            "reclaimed stale work",
            extra={"dedup_key": dedup_key, "attempts": attempts + 1, "previous_state": state},
        )
        return Claim(dedup_key=dedup_key, outcome=ClaimOutcome.RECLAIMED, attempts=attempts + 1)

    def extend(self, dedup_key: str) -> datetime:
        """Push the lease forward while the work is genuinely still running.

        A fixed lease has to be guessed against the longest handler, and the guess is
        only as good as the slowest day. Measured at 312 questions the longest drafting
        partition is 269s against a 900s lease -- comfortable -- but that comfort depends
        on triage spreading questions across three departments. Concentrate them in one
        and the partition is ~682s, and the margin falls from 3.3x to 1.3x.

        So a live worker pushes its own lease forward instead of relying on the estimate.
        The lease stays deliberately longer than the Pub/Sub ack deadline (900s vs 600s)
        as well: a redelivery arriving mid-handler then finds a live claim and is refused
        rather than starting a second copy of the same drafting work.

        Returns:
            The new expiry, for the log line.
        """
        expires_at = datetime.now(UTC) + self._lease
        self._doc(dedup_key).update(
            {"lease_expires_at": expires_at.isoformat()}, timeout=self._timeout
        )
        return expires_at

    def complete(self, dedup_key: str) -> None:
        """Mark the work done. A completed claim is never retaken."""
        self._doc(dedup_key).update(
            {
                "state": ClaimState.COMPLETED.value,
                "completed_at": datetime.now(UTC).isoformat(),
            },
            timeout=self._timeout,
        )

    def fail(self, dedup_key: str, reason: str) -> None:
        """Release the claim after a failed attempt so a retry can take it immediately.

        Deliberately not left `IN_PROGRESS` to lapse: waiting out a 15-minute lease to
        retry work that failed in two seconds would turn a transient blip into a stalled
        run.
        """
        self._doc(dedup_key).update(
            {
                "state": ClaimState.FAILED.value,
                "failed_at": datetime.now(UTC).isoformat(),
                "failure_reason": reason[:500],
            },
            timeout=self._timeout,
        )

    def get(self, dedup_key: str) -> dict[str, Any] | None:
        """Read a claim. For tests and for the dead-letter audit record."""
        snapshot = self._doc(dedup_key).get(timeout=self._timeout)
        return dict(snapshot.to_dict() or {}) if snapshot.exists else None
