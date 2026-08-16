"""Query expansion.

The measured failure this exists to fix: `"Recovery Time Objective"` returned 0 results
from a datastore containing that exact phrase, while `"recovery objective backup
restore"` returned it at 0.95. Interrogative framing and bare abbreviations retrieve
badly; noun phrases and expanded terms retrieve well.

No network — expansion heuristics are pure functions, which is the point of keeping them
deterministic rather than model-driven.
"""

from __future__ import annotations

import pytest

from attestor_core.domain import Department, Evidence
from attestor_platform.search.expansion import (
    ExpandingCorpusSearch,
    QueryExpander,
    heuristic_variants,
)


class TestInterrogativeStripping:
    @pytest.mark.parametrize(
        "question",
        [
            "Do you encrypt customer data at rest?",
            "What is your Recovery Time Objective?",
            "How frequently are user access reviews performed?",
            "Describe your break-glass access procedure.",
            "Provide your current list of subprocessors.",
            "Which cloud service providers do you use?",
            "Please confirm your encryption standard.",
        ],
    )
    def test_produces_a_declarative_variant(self, question: str) -> None:
        variants = heuristic_variants(question)
        assert variants, f"no variant produced for {question!r}"
        # The first variant drops the interrogative opener.
        assert variants[0].lower() != question.lower().rstrip("?.")

    def test_strips_the_measured_failure_case(self) -> None:
        variants = heuristic_variants("What is your Recovery Time Objective?")
        assert any("recovery time objective" in v.lower() for v in variants)
        assert not variants[0].lower().startswith("what is")


class TestSynonymExpansion:
    @pytest.mark.parametrize(
        ("question", "expected_term"),
        [
            ("RTO", "recovery time objective"),
            ("MFA", "multi-factor authentication"),
            ("CMEK", "customer managed encryption keys"),
            ("SBOM", "software bill of materials"),
            ("DPA", "data processing agreement"),
            ("SDLC", "software development lifecycle"),
            ("SCCs", "standard contractual clauses"),
            ("SLA", "service level agreement"),
        ],
    )
    def test_bare_abbreviations_expand(self, question: str, expected_term: str) -> None:
        """A bare abbreviation is the worst possible search query on its own."""
        variants = heuristic_variants(question)
        joined = " ".join(variants).lower()
        assert expected_term in joined, f"{question!r} did not expand to {expected_term!r}"

    def test_synonym_output_is_byte_stable(self) -> None:
        """Sorted, not set-ordered -- caching and prompt stability both depend on it."""
        first = heuristic_variants("RTO and RPO for disaster recovery")
        second = heuristic_variants("RTO and RPO for disaster recovery")
        assert first == second


class TestControlIds:
    @pytest.mark.parametrize("control", ["CC7.2", "CC6.1", "A.8.24", "A.5.15"])
    def test_control_id_becomes_a_variant(self, control: str) -> None:
        variants = heuristic_variants(f"{control} Are audit logs tamper-resistant?")
        assert any(control.upper() in v.upper() for v in variants)

    def test_no_control_id_produces_no_control_variant(self) -> None:
        variants = heuristic_variants("Are audit logs tamper-resistant?")
        assert all("CC" not in v for v in variants)


class TestExpander:
    def test_original_is_always_searched_first(self) -> None:
        expanded = QueryExpander().expand("Do you encrypt data at rest?")
        assert expanded.all_queries[0] == "Do you encrypt data at rest?"

    def test_variants_do_not_duplicate_the_original(self) -> None:
        expanded = QueryExpander().expand("Do you encrypt data at rest?")
        lowered = [q.lower() for q in expanded.all_queries]
        assert len(lowered) == len(set(lowered))

    def test_caches_by_question_id(self) -> None:
        """The same question recurs across rounds; re-expanding is pure waste."""
        expander = QueryExpander()
        first = expander.expand("Do you encrypt data at rest?")
        second = expander.expand("Do you encrypt data at rest?")
        assert first is second

    def test_reworded_round_two_hits_the_same_cache_entry(self) -> None:
        """Content-derived IDs mean round 2 reuses round 1's expansion."""
        expander = QueryExpander()
        first = expander.expand("Do you encrypt customer data at rest?")
        second = expander.expand("12. Do you encrypt customer data at rest?")
        assert second is first

    def test_model_disabled_by_default(self) -> None:
        """Measured decision: heuristics alone clear the gate at 95%."""
        assert QueryExpander().use_model is False


