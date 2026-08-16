"""Section-level reranking over the corpus documents themselves.

Discovery Engine returns one snippet per document, chosen by its own matcher, and that
snippet is frequently the wrong part of the right document. Measured directly: asked
*"How long does a restore from backup take?"*, retrieval returns
`backup-restore-procedure` — the correct document — with a snippet about **backup
encryption**. The drafter reads a passage that does not answer the question and correctly
replies `INSUFFICIENT_EVIDENCE`. The retrieval hit counts, the answer does not.

It also flattens the score. Cosine between a question and an arbitrary snippet of a
same-domain policy document separates the labelled-relevant document from the rest by
only **0.05** (0.653 vs 0.601 median, measured over the 63 labelled pairs). A signal that
weak cannot carry `compute_confidence`.

So retrieval is candidate generation and this is the rerank: the document's own text is
split on its heading structure, every section is scored against the question, and the
best-scoring section becomes both the citation snippet and the score. The corpus was
authored with real heading structure precisely so this would work.

Cheap by construction: sections are embedded once per run and cached by content hash in
the shared `RelevanceScorer`, so a document read by thirty questions costs one embedding.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from attestor_core.domain import Department
from attestor_platform.config import project_id

logger = logging.getLogger(__name__)

#: Split on any markdown heading. The corpus is staged as `text/plain` (Vertex AI Search
#: rejects `text/markdown`) but the `#` structure survives verbatim, which is exactly why
#: it was staged that way rather than stripped.
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

#: Sections longer than this are split. Long enough to hold a complete policy statement
#: with its numbers, short enough that one embedding still points at one idea.
MAX_SECTION_CHARS = 1400
#: Sections shorter than this are merged forward -- a bare heading is not evidence.
MIN_SECTION_CHARS = 120


@dataclass(frozen=True)
class Section:
    """One citable passage of one document."""

    heading: str
    text: str


def split_sections(document: str) -> list[Section]:
    """Split a document on its headings, merging stubs and splitting overlong bodies."""
    matches = list(_HEADING.finditer(document))
    if not matches:
        return [Section(heading="", text=chunk) for chunk in _chunk(document.strip())]

    raw: list[Section] = []
    preamble = document[: matches[0].start()].strip()
    if len(preamble) >= MIN_SECTION_CHARS:
        raw.append(Section(heading="", text=preamble))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        heading = match.group(1).strip()
        body = document[match.end() : end].strip()
        if not body:
            continue
        # The heading rides along in the text: "Recovery objectives" is often the most
        # retrievable phrase in the section, and dropping it loses that signal.
        for chunk in _chunk(f"{heading}\n{body}"):
            raw.append(Section(heading=heading, text=chunk))

    merged: list[Section] = []
    #: A stub at the very top has no previous section to join, so it is carried forward
    #: into the next one instead of being dropped -- a document's title block is often
    #: where the document ID and approval date live, and those are citable facts.
    carried = ""
    for section in raw:
        text = f"{carried}\n{section.text}".strip() if carried else section.text
        carried = ""
        if len(text) < MIN_SECTION_CHARS:
            if merged:
                previous = merged[-1]
                merged[-1] = Section(heading=previous.heading, text=f"{previous.text}\n{text}")
            else:
                carried = text
            continue
        merged.append(Section(heading=section.heading, text=text))
    if carried and merged:
        first = merged[0]
        merged[0] = Section(heading=first.heading, text=f"{carried}\n{first.text}")
    elif carried:
        merged.append(Section(heading="", text=carried))
    return merged


def _chunk(text: str) -> list[str]:
    """Split an overlong section on paragraph boundaries, never mid-sentence."""
    if len(text) <= MAX_SECTION_CHARS:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > MAX_SECTION_CHARS:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        chunks.append(current.strip())
    return chunks


class SectionIndex:
    """Loads and caches the section text of one department's corpus.

    Reads from the same GCS objects Vertex AI Search indexed, so a citation points at a
    passage that provably exists in the corpus rather than at something reconstructed.
    """

    def __init__(self, department: Department, project: str | None = None) -> None:
        self.department = department
        self.project = project or project_id()
        self.bucket_name = f"{self.project}-corpus"
        self._sections: dict[str, list[Section]] = {}
        self._lock = threading.Lock()
        self._client: Any | None = None

    def _bucket(self) -> Any:
        from google.cloud import storage  # type: ignore[attr-defined]

        if self._client is None:
            self._client = storage.Client(project=self.project)
        return self._client.bucket(self.bucket_name)

    def sections_for(self, document_uri: str) -> list[Section]:
        """Sections of one document, loaded on first use and cached thereafter.

        Returns an empty list when the document cannot be read -- an unreadable corpus
        object degrades to snippet scoring rather than failing the question.
        """
        with self._lock:
            cached = self._sections.get(document_uri)
        if cached is not None:
            return cached

        sections: list[Section] = []
        try:
            prefix = f"gs://{self.bucket_name}/"
            if not document_uri.startswith(prefix):
                raise ValueError(f"{document_uri} is not in {self.bucket_name}")
            blob = self._bucket().blob(document_uri[len(prefix) :])
            sections = split_sections(blob.download_as_text())
        except Exception as exc:
            logger.warning("could not load sections for %s: %s", document_uri, exc)

        with self._lock:
            self._sections[document_uri] = sections
        return sections
