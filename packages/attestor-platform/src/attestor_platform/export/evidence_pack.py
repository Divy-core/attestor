"""The PDF a security reviewer actually reads.

The workbook answers the question "what did you say?". This answers "why should I believe
it?" — every answer printed with the passages it stands on, the section each came from, and
the relevance score retrieval assigned. That is the artefact that closes a review, and it
is the one place in the system where the provenance chain is rendered for someone who will
never open the console.

## Composition, not drawing

reportlab has two levels: a canvas you draw on at coordinates, and Platypus, which flows
paragraphs and tables across pages. This uses Platypus, because 312 answers with variable-
length citations cannot be laid out at fixed coordinates without either clipping text or
reimplementing pagination. The one canvas-level thing here is the page footer, which needs
the page number and therefore has to be drawn per page.

## No colour

The workbook uses one fill to mark what a human has not signed. This document is greyscale
by design: it gets printed, faxed inside banks, and attached to procurement tickets that
strip colour. Release state is stated in words in every single block, so nothing depends on
a reader seeing a tint.
"""

from __future__ import annotations

import io
import logging
from html import escape
from typing import TYPE_CHECKING, Any

from attestor_platform.export.model import RELEASE_RULE, ExportBundle, ExportRow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from reportlab.platypus import Flowable

logger = logging.getLogger(__name__)

#: How much of a cited passage is printed. The full passage can be hundreds of lines of
#: policy text; what a reviewer needs is enough to see that it says what we claim it says,
#: with the document and section named so they can pull the original.
SNIPPET_CHARS = 700

#: Answers per document. A 312-question pack runs to a few hundred pages, which is normal
#: for a completed vendor review and is what the customer asked for.
_PAGE_MARGIN = 42


def _styles() -> dict[str, Any]:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AttestorTitle", parent=base["Title"], fontSize=20, leading=24, alignment=TA_LEFT
        ),
        "meta": ParagraphStyle(
            "AttestorMeta",
            parent=base["BodyText"],
            fontSize=8.5,
            leading=12,
            fontName="Courier",
            spaceAfter=2,
        ),
        "note": ParagraphStyle(
            "AttestorNote", parent=base["BodyText"], fontSize=9, leading=13, spaceAfter=8
        ),
        "question": ParagraphStyle(
            "AttestorQuestion",
            parent=base["BodyText"],
            fontSize=10.5,
            leading=14,
            fontName="Helvetica-Bold",
            spaceBefore=14,
            spaceAfter=3,
        ),
        "answer": ParagraphStyle(
            "AttestorAnswer", parent=base["BodyText"], fontSize=9.5, leading=13, spaceAfter=4
        ),
        "label": ParagraphStyle(
            "AttestorLabel",
            parent=base["BodyText"],
            fontSize=8,
            leading=11,
            fontName="Helvetica-Oblique",
            spaceAfter=4,
        ),
        "citation": ParagraphStyle(
            "AttestorCitation",
            parent=base["BodyText"],
            fontSize=8.5,
            leading=11.5,
            leftIndent=12,
            spaceAfter=5,
        ),
        "passage": ParagraphStyle(
            "AttestorPassage",
            parent=base["BodyText"],
            fontSize=8,
            leading=11,
            leftIndent=12,
            textColor="#333333",
            spaceAfter=6,
        ),
        "section": ParagraphStyle(
            "AttestorSection", parent=base["Heading2"], fontSize=13, leading=16, spaceBefore=18
        ),
    }


def _footer(canvas: Any, document: Any, origin: str) -> None:
    """Page number and origin on every page, drawn rather than flowed.

    The origin is the deployed service that produced the file. A reviewer holding a printed
    page should be able to see where it came from without the covering email.
    """
    canvas.saveState()
    canvas.setFont("Courier", 7)
    canvas.setFillGray(0.45)
    canvas.drawString(_PAGE_MARGIN, 24, origin or "attestor")
    canvas.drawRightString(document.pagesize[0] - _PAGE_MARGIN, 24, f"page {document.page}")
    canvas.restoreState()


