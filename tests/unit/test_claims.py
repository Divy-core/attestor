"""Idempotency claims under every delivery scenario that actually happens.

No network. Firestore is faked at the document level, because what is under test is the
claim protocol — conditional create, lease expiry, takeover, completion — not Google's
client library.

The scenario worth reading first is `test_a_dead_worker_does_not_park_the_work_forever`.
A naive guard ("key exists → ack and skip") loses that message permanently the first time
an instance is culled mid-handler, and reports nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from google.api_core import exceptions as gexc

from attestor_platform.firestore.claims import (
    ClaimOutcome,
    ClaimState,
    WorkClaimRepository,
)


class _Snapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def create(self, record: dict[str, Any], timeout: float = 0.0) -> None:
        if self._key in self._store:
            raise gexc.AlreadyExists(self._key)
        self._store[self._key] = dict(record)

    def get(self, timeout: float = 0.0) -> _Snapshot:
        return _Snapshot(self._store.get(self._key))

    def update(self, patch: dict[str, Any], timeout: float = 0.0) -> None:
        self._store.setdefault(self._key, {}).update(patch)


class _Collection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _Doc:
        return _Doc(self._store, key)


class _Db:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def collection(self, _name: str) -> _Collection:
        return _Collection(self.store)


@pytest.fixture
def db() -> _Db:
    return _Db()


def _repo(db: _Db, lease_seconds: int = 900) -> WorkClaimRepository:
    return WorkClaimRepository(client=db, lease_seconds=lease_seconds)  # type: ignore[arg-type]


def _claim(repo: WorkClaimRepository, key: str = "k1", run: str = "run-1") -> Any:
    return repo.claim(key, run_id=run, kind="draft_answer", review_id="rev1", worker="w1")


class TestFirstDelivery:
    def test_a_fresh_key_is_claimed(self, db: _Db) -> None:
        assert _claim(_repo(db)).outcome is ClaimOutcome.CLAIMED

    def test_claiming_writes_a_lease(self, db: _Db) -> None:
        _claim(_repo(db))
        record = db.store["k1"]
        assert record["state"] == ClaimState.IN_PROGRESS.value
        assert record["lease_expires_at"] > record["claimed_at"]

    def test_may_proceed_is_true(self, db: _Db) -> None:
        assert _claim(_repo(db)).may_proceed is True


class TestDuplicateDelivery:
    def test_a_completed_key_is_a_duplicate(self, db: _Db) -> None:
        repo = _repo(db)
        _claim(repo)
        repo.complete("k1")

        second = _claim(repo, run="run-2")

        assert second.outcome is ClaimOutcome.DUPLICATE
        assert second.may_proceed is False

    def test_the_duplicate_names_the_run_that_did_the_work(self, db: _Db) -> None:
        """So the audit line reads "already done by run-1" rather than just "skipped"."""
        repo = _repo(db)
        _claim(repo, run="run-1")
        repo.complete("k1")

        assert _claim(repo, run="run-2").completed_by_run == "run-1"

    def test_a_live_lease_is_held_not_duplicated(self, db: _Db) -> None:
        """A concurrent delivery while the first worker is still running must NOT be
        acked as a duplicate -- the work has not been done yet. It must be redelivered."""
        repo = _repo(db)
        _claim(repo, run="run-1")

        second = _claim(repo, run="run-2")

        assert second.outcome is ClaimOutcome.HELD
        assert second.may_proceed is False


class TestCrashRecovery:
    def test_a_dead_worker_does_not_park_the_work_forever(self, db: _Db) -> None:
        """The scenario a naive guard loses permanently.

        Instance claims, starts drafting, is culled. The claim is IN_PROGRESS and no one
        will ever complete it. A guard that only checks existence acks the redelivery and
        the round never advances -- with no exception anywhere.
        """
        repo = _repo(db, lease_seconds=0)  # lease expires immediately
        _claim(repo, run="run-dead")

        recovered = _claim(repo, run="run-fresh")

        assert recovered.outcome is ClaimOutcome.RECLAIMED
        assert recovered.may_proceed is True
        assert recovered.attempts == 2

    def test_a_failed_claim_is_retaken_immediately(self, db: _Db) -> None:
        """A handler that failed in two seconds must not wait out a 15-minute lease."""
        repo = _repo(db)
        _claim(repo)
        repo.fail("k1", "transient model error")

        assert _claim(repo, run="run-2").outcome is ClaimOutcome.RECLAIMED

    def test_the_failure_reason_is_kept(self, db: _Db) -> None:
        repo = _repo(db)
        _claim(repo)
        repo.fail("k1", "429 RESOURCE_EXHAUSTED")

        assert "429" in db.store["k1"]["failure_reason"]

    def test_attempts_accumulate_across_takeovers(self, db: _Db) -> None:
        """The dead-letter decision is made on this count."""
        repo = _repo(db, lease_seconds=0)
        for run in ("run-1", "run-2", "run-3"):
            claim = _claim(repo, run=run)
        assert claim.attempts == 3

    def test_a_corrupt_lease_is_treated_as_expired(self, db: _Db) -> None:
        """A malformed timestamp must not park a work unit permanently. Expired is the
        safe reading: worst case the work runs twice, and the handlers are idempotent
        against their own state machine."""
        repo = _repo(db)
        _claim(repo)
        db.store["k1"]["lease_expires_at"] = "not-a-timestamp"

        assert _claim(repo, run="run-2").outcome is ClaimOutcome.RECLAIMED

    def test_a_completed_claim_is_never_retaken_however_old(self, db: _Db) -> None:
        """Completion outranks lease expiry. Otherwise a slow ack after a long handler
        would re-run finished work."""
        repo = _repo(db, lease_seconds=0)
        _claim(repo)
        repo.complete("k1")
        db.store["k1"]["lease_expires_at"] = (datetime.now(UTC) - timedelta(days=30)).isoformat()

        assert _claim(repo, run="run-2").outcome is ClaimOutcome.DUPLICATE


class TestClaimInspection:
    def test_get_returns_none_for_an_unknown_key(self, db: _Db) -> None:
        assert _repo(db).get("never-claimed") is None

    def test_get_returns_the_record(self, db: _Db) -> None:
        repo = _repo(db)
        _claim(repo)
        record = repo.get("k1")
        assert record is not None
        assert record["kind"] == "draft_answer"


class TestLeaseExtension:
    """A live worker pushes its own lease forward rather than trusting the estimate.

    Measured at 312 questions the longest partition is 269s against a 900s lease -- but
    that depends on triage spreading questions across three departments. Concentrated in
    one, a partition is ~682s and the margin falls from 3.3x to 1.3x.
    """

    def test_extending_moves_the_expiry_forward(self, db: _Db) -> None:
        repo = _repo(db, lease_seconds=900)
        _claim(repo)
        before = db.store["k1"]["lease_expires_at"]

        repo.extend("k1")

        assert db.store["k1"]["lease_expires_at"] >= before

    def test_an_extended_claim_is_not_reclaimable(self, db: _Db) -> None:
        """The point of the whole mechanism: a long-running handler keeps its work."""
        repo = _repo(db, lease_seconds=0)  # would expire instantly without the extension
        _claim(repo)

        # A worker that is alive and heartbeating with a real lease.
        live = _repo(db, lease_seconds=900)
        live.extend("k1")

        assert _claim(repo, run="run-2").outcome is ClaimOutcome.HELD

    def test_extending_does_not_resurrect_a_completed_claim(self, db: _Db) -> None:
        """Completion outranks the lease, so a late heartbeat cannot reopen finished work."""
        repo = _repo(db)
        _claim(repo)
        repo.complete("k1")
        repo.extend("k1")

        assert _claim(repo, run="run-2").outcome is ClaimOutcome.DUPLICATE
