"""Relevance scoring: a measured distance, not a position in a list.

Discovery Engine's standard-edition search surface returns **no relevance score**
(probed directly: no `model_scores`, no `relevance_score` in `derived_struct_data`), and
Enterprise edition -- which does -- needs an engine/app serving config and a paid tier.
The first implementation therefore derived a score from rank: `0.95 - rank * 0.1`.

That was worse than it looked. `compute_confidence` consumes `max_retrieval_score` and
`mean_retrieval_score`, so with rank-derived inputs **two of its four signals were
positional artifacts**: the top hit scored 0.95 whether it was a bullseye or barely
related, and confidence collapsed into a function of citation count and hedging alone.
Confidence drives `requires_human`, which drives the approval queue. And the claim that
confidence is computed deterministically from observable signals does not survive half
the signals meaning nothing.

So the score is now the **cosine similarity between the question and the retrieved
passage**, both embedded with a Vertex text-embedding model. That is deterministic,
genuinely relevance-based, cheap at this volume, and -- the part that matters for the
architecture claim -- it is a measured distance between two vectors, not a model being
asked how confident it feels.

Two properties worth stating:

* **Passage vectors are cached by content hash.** The same snippet retrieved by four
  query variants and by thirty questions is embedded once.
* **Failure degrades, never crashes.** If embedding is unavailable the scorer falls back
  to normalised lexical overlap and says so through `last_method`, because a retrieval
  run that dies because the scorer had a blip is a worse outcome than a weaker score.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections.abc import Sequence
from typing import Any

from attestor_platform.config import EMBEDDING_MODEL, genai_client
from attestor_platform.retry import TRANSIENT_MARKERS, is_transient

logger = logging.getLogger(__name__)

#: Instances per embed call. Vertex accepts far more, but the request is token-limited
#: and a smaller batch keeps one oversized snippet from failing the whole group.
EMBED_BATCH = 16

#: Transient failures are waited out before the lexical fallback is taken. Eight drafting
#: workers embedding concurrently WILL see 429s on a 312-question run; that is expected
#: load, not an outage.
EMBED_RETRIES = 4
EMBED_BACKOFF_SECONDS = 1.0

#: Which failures are worth retrying: the shared list in `attestor_platform.retry`, not a
#: second copy. This module had its own, agreeing on 429 and 503 and missing the
#: dropped-stream family entirely — the failure mode of a duplicated whitelist is that the
#: copy which learns something new does not teach the others.
#:
#: Kept as module-level names because the retry loop below is not a plain `retrying()` call:
#: it falls back to lexical scoring rather than raising, which is a decision only this
#: module can make.
_TRANSIENT_MARKERS = TRANSIENT_MARKERS
_is_transient = is_transient


#: Cosine over `text-embedding-005` does not use the full 0..1 range: unrelated text in
#: the same domain still scores ~0.6. Scores are passed through unclamped at the top and
#: floored at 0, and the confidence thresholds in `attestor_core.policy` are calibrated
#: against the measured distribution rather than against an assumed scale.
_MIN_SCORE = 0.0
_MAX_SCORE = 1.0

_WORD = re.compile(r"[a-z0-9]+")
#: Words that match everything and therefore discriminate nothing.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS}


def lexical_overlap(query: str, passage: str) -> float:
    """Fallback score: what share of the query's content words the passage contains.

    Weaker than cosine and honest about it. Free, deterministic, and never unavailable,
    which is exactly what a fallback has to be.
    """
    query_terms = _tokens(query)
    if not query_terms:
        return 0.0
    passage_terms = _tokens(passage)
    return len(query_terms & passage_terms) / len(query_terms)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity between two vectors, floored at zero."""
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(_MIN_SCORE, min(_MAX_SCORE, dot / (left_norm * right_norm)))


