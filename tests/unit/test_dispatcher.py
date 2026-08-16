"""The dispatcher: ack decisions, the idempotency guard, and the drafting join.

No network, no Pub/Sub, no Firestore. Every dependency is a fake, because what is under
test is the *decision logic* — which failures are permanent, which are transient, when
work is skipped, when the join closes.

The test that matters most is
`TestTheGuardIsActuallyCalled::test_a_duplicate_delivery_does_not_run_the_handler`.
Phase 3 shipped a recursive-split backstop that was correct, tested, and never called;
asserting that the claim repository returns `DUPLICATE` would reproduce exactly that
mistake. So the assertion is on the **handler's** call count.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi import Response

from attestor_core.errors import ContractViolation, IllegalTransition
from attestor_core.protocol import WorkEnvelope, WorkKind
from attestor_platform.firestore.claims import WorkClaimRepository
from dispatcher import main as dispatcher_main
from dispatcher.handlers import HandlerResult
from dispatcher.push import parse_push

# ---------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------


class _Snapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store, self._key = store, key

    def create(self, record: dict[str, Any], timeout: float = 0.0) -> None:
        from google.api_core import exceptions as gexc

        if self._key in self._store:
            raise gexc.AlreadyExists(self._key)
        self._store[self._key] = dict(record)

    def get(self, timeout: float = 0.0) -> _Snapshot:
        return _Snapshot(self._store.get(self._key))

    def update(self, patch: dict[str, Any], timeout: float = 0.0) -> None:
        self._store.setdefault(self._key, {}).update(patch)


class _Db:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def collection(self, _name: str) -> Any:
        store = self.store
        return type("C", (), {"document": staticmethod(lambda k: _Doc(store, k))})()


class _Handlers:
    """Records every envelope it is asked to run, and can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[WorkEnvelope] = []
        self.error = error

    def run(self, envelope: WorkEnvelope) -> HandlerResult:
        self.calls.append(envelope)
        if self.error is not None:
            raise self.error
        return HandlerResult(detail={"ok": True})


class _DeadLetters:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    def record(
        self, envelope: WorkEnvelope, error: Exception, *, attempt: int, permanent: bool
    ) -> None:
        self.recorded.append(
            {
                "dedup_key": envelope.dedup_key,
                "error": type(error).__name__,
                "attempt": attempt,
                "permanent": permanent,
            }
        )

    def record_unparseable(self, body: dict[str, Any], error: Exception) -> None:
        self.recorded.append({"unparseable": True, "error": type(error).__name__})


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point the dispatcher's module-level singletons at fakes."""
    db = _Db()
    claims = WorkClaimRepository(client=db, lease_seconds=900)  # type: ignore[arg-type]
    handlers = _Handlers()
    dlq = _DeadLetters()

    monkeypatch.setattr(dispatcher_main, "_claims", claims)
    monkeypatch.setattr(dispatcher_main, "_handlers", handlers)
    monkeypatch.setattr(dispatcher_main, "_deadletter", dlq)

    return type("Wired", (), {"db": db, "claims": claims, "handlers": handlers, "dlq": dlq})()


def _envelope(partition: str | None = "security", **kw: Any) -> WorkEnvelope:
    return WorkEnvelope.for_work(
        message_id=kw.pop("message_id", "m1"),
        review_id=kw.pop("review_id", "rev-acme-2026-q3"),
        run_id=kw.pop("run_id", "run-1"),
        kind=kw.pop("kind", WorkKind.DRAFT_ANSWER),
        round_id=kw.pop("round_id", "rnd-1"),
        partition=partition,
        **kw,
    )


def _dispatch(envelope: WorkEnvelope, attempt: int = 1) -> dict[str, Any]:
    from dispatcher.push import PushMessage

    response = Response()
    outcome = dispatcher_main._dispatch(
        PushMessage(envelope=envelope, pubsub_message_id="p1", delivery_attempt=attempt),
        response,
    )
    return {**outcome, "status_code": response.status_code}


