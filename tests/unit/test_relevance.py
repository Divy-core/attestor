"""Relevance scoring: the cache, the fallback, and the arithmetic.

No network. The embedding client is a fake whose vectors are chosen so the expected
ordering is arithmetic rather than a hope about what a real model would say.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from attestor_platform.search.relevance import (
    RelevanceScorer,
    cosine,
    lexical_overlap,
)


class _Embedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _Metadata:
    def __init__(self, count: int) -> None:
        self.billable_character_count = count


class _Response:
    def __init__(self, vectors: list[list[float]], characters: int) -> None:
        self.embeddings = [_Embedding(v) for v in vectors]
        self.metadata = _Metadata(characters)


class _FakeModels:
    """Maps text to a vector by first character, so similarity is predictable."""

    VECTORS: ClassVar[dict[str, list[float]]] = {
        "q": [1.0, 0.0, 0.0],
        "a": [1.0, 0.0, 0.0],  # identical to the query
        "b": [0.0, 1.0, 0.0],  # orthogonal
        "c": [1.0, 1.0, 0.0],  # halfway
    }

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    def embed_content(self, *, model: str, contents: list[str], config: Any) -> _Response:
        del model, config
        if self.fail:
            raise RuntimeError("503 embedding unavailable")
        self.calls.append(list(contents))
        return _Response(
            [self.VECTORS[text[0]] for text in contents],
            sum(len(text) for text in contents),
        )


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


class TestArithmetic:
    def test_cosine_is_cosine(self) -> None:
        assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
        assert math.isclose(cosine([1.0, 1.0], [1.0, 0.0]), 0.7071, abs_tol=1e-4)

    def test_cosine_is_floored_not_negative(self) -> None:
        """A negative similarity is not a meaningful retrieval score."""
        assert cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0

    def test_cosine_of_a_zero_vector_is_zero_not_a_crash(self) -> None:
        assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_lexical_overlap_ignores_stopwords(self) -> None:
        """Otherwise 'do you have' matches every document in the corpus equally."""
        assert lexical_overlap("Do you encrypt data at rest?", "encrypt data at rest") == 1.0
        assert lexical_overlap("Do you encrypt data at rest?", "the cafeteria menu") == 0.0

    def test_lexical_overlap_does_not_stem(self) -> None:
        """A stated limit of the fallback: `encrypt` does not match `encryption`.

        Recorded as a test rather than a comment so nobody later reads a 0.67 here as a
        bug. It is why this is the fallback and cosine is the primary.
        """
        assert lexical_overlap("Do you encrypt data at rest?", "encryption of data at rest") < 1.0

    def test_lexical_overlap_of_an_empty_query_is_zero(self) -> None:
        assert lexical_overlap("do you have the", "anything") == 0.0


class TestScoring:
    def test_scores_are_relevance_not_position(self) -> None:
        """The point of the whole module: the first result is not automatically the best."""
        client = _FakeClient()
        scorer = RelevanceScorer(client=client)

        scores = scorer.score("query text", ["b irrelevant", "a identical", "c halfway"])

        assert scores[1] == 1.0
        assert scores[0] == 0.0
        assert math.isclose(scores[2], 0.7071, abs_tol=1e-4)

    def test_passages_are_embedded_once_and_cached(self) -> None:
        client = _FakeClient()
        scorer = RelevanceScorer(client=client)

        scorer.score("query one", ["a passage", "b passage"])
        scorer.score("query two", ["a passage", "b passage"])

        embedded = [text for call in client.models.calls for text in call]
        assert embedded.count("a passage") == 1
        assert embedded.count("b passage") == 1
        # Both queries are still embedded: a query is not a cached passage.
        assert embedded.count("query one") == 1
        assert embedded.count("query two") == 1

    def test_query_and_passages_use_different_task_types(self) -> None:
        """Asymmetric embedding is what separates a good match from a bad one."""
        recorded: list[str] = []

        class _RecordingModels(_FakeModels):
            def embed_content(self, *, model: str, contents: list[str], config: Any) -> _Response:
                recorded.append(config.task_type)
                return super().embed_content(model=model, contents=contents, config=config)

        client = _FakeClient()
        client.models = _RecordingModels()
        RelevanceScorer(client=client).score("query", ["a passage"])

        assert recorded == [RelevanceScorer.QUERY_TASK, RelevanceScorer.DOCUMENT_TASK]

    def test_billable_characters_are_recorded(self) -> None:
        client = _FakeClient()
        scorer = RelevanceScorer(client=client)

        scorer.score("query", ["a passage"])

        assert scorer.billable_characters == len("query") + len("a passage")

    def test_no_passages_costs_no_call(self) -> None:
        client = _FakeClient()

        assert RelevanceScorer(client=client).score("query", []) == []
        assert client.models.calls == []

    def test_embedding_failure_degrades_to_lexical(self) -> None:
        """A scorer blip must not take a 312-question run down with it."""
        client = _FakeClient()
        client.models.fail = True
        scorer = RelevanceScorer(client=client)

        scores = scorer.score("encrypt data at rest", ["data at rest encryption", "lunch menu"])

        assert scorer.last_method == "lexical"
        assert scores[0] > scores[1]

    def test_method_is_reported_per_batch(self) -> None:
        client = _FakeClient()
        scorer = RelevanceScorer(client=client)

        scorer.score("query", ["a passage"])
        assert scorer.last_method == "embedding"

        client.models.fail = True
        scorer.score("query", ["different passage"])
        assert scorer.last_method == "lexical"
