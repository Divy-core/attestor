"""The lease heartbeat: it runs, it survives failure, and it stops.

The failure mode being guarded is subtle. A drafting partition that outlives its lease
gets taken over by a redelivery, and then the SAME questions are drafted twice — double
model spend and two sets of writes, with nothing reporting an error because both workers
believe they hold the claim.
"""

from __future__ import annotations

import time
from typing import Any

from dispatcher.lease import LeaseKeeper


class _Claims:
    def __init__(self, fail: bool = False) -> None:
        self.extends = 0
        self.fail = fail

    def extend(self, dedup_key: str) -> Any:
        del dedup_key
        self.extends += 1
        if self.fail:
            raise RuntimeError("503 Firestore unavailable")
        from datetime import UTC, datetime

        return datetime.now(UTC)


class TestHeartbeat:
    def test_a_long_handler_gets_its_lease_extended(self) -> None:
        claims = _Claims()
        with LeaseKeeper(claims, "k1", interval=0.02):
            time.sleep(0.12)

        assert claims.extends >= 3, "the heartbeat never fired"

    def test_a_short_handler_costs_no_extension(self) -> None:
        """Almost every message finishes in seconds. They must not pay a Firestore write
        for a lease that was never at risk."""
        claims = _Claims()
        with LeaseKeeper(claims, "k1", interval=5.0):
            pass

        assert claims.extends == 0

    def test_the_heartbeat_stops_when_the_handler_returns(self) -> None:
        claims = _Claims()
        with LeaseKeeper(claims, "k1", interval=0.02):
            time.sleep(0.08)
        settled = claims.extends

        time.sleep(0.1)

        assert claims.extends == settled, "the thread outlived its handler"

    def test_a_failing_heartbeat_does_not_break_the_handler(self) -> None:
        """The lease still has minutes on it. A blip in lease bookkeeping must never be
        the reason a twelve-minute review aborts."""
        claims = _Claims(fail=True)

        with LeaseKeeper(claims, "k1", interval=0.02):
            time.sleep(0.08)

        assert claims.extends >= 2, "it gave up after the first failure"

    def test_the_count_is_reported(self) -> None:
        """So a run can say how much lease extension it actually needed."""
        claims = _Claims()
        with LeaseKeeper(claims, "k1", interval=0.02) as keeper:
            time.sleep(0.08)

        assert keeper.heartbeats >= 2