class RelevanceScorer:
    """Scores retrieved passages against the question that retrieved them.

    Thread-safe: drafting fans out across eight workers and they share one scorer, so the
    passage cache is guarded. Sharing it is the point — the cache is what keeps the cost
    of this negligible over a 312-question run.
    """

    #: Vertex task types. Query and document are embedded asymmetrically because that is
    #: what the model was trained for; embedding both as documents measurably narrows the
    #: gap between a good match and a bad one.
    QUERY_TASK = "RETRIEVAL_QUERY"
    DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"

    def __init__(self, client: Any | None = None, model: str = EMBEDDING_MODEL) -> None:
        self._client = client
        self._model = model
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        #: "embedding" or "lexical" — which method produced the last batch of scores.
        self.last_method = "embedding"
        #: Billable characters, so the embedding cost is reported rather than assumed.
        self.billable_characters = 0
        #: How many scoring batches used each method. `last_method` alone is misleading
        #: on a long run: one batch that fell back and recovered would report
        #: "embedding" at the end while part of the run was scored lexically.
        self.embedding_batches = 0
        self.lexical_batches = 0
        #: Transient embedding failures that were retried rather than fallen back from.
        self.throttled_batches = 0
        self._degraded = False

    # -- embedding ---------------------------------------------------------------------

    def _lazy_client(self) -> Any:
        if self._client is None:
            self._client = genai_client()
        return self._client

    def _embed(self, texts: Sequence[str], task: str) -> list[list[float]]:
        """Embed a batch, retrying transient failures, raising when it cannot be done.

        The retry is not decoration. Measured on a full 312-question run: eight drafting
        workers each embedding tens of passages produced
        `429 RESOURCE_EXHAUSTED` from Vertex, the scorer degraded to lexical overlap as
        designed, and **the run's scores silently became a different quantity**. Degrading
        is the right production behaviour and the wrong measurement behaviour, so a
        transient 429 is now waited out before the fallback is taken.
        """
        from google.genai import types

        client = self._lazy_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            chunk = list(texts[start : start + EMBED_BATCH])
            response = None
            last_error: Exception | None = None
            for attempt in range(EMBED_RETRIES):
                try:
                    response = client.models.embed_content(
                        model=self._model,
                        contents=chunk,
                        config=types.EmbedContentConfig(task_type=task),
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if not _is_transient(exc) or attempt == EMBED_RETRIES - 1:
                        raise
                    self.throttled_batches += 1
                    time.sleep(EMBED_BACKOFF_SECONDS * (2**attempt))
            if response is None:  # pragma: no cover - defensive; the loop raises first
                raise RuntimeError(f"embedding failed: {last_error}")

            metadata = getattr(response, "metadata", None)
            if metadata is not None:
                self.billable_characters += int(
                    getattr(metadata, "billable_character_count", 0) or 0
                )
            vectors.extend(list(embedding.values or []) for embedding in response.embeddings)
        return vectors

    def _passage_vectors(self, passages: Sequence[str]) -> list[list[float]]:
        """Vectors for passages, embedding only what is not already cached."""
        keys = [hashlib.sha256(passage.encode("utf-8")).hexdigest() for passage in passages]
        with self._lock:
            missing = [
                (key, passage)
                for key, passage in zip(keys, passages, strict=True)
                if key not in self._cache
            ]
        if missing:
            fresh = self._embed([passage for _, passage in missing], self.DOCUMENT_TASK)
            with self._lock:
                for (key, _), vector in zip(missing, fresh, strict=True):
                    self._cache[key] = vector
        with self._lock:
            return [self._cache[key] for key in keys]

    # -- the public surface --------------------------------------------------------------

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Score each passage against the query, 0..1.

        Falls back to lexical overlap on any embedding failure. The fallback is recorded
        on `last_method` and logged once per scorer rather than per call, because a
        degraded run should be obvious in the logs without drowning them.
        """
        if not passages:
            return []
        try:
            query_vector = self._embed([query], self.QUERY_TASK)[0]
            passage_vectors = self._passage_vectors(passages)
        except Exception as exc:
            if not self._degraded:
                logger.warning(
                    "relevance scoring degraded to lexical overlap (embedding failed): %s", exc
                )
                self._degraded = True
            self.last_method = "lexical"
            with self._lock:
                self.lexical_batches += 1
            return [lexical_overlap(query, passage) for passage in passages]

        self.last_method = "embedding"
        with self._lock:
            self.embedding_batches += 1
        return [cosine(query_vector, vector) for vector in passage_vectors]

    def score_one(self, query: str, passage: str) -> float:
        """Single-passage convenience. Same cache, same fallback."""
        return self.score(query, [passage])[0]