# ---------------------------------------------------------------------------------


class TestTheGuardIsActuallyCalled:
    """Not "does the claim repository work" -- that is `test_claims.py`. This asks
    whether the dispatcher actually consults it before doing work."""

    def test_a_duplicate_delivery_does_not_run_the_handler(self, wired: Any) -> None:
        envelope = _envelope()

        first = _dispatch(envelope)
        second = _dispatch(envelope)

        assert first["result"] == "ok"
        assert second["result"] == "duplicate"
        # THE assertion. One delivery, one execution.
        assert len(wired.handlers.calls) == 1

    def test_a_duplicate_is_acked_not_retried(self, wired: Any) -> None:
        """A nack here would redeliver completed work forever."""
        envelope = _envelope()
        _dispatch(envelope)

        assert _dispatch(envelope)["status_code"] == 200

    def test_a_held_claim_does_not_run_the_handler_either(self, wired: Any) -> None:
        """Concurrent delivery while another worker is live: no work, and a nack so it
        comes back rather than being lost."""
        envelope = _envelope()
        wired.claims.claim(
            envelope.dedup_key,
            run_id="other-run",
            kind="draft_answer",
            review_id="rev-acme-2026-q3",
            worker="other-worker",
        )

        outcome = _dispatch(envelope)

        assert outcome["result"] == "held"
        assert outcome["status_code"] == 409
        assert wired.handlers.calls == []

    def test_different_partitions_each_run(self, wired: Any) -> None:
        """ADR-0005 end to end: three department messages, three executions."""
        for department in ("security", "legal", "engineering"):
            _dispatch(_envelope(partition=department))

        assert len(wired.handlers.calls) == 3
        assert {e.partition for e in wired.handlers.calls} == {
            "security",
            "legal",
            "engineering",
        }