def _cover(bundle: ExportBundle, styles: dict[str, Any]) -> list[Flowable]:
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    review = bundle.review
    facts = [
        ("Customer", review.customer),
        ("Review", review.review_id),
        ("Round", f"{bundle.round_.ordinal}  ({bundle.round_.round_id})"),
        ("Framework", review.framework.value),
        ("Data residency", review.residency.value),
        ("Review state", review.state.value),
        ("Questions", str(len(bundle.rows))),
        ("Answered", str(bundle.answered)),
        ("Answers with citations", f"{bundle.cited} of {bundle.answered}"),
        ("Sendable", str(bundle.sendable)),
        ("Approved by a human", str(bundle.human_approved)),
        ("Generated", bundle.generated_at.isoformat(timespec="seconds")),
    ]
    if bundle.origin:
        facts.append(("Produced by", bundle.origin))

    table = Table(
        [
            [Paragraph(f"<b>{escape(k)}</b>", styles["note"]), Paragraph(escape(v), styles["meta"])]
            for k, v in facts
        ],
        colWidths=[130, 340],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, "#DDDDDD"),
            ]
        )
    )

    story: list[Flowable] = [
        Paragraph("Evidence pack", styles["title"]),
        Spacer(1, 4),
        Paragraph(
            "Every answer in this round, with the passages it is drawn from.",
            styles["note"],
        ),
        Spacer(1, 10),
        table,
        Spacer(1, 14),
        Paragraph("<b>Release rule</b>", styles["note"]),
        Paragraph(escape(RELEASE_RULE), styles["note"]),
        Spacer(1, 6),
    ]

    tally = [
        [Paragraph(escape(str(state)), styles["note"]), Paragraph(str(count), styles["meta"])]
        for state, count in sorted(bundle.counts.items(), key=lambda item: -item[1])
    ]
    if tally:
        counts = Table(tally, colWidths=[300, 60], hAlign="LEFT")
        counts.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(counts)
    return story


def _block(row: ExportRow, index: int, styles: dict[str, Any]) -> list[Flowable]:
    """One question, its answer, its release state, and its provenance."""
    from reportlab.platypus import Paragraph

    question = row.question
    reference = question.source_ref
    where = ""
    if reference and reference.row is not None:
        where = f"{reference.sheet or 'sheet'} row {reference.row}"
    hint = f" · {question.framework_hint}" if question.framework_hint else ""

    flowables: list[Flowable] = [
        Paragraph(f"{index}. {escape(question.text)}", styles["question"]),
        Paragraph(
            escape(f"{question.department.value}{hint}" + (f" · {where}" if where else "")),
            styles["label"],
        ),
    ]

    if row.answer is None:
        flowables.append(
            Paragraph(
                "<i>No answer was produced for this question in this round.</i>",
                styles["answer"],
            )
        )
    else:
        flowables.append(Paragraph(escape(row.text) or "<i>(no text)</i>", styles["answer"]))

    # Release state is printed in words for every single answer, cleared or not. The
    # workbook can rely on a fill; a printed page cannot.
    flowables.append(
        Paragraph(
            escape(
                f"Release status: {row.release}"
                + (
                    f" · confidence {row.answer.confidence.value}"
                    f" · drafted by {row.answer.authored_by}"
                    if row.answer
                    else ""
                )
            ),
            styles["label"],
        )
    )

    if row.answer is None or not row.answer.citations:
        flowables.append(
            Paragraph(
                "<b>Evidence:</b> none. No retrieved passage supports this answer, which is "
                "why it must not be sent to a customer as one.",
                styles["citation"],
            )
        )
        return flowables

    flowables.append(
        Paragraph(f"<b>Evidence ({len(row.answer.citations)})</b>", styles["citation"])
    )
    for position, citation in enumerate(row.answer.citations, start=1):
        section = citation.section or "no section recorded"
        flowables.append(
            Paragraph(
                escape(
                    f"[{position}] {citation.document_title} — {section} — "
                    f"relevance {citation.retrieval_score:.2f}"
                ),
                styles["citation"],
            )
        )
        snippet = citation.snippet.strip()
        if len(snippet) > SNIPPET_CHARS:
            snippet = f"{snippet[:SNIPPET_CHARS].rstrip()}…"
        flowables.append(Paragraph(escape(snippet).replace("\n", "<br/>"), styles["passage"]))
        flowables.append(Paragraph(escape(citation.document_uri), styles["meta"]))
    return flowables


def build_evidence_pack(bundle: ExportBundle) -> bytes:
    """Render the bundle as a PDF.

    Pure: no network, no Firestore, no credentials. Everything it prints came from the
    bundle, which is why this is testable and why the numbers on the cover cannot disagree
    with the numbers in the workbook.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_PAGE_MARGIN,
        rightMargin=_PAGE_MARGIN,
        topMargin=_PAGE_MARGIN,
        bottomMargin=_PAGE_MARGIN + 12,
        title=f"Attestor evidence pack — {bundle.review.customer}",
        author="Attestor",
        subject=f"{bundle.round_.round_id} · {len(bundle.rows)} questions",
    )

    story: list[Flowable] = [*_cover(bundle, styles), PageBreak()]
    story.append(Paragraph("Answers and evidence", styles["section"]))
    for index, row in enumerate(bundle.rows, start=1):
        story.extend(_block(row, index, styles))

    origin = bundle.origin
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _footer(canvas, doc, origin),
        onLaterPages=lambda canvas, doc: _footer(canvas, doc, origin),
    )
    logger.info("evidence pack: %d blocks, %d bytes", len(bundle.rows), buffer.tell())
    return buffer.getvalue()
