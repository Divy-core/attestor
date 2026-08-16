"""The static prompt prefix must be byte-identical across turns.

Gemini context caching only pays out when the static prefix is byte-identical. Anything
that varies breaks the cache *silently*: no error, just higher cost and worse latency,
which is the worst kind of regression because nothing tells you it happened.

So this asserts what a human reviewer cannot reliably eyeball — that assembling the same
agent's prompt with completely different dynamic inputs leaves the prefix untouched.
"""

from __future__ import annotations

import pytest

from attestor_core.domain import Department, Evidence
from attestor_fleet.prompts import (
    CONSISTENCY_STATIC,
    TRIAGE_STATIC,
    consistency_prompt,
    drafting_prompt,
    drafting_static,
    is_hedged,
    split_prompt,
    triage_prompt,
)
from attestor_fleet.prompts.base import render_list, render_mapping


def evidence(title: str, content: str, score: float = 0.9) -> Evidence:
    return Evidence(
        document_uri=f"gs://c/{title}.txt",
        document_title=title,
        content=content,
        score=score,
        department=Department.SECURITY,
    )


class TestTriagePrefixStability:
    def test_prefix_identical_across_different_batches(self) -> None:
        first = triage_prompt([(0, "Do you encrypt data at rest?")])
        second = triage_prompt(
            [(0, "What is your RTO?"), (1, "Will you sign a DPA?"), (2, "Describe tenancy.")]
        )

        assert split_prompt(first)[0] == split_prompt(second)[0]

    def test_prefix_is_the_declared_constant(self) -> None:
        prefix, _ = split_prompt(triage_prompt([(0, "anything")]))
        assert prefix == TRIAGE_STATIC

    def test_dynamic_half_actually_differs(self) -> None:
        """Guards against a false pass: if both halves were equal the test is vacuous."""
        first = triage_prompt([(0, "Do you encrypt data at rest?")])
        second = triage_prompt([(0, "What is your RTO?")])

        assert split_prompt(first)[1] != split_prompt(second)[1]


class TestDraftingPrefixStability:
    @pytest.mark.parametrize(
        "department",
        [Department.SECURITY, Department.LEGAL, Department.ENGINEERING],
    )
    def test_prefix_identical_across_questions_and_evidence(self, department: Department) -> None:
        first = drafting_prompt(
            department,
            "Do you encrypt customer data at rest?",
            [evidence("encryption-standard", "AES-256-GCM at rest.")],
        )
        second = drafting_prompt(
            department,
            "How long are elevated access grants valid?",
            [
                evidence("access-control-standard", "Eight hours.", 0.71),
                evidence("information-security-policy", "Least privilege.", 0.62),
            ],
        )

        assert split_prompt(first)[0] == split_prompt(second)[0]
        assert split_prompt(first)[0] == drafting_static(department)

    def test_each_department_has_its_own_stable_prefix(self) -> None:
        """Separate prefixes mean separate cache entries, which is intended."""
        prefixes = {
            department: drafting_static(department)
            for department in (Department.SECURITY, Department.LEGAL, Department.ENGINEERING)
        }
        assert len(set(prefixes.values())) == 3

    def test_prefix_survives_empty_evidence(self) -> None:
        with_evidence = drafting_prompt(Department.SECURITY, "Q?", [evidence("doc", "content")])
        without = drafting_prompt(Department.SECURITY, "Q?", [])

        assert split_prompt(with_evidence)[0] == split_prompt(without)[0]


class TestConsistencyPrefixStability:
    def test_prefix_identical_across_commitments(self) -> None:
        first = consistency_prompt("We offer self-hosted deployment.", ["No on-prem offered."])
        second = consistency_prompt(
            "We encrypt at rest.", ["AES-256 at rest.", "TLS 1.3 in transit."]
        )

        assert split_prompt(first)[0] == split_prompt(second)[0] == CONSISTENCY_STATIC


class TestNoUnstableContent:
    """The prefixes must contain nothing that varies between processes."""

    ALL_PREFIXES = (
        TRIAGE_STATIC,
        CONSISTENCY_STATIC,
        drafting_static(Department.SECURITY),
        drafting_static(Department.LEGAL),
        drafting_static(Department.ENGINEERING),
    )

    @pytest.mark.parametrize("prefix", ALL_PREFIXES)
    def test_contains_no_digits_that_look_like_a_year(self, prefix: str) -> None:
        """A date in the prefix would change it every year, and nobody would notice."""
        assert "2026" not in prefix
        assert "2025" not in prefix

    @pytest.mark.parametrize("prefix", ALL_PREFIXES)
    def test_contains_no_object_repr(self, prefix: str) -> None:
        """`<object at 0x...>` is the classic accidental instability."""
        assert " at 0x" not in prefix
        assert "object at" not in prefix

    def test_repeated_assembly_is_byte_identical(self) -> None:
        """Belt and braces: same inputs, two calls, identical bytes."""
        args = (Department.LEGAL, "Will you sign a DPA?", [evidence("dpa", "Yes.")])
        assert drafting_prompt(*args) == drafting_prompt(*args)


class TestOrderingHelpers:
    def test_render_list_sorts(self) -> None:
        """A list built from a set would come out differently between processes."""
        assert render_list(["zebra", "apple", "mango"]) == "- apple\n- mango\n- zebra"

    def test_render_list_is_stable_for_set_input(self) -> None:
        items = {"delta", "alpha", "charlie"}
        assert render_list(items) == render_list(items)
        assert render_list(items).splitlines()[0] == "- alpha"

    def test_render_mapping_sorts_by_key(self) -> None:
        rendered = render_mapping({"zulu": "1", "alpha": "2"})
        assert rendered.splitlines()[0] == "- alpha: 2"


class TestHedgeDetection:
    """Hedging is detected lexically -- never by asking a model how sure it is."""

    @pytest.mark.parametrize(
        "text",
        [
            "We may encrypt data at rest.",
            "Access is typically revoked within an hour.",
            "We believe the RTO is four hours.",
            "Backups should be retained for 35 days.",
            "To our knowledge no breach has occurred.",
        ],
    )
    def test_detects_hedging(self, text: str) -> None:
        assert is_hedged(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "All customer data at rest is encrypted with AES-256-GCM.",
            "The Recovery Time Objective is 4 hours.",
            "No. Kestrel does not offer on-premises deployment.",
        ],
    )
    def test_confident_answers_are_not_hedged(self, text: str) -> None:
        assert is_hedged(text) is False

    def test_is_deterministic(self) -> None:
        text = "We may typically retain backups."
        assert is_hedged(text) is is_hedged(text)
