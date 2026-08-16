"""The distinction this project has got wrong four times.

A read that finds nothing returns an empty collection. A read that **could not be
performed** raises `ContextUnavailable`. Collapsing the two is the single most repeated
bug in this build, and it is invisible every time — no exception, no dead letter, a green
run and a smaller number:

1. Discovery Engine returning `[]` under a 429 → "the corpus has no answer".
2. Model Armor denying under a timeout → "this passage is poisoned".
3. Embeddings degrading under quota exhaustion → "these scores are cosines".
4. The commitment read returning `[]` when Firestore is unreachable → "this customer has
   no prior commitments", which disables the consistency check for the whole run.

These tests pin the two Phase 4 fixes so a future refactor that "helpfully" adds an
`except: return []` fails `make check` rather than a demo.
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from attestor_core.errors import ContextUnavailable


class TestCommitmentReadRaises:
    """The dangerous one: an empty commitment list disables the consistency check.

    Phase 4 moved the canonical store from Firestore to Memory Bank. The store's own
    behaviour is covered in `test_memory_commitments.py`; what these two pin is that the
    **caller** did not quietly reintroduce a `try/except: return []` around it during the
    move. That is exactly the shape of edit that would restore the bug while every other
    test stayed green.
    """

    @staticmethod
    def _patch_store(monkeypatch: pytest.MonkeyPatch, *, fail: bool) -> None:
        import attestor_platform.memory as memory_module

        class _Store:
            def __init__(self, **_: Any) -> None: ...

            def for_review(self, review_id: str) -> list[tuple[str, str]]:
                if fail:
                    raise ContextUnavailable(
                        f"could not read commitments for {review_id} from Memory Bank. "
                        "Refusing to continue as though the customer has no history -- "
                        "that would disable the consistency check.",
                        review_id=review_id,
                    )
                return []

        monkeypatch.setattr(memory_module, "MemoryBankCommitments", _Store)

    def test_an_unreachable_store_raises_rather_than_reporting_no_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROJECT_ID", "test-project")
        import run_review

        self._patch_store(monkeypatch, fail=True)

        with pytest.raises(ContextUnavailable) as caught:
            run_review.load_commitments("rev-acme-2026-q3")

        # The message has to say what was refused and why, because the person reading it
        # is mid-demo.
        assert "consistency check" in str(caught.value)

    def test_a_reachable_store_with_no_commitments_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half. A genuinely new customer has no history, and that is data."""
        monkeypatch.setenv("PROJECT_ID", "test-project")
        import run_review

        self._patch_store(monkeypatch, fail=False)

        assert run_review.load_commitments("rev-brand-new") == []


class TestRegistryReadRaises:
    """Less dangerous, but on a demo surface: an empty registry panel is a claim."""

    @staticmethod
    def _client() -> Any:
        from attestor_platform.registry.agent_registry import AgentRegistry

        client = AgentRegistry.__new__(AgentRegistry)
        client.project, client.region = "p", "us-central1"
        client._timeout = 1.0  # type: ignore[attr-defined]
        client._token = lambda: "fake"  # type: ignore[method-assign]
        return client

    def test_an_unreachable_registry_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import attestor_platform.registry.agent_registry as registry

        def _urlopen(*_: Any, **__: Any) -> Any:
            raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(registry.urllib.request, "urlopen", _urlopen)

        with pytest.raises(ContextUnavailable):
            self._client().list_agents()

    def test_a_reachable_registry_with_no_agents_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json as _json

        import attestor_platform.registry.agent_registry as registry

        class _Response:
            def read(self) -> bytes:
                return _json.dumps({"agents": []}).encode()

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: Any) -> None: ...

        monkeypatch.setattr(registry.urllib.request, "urlopen", lambda *a, **k: _Response())

        assert self._client().list_agents() == []