class TestAckDecisions:
    def test_success_acks(self, wired: Any) -> None:
        assert _dispatch(_envelope())["status_code"] == 204

    def test_a_transient_failure_nacks_for_redelivery(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(wired.handlers, "error", RuntimeError("503 backend unavailable"))

        outcome = _dispatch(_envelope(), attempt=1)

        assert outcome["status_code"] == 500
        assert outcome["result"] == "retry"
        assert wired.dlq.recorded == []

    def test_a_transient_failure_releases_the_claim_so_a_retry_can_run(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the retry finds a live lease and bounces for 15 minutes."""
        envelope = _envelope()
        monkeypatch.setattr(wired.handlers, "error", RuntimeError("503"))
        _dispatch(envelope)

        monkeypatch.setattr(wired.handlers, "error", None)
        assert _dispatch(envelope, attempt=2)["result"] == "ok"

    def test_exhausted_attempts_dead_letter_and_ack(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stop burning quota, but leave a record."""
        monkeypatch.setattr(wired.handlers, "error", RuntimeError("503"))

        outcome = _dispatch(_envelope(), attempt=dispatcher_main.MAX_ATTEMPTS)

        assert outcome["result"] == "dead_lettered"
        assert outcome["status_code"] == 200
        assert wired.dlq.recorded[0]["permanent"] is False

    @pytest.mark.parametrize(
        "error",
        [
            ContractViolation("payload is malformed"),
            IllegalTransition("drafting -> delivered is not legal"),
        ],
    )
    def test_permanent_failures_dead_letter_on_the_first_attempt(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        """Retrying these five times would be five identical failures."""
        monkeypatch.setattr(wired.handlers, "error", error)

        outcome = _dispatch(_envelope(), attempt=1)

        assert outcome["result"] == "dead_lettered"
        assert outcome["permanent"] is True
        assert wired.dlq.recorded[0]["permanent"] is True

    def test_a_dead_lettered_message_is_recorded_before_it_is_acked(
        self, wired: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exit criterion: exhausted retries land in the DLQ *with* a record, not
        silently."""
        monkeypatch.setattr(wired.handlers, "error", RuntimeError("boom"))

        _dispatch(_envelope(), attempt=dispatcher_main.MAX_ATTEMPTS)

        assert len(wired.dlq.recorded) == 1
        assert wired.dlq.recorded[0]["dedup_key"] == _envelope().dedup_key


class TestPushParsing:
    @staticmethod
    def _body(envelope: WorkEnvelope) -> dict[str, Any]:
        return {
            "message": {
                "data": base64.b64encode(envelope.model_dump_json().encode()).decode(),
                "messageId": "12345",
            },
            "subscription": "projects/p/subscriptions/s",
        }

    def test_a_valid_push_decodes(self) -> None:
        envelope = _envelope()
        parsed = parse_push(self._body(envelope))
        assert parsed.envelope.dedup_key == envelope.dedup_key
        assert parsed.pubsub_message_id == "12345"

    def test_pubsub_delivery_attempt_wins_over_the_envelope(self) -> None:
        """Ours is only as accurate as the publisher; a redelivery never increments it."""
        body = {**self._body(_envelope()), "deliveryAttempt": 4}
        assert parse_push(body).attempt == 4

    def test_the_envelope_attempt_is_the_fallback(self) -> None:
        """Subscriptions without a dead-letter policy send no deliveryAttempt."""
        assert parse_push(self._body(_envelope())).attempt == 1

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"message": "not-an-object"},
            {"message": {}},
            {"message": {"data": "!!! not base64 !!!"}},
            {"message": {"data": base64.b64encode(b"not json").decode()}},
            {"message": {"data": base64.b64encode(b'{"kind":"nope"}').decode()}},
        ],
    )
    def test_malformed_bodies_raise_contract_violation(self, body: dict[str, Any]) -> None:
        """All permanent: identical bytes will fail identically on every retry."""
        with pytest.raises(ContractViolation):
            parse_push(body)

    def test_an_unparseable_body_is_dead_lettered_not_retried(self, wired: Any) -> None:
        import asyncio

        class _Request:
            async def json(self) -> dict[str, Any]:
                return {"message": {"data": "!!!"}}

        response = Response()
        outcome = asyncio.run(dispatcher_main.push(_Request(), response))  # type: ignore[arg-type]

        assert outcome["result"] == "dead_lettered"
        assert response.status_code == 200
        assert wired.dlq.recorded[0]["unparseable"] is True

    def test_a_body_that_is_not_even_json_is_discarded(self, wired: Any) -> None:
        import asyncio

        class _Request:
            async def json(self) -> dict[str, Any]:
                raise ValueError("Expecting value: line 1 column 1")

        response = Response()
        outcome = asyncio.run(dispatcher_main.push(_Request(), response))  # type: ignore[arg-type]

        assert outcome["result"] == "discarded"
        assert response.status_code == 200


class TestCrashMidRun:
    def test_work_abandoned_by_a_dead_instance_is_picked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exit criterion: kill the dispatcher mid-run, restart, no data loss.

        Simulated at the claim level -- an instance that claimed and never completed --
        because that is exactly the state a culled Cloud Run instance leaves behind.
        """
        db = _Db()
        claims = WorkClaimRepository(client=db, lease_seconds=0)  # type: ignore[arg-type]
        handlers = _Handlers()
        monkeypatch.setattr(dispatcher_main, "_claims", claims)
        monkeypatch.setattr(dispatcher_main, "_handlers", handlers)
        monkeypatch.setattr(dispatcher_main, "_deadletter", _DeadLetters())

        envelope = _envelope()
        claims.claim(
            envelope.dedup_key,
            run_id="run-that-died",
            kind="draft_answer",
            review_id="rev-acme-2026-q3",
            worker="instance-culled",
        )

        outcome = _dispatch(envelope)

        assert outcome["result"] == "ok"
        assert len(handlers.calls) == 1
        assert claims.get(envelope.dedup_key)["state"] == "completed"  # type: ignore[index]
