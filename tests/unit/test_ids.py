"""Stable question identity.

The load-bearing test in this file is `test_reworded_and_renumbered_variants_match`.
Round 2 arrives as a different document with questions reordered, renumbered, and
partially reworded; if IDs are not stable across that, round-1-to-round-2 matching is
impossible and the consistency demo does not exist.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from attestor_core.domain.ids import make_dedup_key, make_question_id, normalize_question_text

BASE = "Do you encrypt customer data at rest?"


class TestNormalization:
    def test_lowercases(self) -> None:
        assert normalize_question_text("DO YOU ENCRYPT?") == normalize_question_text(
            "do you encrypt?"
        )

    def test_collapses_whitespace(self) -> None:
        assert normalize_question_text("do   you\t\nencrypt") == "do you encrypt"

    def test_strips_punctuation(self) -> None:
        assert normalize_question_text("do you encrypt?!") == "do you encrypt"

    @pytest.mark.parametrize(
        "prefix",
        ["1. ", "1) ", "Q1. ", "Q-1 ", "(3) ", "iv. ", "CC6.1 ", "A.9.2.3 ", "12.4.1) "],
    )
    def test_strips_leading_numbering(self, prefix: str) -> None:
        assert normalize_question_text(f"{prefix}{BASE}") == normalize_question_text(BASE)

    def test_strips_stacked_numbering(self) -> None:
        assert normalize_question_text(f"Q1. 1.2 {BASE}") == normalize_question_text(BASE)

    def test_is_idempotent(self) -> None:
        once = normalize_question_text(f"  Q7)  {BASE}  ")
        assert normalize_question_text(once) == once

    def test_nfkc_folds_unicode_lookalikes(self) -> None:
        # Curly apostrophe and non-breaking space, as produced by Word -> Excel.
        messy = "Do you encrypt customer data at rest?"  # noqa: RUF001 - the ambiguous chars ARE the test
        assert normalize_question_text(messy) == normalize_question_text(BASE)


class TestQuestionId:
    def test_is_sixteen_hex_chars(self) -> None:
        qid = make_question_id(BASE)
        assert len(qid) == 16
        assert all(c in "0123456789abcdef" for c in qid)

    def test_is_deterministic(self) -> None:
        assert make_question_id(BASE) == make_question_id(BASE)

    def test_different_questions_differ(self) -> None:
        assert make_question_id(BASE) != make_question_id("Do you encrypt data in transit?")

    def test_reworded_and_renumbered_variants_match(self) -> None:
        """The whole point: round 2 must match round 1.

        Same question, as it plausibly appears across two rounds of a real review --
        renumbered, respaced, recapitalised, repunctuated.
        """
        round_one = "12. Do you encrypt customer data at rest?"
        round_two = "Q7)  DO  YOU  ENCRYPT   CUSTOMER DATA AT REST"

        assert make_question_id(round_one) == make_question_id(round_two)

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="normalises to empty"):
            make_question_id("   ")

    def test_numbering_only_text_raises(self) -> None:
        with pytest.raises(ValueError, match="normalises to empty"):
            make_question_id("1. ")


class TestDedupKey:
    def test_is_deterministic(self) -> None:
        assert make_dedup_key("rev1", "r1", "q1", "draft") == make_dedup_key(
            "rev1", "r1", "q1", "draft"
        )

    def test_differs_on_any_part(self) -> None:
        base = make_dedup_key("rev1", "r1", "q1", "draft")
        assert base != make_dedup_key("rev1", "r1", "q2", "draft")
        assert base != make_dedup_key("rev1", "r2", "q1", "draft")
        assert base != make_dedup_key("rev1", "r1", "q1", "assemble")

    def test_separator_prevents_field_smearing(self) -> None:
        """("ab","c") and ("a","bc") must not collide."""
        assert make_dedup_key("ab", "c") != make_dedup_key("a", "bc")


class TestRoundTwoMatching:
    """The round-1 to round-2 rewordings actually used in seed/questionnaires/followup.

    These are the exact strings in the seeded round-2 questionnaire. If any stops
    matching its round-1 form, the consistency demo silently stops working -- the
    round-2 answer would be treated as a brand new question with no prior commitment.
    """

    ROUND_PAIRS: ClassVar[list[tuple[str, str]]] = [
        ("Do you encrypt customer data at rest?", "12. Do you encrypt customer data at rest?"),
        (
            "Is multi-factor authentication enforced for all personnel with production access?",
            "Q7) IS MULTI-FACTOR AUTHENTICATION ENFORCED FOR ALL PERSONNEL WITH PRODUCTION ACCESS",
        ),
        ("What is your Recovery Time Objective?", "3.1 What is your Recovery Time Objective?"),
        (
            "Will you execute a Data Processing Agreement?",
            "(a) Will you execute a Data Processing Agreement?",
        ),
        (
            "Have you experienced a customer data breach in the last 3 years?",
            "iv. Have you experienced a customer data breach in the last 3 years?",
        ),
    ]

    @pytest.mark.parametrize(("round_one", "round_two"), ROUND_PAIRS)
    def test_seeded_rewordings_match(self, round_one: str, round_two: str) -> None:
        assert make_question_id(round_one) == make_question_id(round_two)


class TestAlphabeticListMarkers:
    """`(a)`, `b.`, `c)` are as common as numbers in real questionnaires."""

    @pytest.mark.parametrize("prefix", ["(a) ", "a. ", "b) ", "(c) ", "d - "])
    def test_alphabetic_markers_are_stripped(self, prefix: str) -> None:
        assert make_question_id(f"{prefix}{BASE}") == make_question_id(BASE)

    def test_distinct_questions_remain_distinct(self) -> None:
        """Stripping markers must not collapse genuinely different questions."""
        at_rest = make_question_id("Do you encrypt customer data at rest?")
        in_transit = make_question_id("Do you encrypt customer data in transit?")
        assert at_rest != in_transit

    def test_a_word_boundary_is_not_eaten(self) -> None:
        """`Do you...` must not lose its `D` -- the marker needs a separator after it."""
        assert normalize_question_text("Do you encrypt?").startswith("do you")
