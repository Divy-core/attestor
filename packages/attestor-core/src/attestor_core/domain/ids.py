"""Stable, content-derived identifiers.

The single most important function in this package is ``make_question_id``.

Round 2 of a vendor security review arrives weeks later as a *different document*:
questions reordered, renumbered, and partially reworded. A positional ID
("row 47 of sheet 1") makes round-1 to round-2 matching impossible, and that matching
is the mechanism behind the consistency demo -- the hardest and most valuable beat in
the build. So IDs are derived from normalised question content, not position.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: Length of the hex digest we keep. 16 hex chars = 64 bits. At questionnaire scale
#: (hundreds of questions, not billions) collision risk is negligible, and a short id
#: stays readable in a Firestore console, a trace attribute, and a log line.
_ID_LENGTH = 16

#: Leading enumeration to strip: "1.", "1)", "Q1", "Q-1", "A.1.2", "iv.", "(3)" etc.
_LEADING_NUMBERING = re.compile(
    r"""^\s*
    \(?                                  # optional opening paren
    (?:
        [Qq][\s\-.:#]*\d+                # Q1, Q-1, Q.1, Q #1
      | [A-Za-z]{1,3}[\s\-.]?\d+(?:\.\d+)*   # CC6.1, A.9.2.3, AC-2
      | \d+(?:\.\d+)*                    # 1, 1.2, 1.2.3
      | [ivxlcdm]+                       # roman numerals
    )
    \)?                                  # optional closing paren
    [\s.):\-\]]+                         # the separator that follows
    """,
    re.VERBOSE,
)

#: Everything that is not a letter, digit, or whitespace. Punctuation is noise for
#: identity purposes: "Do you encrypt data at rest?" and "Do you encrypt data at rest"
#: are the same question.
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

_WHITESPACE = re.compile(r"\s+")


def normalize_question_text(text: str) -> str:
    """Reduce a question to the form its identity is derived from.

    Applies, in order: Unicode NFKC normalisation, lowercasing, removal of leading
    enumeration, punctuation stripping, and whitespace collapsing.

    NFKC comes first and matters more than it looks: questionnaires are copied between
    Word, Excel, and PDF, so the same question routinely arrives with a non-breaking
    space, a curly apostrophe, or a full-width character. Without NFKC those produce
    different IDs for identical questions, which silently breaks round matching.

    Args:
        text: Raw question text as extracted from the source document.

    Returns:
        The normalised form. Deterministic and idempotent.
    """
    normalized = unicodedata.normalize("NFKC", text)
    # Non-breaking and zero-width characters survive NFKC; treat them as whitespace.
    normalized = normalized.replace(" ", " ").replace("​", " ")  # noqa: RUF001
    normalized = normalized.casefold()
    # Strip enumeration repeatedly: "Q1. 1.2 Do you..." carries two layers.
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = _LEADING_NUMBERING.sub("", normalized)
    normalized = _PUNCTUATION.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized)
    return normalized.strip()


def make_question_id(text: str) -> str:
    """Return a stable identifier derived from the question's content.

    The same question yields the same ID regardless of its numbering, surrounding
    whitespace, punctuation, or letter case -- which is what lets a round-2 document
    be matched against round 1.

    Args:
        text: Raw question text.

    Returns:
        A 16-character lowercase hex string.

    Raises:
        ValueError: If the text normalises to nothing. A question with no content
            cannot be given a content-derived identity, and silently hashing the
            empty string would give every blank row the same ID.
    """
    normalized = normalize_question_text(text)
    if not normalized:
        raise ValueError("cannot derive a question id from text that normalises to empty")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:_ID_LENGTH]


def make_dedup_key(*parts: str) -> str:
    """Build a deterministic dedup key from the parts that identify a unit of work.

    Deterministic is the whole point: Pub/Sub guarantees at-least-once delivery, so
    the dispatcher must be able to recognise a redelivered message as the same work.
    A random key would make every redelivery look like new work.

    Args:
        *parts: Identifying components, e.g. review id, round id, question id, kind.

    Returns:
        A 16-character lowercase hex string.
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_ID_LENGTH]