class _FakeSearch:
    """Records queries and returns canned evidence per query."""

    def __init__(self, responses: dict[str, list[Evidence]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def query(self, text: str, page_size: int = 5) -> list[Evidence]:
        self.queries.append(text)
        return self.responses.get(text, [])


def evidence(uri: str, score: float, section: str | None = None) -> Evidence:
    return Evidence(
        document_uri=uri,
        document_title=uri.rsplit("/", 1)[-1],
        section=section,
        content="content",
        score=score,
        department=Department.SECURITY,
    )


class TestRetrieval:
    def test_searches_every_variant(self) -> None:
        fake = _FakeSearch({})
        search = ExpandingCorpusSearch(Department.SECURITY, search=fake)  # type: ignore[arg-type]

        result = search.retrieve("What is your Recovery Time Objective?")

        assert len(fake.queries) == len(result.queries_run)
        assert fake.queries[0] == "What is your Recovery Time Objective?"

    def test_dedupes_by_document_and_section_keeping_best_score(self) -> None:
        """A document found weakly by three variants must not outrank a strong hit."""
        expander = QueryExpander()
        expanded = expander.expand("RTO")
        responses = {
            expanded.all_queries[0]: [evidence("gs://c/a.txt", 0.40)],
            expanded.all_queries[1]: [evidence("gs://c/a.txt", 0.95)],
        }
        fake = _FakeSearch(responses)
        search = ExpandingCorpusSearch(
            Department.SECURITY,
            expander=expander,
            search=fake,  # type: ignore[arg-type]
        )

        result = search.retrieve("RTO")

        assert len(result.evidence) == 1
        assert result.evidence[0].score == 0.95

    def test_ranks_by_score_descending(self) -> None:
        expander = QueryExpander()
        expanded = expander.expand("MFA")
        responses = {
            expanded.all_queries[0]: [evidence("gs://c/low.txt", 0.30)],
            expanded.all_queries[1]: [evidence("gs://c/high.txt", 0.90)],
        }
        fake = _FakeSearch(responses)
        search = ExpandingCorpusSearch(
            Department.SECURITY,
            expander=expander,
            search=fake,  # type: ignore[arg-type]
        )

        scores = [e.score for e in search.retrieve("MFA").evidence]

        assert scores == sorted(scores, reverse=True)

    def test_records_which_variant_found_each_document(self) -> None:
        """Needed for the trace, and for debugging recall regressions."""
        expander = QueryExpander()
        expanded = expander.expand("SBOM")
        winning = expanded.all_queries[1]
        fake = _FakeSearch({winning: [evidence("gs://c/tpl.txt", 0.9)]})
        search = ExpandingCorpusSearch(
            Department.SECURITY,
            expander=expander,
            search=fake,  # type: ignore[arg-type]
        )

        result = search.retrieve("SBOM")

        assert result.matched_by["gs://c/tpl.txt"] == winning

    def test_respects_top_k(self) -> None:
        expander = QueryExpander()
        expanded = expander.expand("MFA")
        fake = _FakeSearch(
            {
                expanded.all_queries[0]: [
                    evidence(f"gs://c/{i}.txt", 0.9 - i / 100) for i in range(9)
                ]
            }
        )
        search = ExpandingCorpusSearch(
            Department.SECURITY,
            expander=expander,
            search=fake,  # type: ignore[arg-type]
        )

        assert len(search.retrieve("MFA", top_k=3).evidence) == 3

    def test_department_binding_is_preserved(self) -> None:
        """Expansion must not weaken the access boundary."""
        fake = _FakeSearch({})
        search = ExpandingCorpusSearch(Department.LEGAL, search=fake)  # type: ignore[arg-type]

        assert search.department is Department.LEGAL
