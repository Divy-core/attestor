"""Memory Bank commitment storage.

No network. The live round trip is proven separately in
`docs/proof/memory-bank-recall.txt`; what is pinned here is the encoding, the scoping,
and — the one that matters — that an unreachable store raises instead of reporting that
the customer has no history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from attestor_core.domain import Commitment
from attestor_core.errors import ContextUnavailable
from attestor_platform.memory import MemoryBankCommitments, decode_fact, encode_fact
from attestor_platform.memory import commitments as commitments_module

STATEMENT = "Kestrel Data does not offer on-premises or self-hosted deployment."
QUESTION_ID = "928a74e05ba09dff"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry without the backoff.

    The failing fakes below raise `503`, which is transient and therefore now retried --
    so without this the unavailability tests would each sit through ~14 seconds of real
    sleeping to assert something about their final state. The attempt *count* is what
    those tests are about, and it is preserved.
    """
    monkeypatch.setattr(commitments_module, "COMMITMENT_RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(commitments_module, "COMMITMENT_RETRY_JITTER_SECONDS", 0.0)


class _Memory:
    def __init__(self, fact: str) -> None:
        self.fact = fact


class _Retrieved:
    """Retrieval nests the memory one level down."""

    def __init__(self, fact: str) -> None:
        self.memory = _Memory(fact)


class _Memories:
    def __init__(
        self,
        stored: list[str] | None = None,
        fail: bool = False,
        fail_times: int = 0,
        error: str = "503 Service Unavailable",
    ) -> None:
        self.stored = list(stored or [])
        self.fail = fail
        #: Fail this many times, then succeed. How a transient blip is modelled, as
        #: distinct from `fail=True`, which is an outage.
        self.fail_times = fail_times
        self.error = error
        self.created: list[dict[str, Any]] = []
        self.scopes: list[dict[str, str]] = []
        self.attempts = 0

    def _maybe_fail(self) -> None:
        self.attempts += 1
        if self.fail or self.attempts <= self.fail_times:
            raise RuntimeError(self.error)

    def create(self, *, name: str, fact: str, scope: dict[str, str]) -> Any:
        self._maybe_fail()
        self.created.append({"name": name, "fact": fact, "scope": scope})
        self.stored.append(fact)
        return type("Op", (), {"name": "operations/1"})()

    def retrieve(self, *, name: str, scope: dict[str, str], **_: Any) -> list[Any]:
        self._maybe_fail()
        self.scopes.append(scope)
        return [_Retrieved(f) for f in self.stored]


class _Client:
    def __init__(self, memories: _Memories) -> None:
        self.agent_engines = type("AE", (), {"memories": memories})()


def _store(memories: _Memories) -> MemoryBankCommitments:
    return MemoryBankCommitments(
        engine_id="8598754324522205184",
        project="attestor-505506",
        location="us-central1",
        client=_Client(memories),
    )


def _commitment() -> Commitment:
    return Commitment(
        commitment_id="0123456789abcdef",
        review_id="rev-acme-2026-q3",
        round_id="rnd-1",
        question_id=QUESTION_ID,
        statement=STATEMENT,
        made_at=datetime.now(UTC),
    )


class TestFactEncoding:
    def test_a_fact_round_trips(self) -> None:
        statement, question_id = decode_fact(encode_fact(STATEMENT, QUESTION_ID))
        assert statement == STATEMENT
        assert question_id == QUESTION_ID

    def test_the_statement_survives_byte_identical(self) -> None:
        """A commitment is a sentence that was sent to a customer. It comes back exactly,
        not paraphrased."""
        assert decode_fact(encode_fact(STATEMENT, QUESTION_ID))[0] == STATEMENT

    def test_a_fact_without_a_ref_still_yields_its_statement(self) -> None:
        """A memory written by something else is still a commitment. ADR-0004 matches by
        meaning, so dropping it for a missing suffix would lose a real one."""
        statement, question_id = decode_fact("We never had a customer data breach.")
        assert statement == "We never had a customer data breach."
        assert question_id is None


class TestScoping:
    def test_writes_and_reads_use_the_same_scope(self) -> None:
        """Isolation lives in the store's addressing, not in a filter we must remember."""
        memories = _Memories()
        store = _store(memories)
        store.record(_commitment())
        store.for_review("rev-acme-2026-q3")

        assert memories.created[0]["scope"] == {"review_id": "rev-acme-2026-q3"}
        assert memories.scopes[0] == {"review_id": "rev-acme-2026-q3"}

    def test_the_engine_resource_name_is_well_formed(self) -> None:
        store = _store(_Memories())
        assert store.resource_name == (
            "projects/attestor-505506/locations/us-central1/reasoningEngines/8598754324522205184"
        )


class TestReadWrite:
    def test_a_recorded_commitment_reads_back(self) -> None:
        memories = _Memories()
        store = _store(memories)
        store.record(_commitment())

        assert store.for_review("rev-acme-2026-q3") == [(QUESTION_ID, STATEMENT)]

    def test_a_review_with_no_commitments_returns_empty(self) -> None:
        """Genuinely empty is data, and must not raise."""
        assert _store(_Memories()).for_review("rev-brand-new") == []


class TestUnavailableIsNotEmpty:
    """The fifth place this project could have collapsed the two, and did not."""

    def test_an_unreachable_read_raises(self) -> None:
        with pytest.raises(ContextUnavailable) as caught:
            _store(_Memories(fail=True)).for_review("rev-acme-2026-q3")
        assert "consistency check" in str(caught.value)

    def test_an_unreachable_write_raises(self) -> None:
        """A commitment that silently failed to persist is worse than one never made:
        round 2 would have no record of what round 1 promised."""
        with pytest.raises(ContextUnavailable):
            _store(_Memories(fail=True)).record(_commitment())


class TestTransientFailuresAreWaitedOut:
    """`close_round` writes one memory per commitment against a per-minute regional quota.

    Sixty of those in a loop with no retry is how the 60-question deployed run came to
    leave its review at `assembling`: the first refusal failed the stage, and every one of
    the five Pub/Sub redeliveries re-attempted all sixty writes into the same congestion.
    """

    def test_a_write_survives_a_transient_refusal(self) -> None:
        memories = _Memories(fail_times=2, error="429 RESOURCE_EXHAUSTED")
        _store(memories).record(_commitment())

        assert memories.attempts == 3
        assert len(memories.created) == 1

    def test_a_read_survives_a_dropped_stream(self) -> None:
        """The family found on the engine path, which this client shares a transport with."""
        memories = _Memories(
            stored=[encode_fact(STATEMENT, QUESTION_ID)],
            fail_times=1,
            error="peer closed connection without sending complete message body",
        )

        assert _store(memories).for_review("rev-acme-2026-q3") == [(QUESTION_ID, STATEMENT)]
        assert memories.attempts == 2

    def test_a_permanent_refusal_is_not_retried(self) -> None:
        """Backing off four times on a permission error burns four ack deadlines to arrive
        at the same denial."""
        memories = _Memories(fail=True, error="403 PERMISSION_DENIED on Memory Bank")
        with pytest.raises(ContextUnavailable):
            _store(memories).record(_commitment())

        assert memories.attempts == 1

    def test_retrying_gives_up_rather_than_looping(self) -> None:
        memories = _Memories(fail=True, error="503 Service Unavailable")
        with pytest.raises(ContextUnavailable):
            _store(memories).record(_commitment())

        assert memories.attempts == commitments_module.COMMITMENT_RETRY_ATTEMPTS
