"""Section splitting: the unit that decides what a citation actually quotes."""

from __future__ import annotations

from attestor_platform.search.sections import (
    MAX_SECTION_CHARS,
    MIN_SECTION_CHARS,
    split_sections,
)

DOCUMENT = """\
# Backup and Restore Procedure

**Document ID:** KD-ENG-004 | **Version:** 3.1 | **Owner:** Dana Whitfield

## 1. Backup schedule

Full snapshots are taken daily at 02:00 UTC. Transaction logs ship continuously to a
second region with a five-minute lag, which is what makes the fifteen-minute recovery
point objective achievable rather than aspirational.

## 2. Encryption and location

All backups are encrypted with AES-256 using AWS KMS. The daily long-term copy is written
to a separate account under object lock.

## 3. Recovery objectives

The committed Recovery Time Objective is four hours and the Recovery Point Objective is
fifteen minutes. A full restore rehearsal was performed on 2026-02-18 and completed in
two hours and forty-one minutes.
"""


class TestSplitting:
    def test_splits_on_headings(self) -> None:
        sections = split_sections(DOCUMENT)
        headings = [s.heading for s in sections]
        assert "1. Backup schedule" in headings
        assert "3. Recovery objectives" in headings

    def test_the_answer_lands_in_one_section(self) -> None:
        """The whole point: 'how long does a restore take' must not read the backup
        encryption paragraph."""
        sections = split_sections(DOCUMENT)
        recovery = next(s for s in sections if s.heading == "3. Recovery objectives")
        assert "four hours" in recovery.text
        assert "AES-256" not in recovery.text

    def test_heading_text_rides_along_with_the_body(self) -> None:
        """'Recovery objectives' is often the most retrievable phrase in the section."""
        sections = split_sections(DOCUMENT)
        recovery = next(s for s in sections if s.heading == "3. Recovery objectives")
        assert recovery.text.startswith("3. Recovery objectives")

    def test_stub_sections_merge_forward(self) -> None:
        """A bare heading with two words under it is not citable evidence on its own."""
        document = "# Title\n\nx\n\n## Real section\n\n" + ("word " * 60)
        sections = split_sections(document)
        assert all(len(s.text) >= MIN_SECTION_CHARS for s in sections)

    def test_overlong_sections_are_chunked_on_paragraphs(self) -> None:
        body = "\n\n".join("paragraph " * 40 for _ in range(6))
        sections = split_sections(f"## Long\n\n{body}")
        assert len(sections) > 1
        assert all(len(s.text) <= MAX_SECTION_CHARS * 1.5 for s in sections)

    def test_a_document_without_headings_still_yields_a_section(self) -> None:
        sections = split_sections("Just prose, no structure at all, but still citable.")
        assert len(sections) == 1
        assert sections[0].heading == ""

    def test_empty_document_yields_nothing(self) -> None:
        assert split_sections("") == []
