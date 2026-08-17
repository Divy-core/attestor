"""Memory Bank as the canonical store for what was promised to a customer.

A commitment is the most consequential thing Attestor holds. "Kestrel Data does not offer
on-premises deployment" was written to a customer in round 1; if round 2 offers it three
weeks later, the review fails and the deal is at risk. That is the fact this store keeps,
across sessions, across rounds, across weeks — which is precisely what Memory Bank is for
and why Track 3 asks for it.

## Facts, not generated memories

Memory Bank can *generate* memories by having a model read a session transcript and
extract what seems important. That is the wrong mechanism here and it is not used. A
commitment is not an impression to be re-derived — it is a sentence that was sent to a
customer, and it must come back byte-identical. So commitments are written with
`memories.create(fact=...)`, which stores exactly what it is given.

## Scope is the tenant boundary

Every memory is scoped `{"review_id": ...}`. Retrieval is scoped the same way, so one
customer's commitments cannot surface in another customer's review — the isolation is in
the store's own addressing rather than in a filter this code has to remember to apply.

## Unreachable is not empty

`for_review` raises `ContextUnavailable` when Memory Bank cannot be read. It must never
return `[]` on failure: an empty commitment list is indistinguishable from "this customer
has no history", which silently disables the consistency check for the entire round and
leaves the run reporting success. That mistake has already been made four times in this
project (see `attestor_core.errors.ContextUnavailable`); this is the fifth place it could
have been made and was not.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from attestor_core.domain import Commitment
from attestor_core.errors import ContextUnavailable
from attestor_platform.config import default_region, project_id
from attestor_platform.retry import retrying

logger = logging.getLogger(__name__)

#: How many memories to pull for one review. A round-1 questionnaire yields a handful of
#: commitments, not hundreds; a cap keeps a pathological review from unbounded retrieval.
MAX_MEMORIES = 200

#: Memory Bank calls are retried on the same terms as every other client here.
#:
#: This was missing until Phase 6, and the omission was measured rather than reasoned
#: about. `close_round` on the 60-question deployed run makes one Memory Bank write per
#: commitment — 60 calls to the same service, over the same transport, against the same
#: per-minute regional quota that had already forced the drafting fan-out down — and with
#: no retry the first refusal failed the whole stage. It then exhausted all five Pub/Sub
#: delivery attempts, because every redelivery re-attempted all 60 writes into the same
#: congestion. The review was left at `assembling` with its answers persisted and no
#: commitments recorded.
#:
#: Jittered for the reason `attestor_platform.retry` explains: `close_round` writes in a
#: loop, so a fixed backoff would march every remaining commitment into the same second.
COMMITMENT_RETRY_ATTEMPTS = 4
COMMITMENT_RETRY_BACKOFF_SECONDS = 2.0
COMMITMENT_RETRY_JITTER_SECONDS = 1.5

#: Commitments are stored as natural-language facts -- that is what Memory Bank indexes
#: and what a human reading the console sees. The question id rides along in a suffix so
#: a retrieved fact can be matched back to the question that produced it.
#:
#: A fact WITHOUT the suffix is still usable: ADR-0004 matches commitments to questions by
#: embedding similarity, so a memory written by something else still participates in the
#: consistency check. The suffix makes exact matching possible; its absence degrades to
#: semantic matching rather than dropping the commitment.
_REF = re.compile(r"\(commitment ref:\s*([0-9a-f]{16})\)\s*$")


def encode_fact(statement: str, question_id: str) -> str:
    """Render a commitment as the fact string stored in Memory Bank."""
    return f"{statement.strip()} (commitment ref: {question_id})"


def decode_fact(fact: str) -> tuple[str, str | None]:
    """Split a stored fact back into ``(statement, question_id)``."""
    match = _REF.search(fact)
    if match is None:
        return fact.strip(), None
    return fact[: match.start()].strip(), match.group(1)


class MemoryBankCommitments:
    """Reads and writes commitments in Vertex AI Memory Bank.

    Scoped to one Agent Engine, which is the resource Memory Bank hangs off. Phase 5
    replaces that engine with the deployed fleet; nothing here changes when it does,
    because the engine is only an addressing scope.
    """

    def __init__(
        self,
        engine_id: str,
        project: str | None = None,
        location: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.engine_id = engine_id
        self.project = project or project_id()
        self.location = location or default_region()
        self._client = client

    @property
    def resource_name(self) -> str:
        return (
            f"projects/{self.project}/locations/{self.location}/reasoningEngines/{self.engine_id}"
        )

    def _memories(self) -> Any:
        if self._client is None:
            import vertexai

            self._client = vertexai.Client(project=self.project, location=self.location)
        return self._client.agent_engines.memories

    @staticmethod
    def scope_for(review_id: str) -> dict[str, str]:
        """The tenant key. Identical on write and read, by construction."""
        return {"review_id": review_id}

    def record(self, commitment: Commitment) -> None:
        """Write one commitment. Raises rather than reporting a silent no-op.

        Transient refusals are waited out first; a permanent one still raises on the first
        attempt. `ContextUnavailable` from here means the commitment is genuinely not on
        file, which is exactly what the caller must not be allowed to mistake for success.

        Raises:
            ContextUnavailable: If the write could not be performed.
        """
        try:
            operation = retrying(
                lambda: self._memories().create(
                    name=self.resource_name,
                    fact=encode_fact(commitment.statement, commitment.question_id),
                    scope=self.scope_for(commitment.review_id),
                ),
                attempts=COMMITMENT_RETRY_ATTEMPTS,
                backoff_seconds=COMMITMENT_RETRY_BACKOFF_SECONDS,
                jitter_seconds=COMMITMENT_RETRY_JITTER_SECONDS,
                description=f"memory bank write {commitment.question_id}",
            )
        except Exception as exc:
            raise ContextUnavailable(
                f"could not write commitment {commitment.commitment_id} to Memory Bank: "
                f"{type(exc).__name__}: {exc}",
                review_id=commitment.review_id,
                question_id=commitment.question_id,
            ) from exc

        logger.info(
            "commitment recorded to Memory Bank",
            extra={
                "review_id": commitment.review_id,
                "question_id": commitment.question_id,
                "operation": getattr(operation, "name", None),
            },
        )

    def for_review(self, review_id: str) -> list[tuple[str, str]]:
        """Every commitment on file for this review, as ``(question_id, statement)``.

        Returns an empty list only when the review genuinely has no commitments.

        Raises:
            ContextUnavailable: If Memory Bank could not be read. Never returns `[]` to
                mean "unreachable" -- that would disable the consistency check silently.
        """
        try:
            retrieved = retrying(
                lambda: list(
                    self._memories().retrieve(
                        name=self.resource_name,
                        scope=self.scope_for(review_id),
                        simple_retrieval_params={"page_size": MAX_MEMORIES},
                    )
                ),
                attempts=COMMITMENT_RETRY_ATTEMPTS,
                backoff_seconds=COMMITMENT_RETRY_BACKOFF_SECONDS,
                jitter_seconds=COMMITMENT_RETRY_JITTER_SECONDS,
                description=f"memory bank read {review_id}",
            )
        except Exception as exc:
            raise ContextUnavailable(
                f"could not read commitments for {review_id} from Memory Bank: "
                f"{type(exc).__name__}: {exc}. Refusing to continue as though the "
                "customer has no history -- that would disable the consistency check.",
                review_id=review_id,
            ) from exc

        pairs: list[tuple[str, str]] = []
        for item in retrieved:
            fact = _fact_text(item)
            if not fact:
                continue
            statement, question_id = decode_fact(fact)
            # A fact with no ref still counts: ADR-0004 matches by meaning, so dropping
            # it would lose a real commitment for the sake of a missing suffix.
            pairs.append((question_id or "", statement))

        logger.info(
            "commitments loaded from Memory Bank",
            extra={"review_id": review_id, "count": len(pairs)},
        )
        return pairs


def _fact_text(item: Any) -> str:
    """Pull the fact out of a retrieved memory, whatever shape it arrives in.

    The retrieval response nests the memory one level down and the SDK has moved this
    field before. Defensive on shape, not on failure: an unreadable item is skipped, an
    unreadable *call* raises in the caller above.
    """
    memory = getattr(item, "memory", None) or item
    fact = getattr(memory, "fact", None)
    if isinstance(fact, str):
        return fact
    if isinstance(memory, dict):
        value = memory.get("fact")
        if isinstance(value, str):
            return value
    return ""
