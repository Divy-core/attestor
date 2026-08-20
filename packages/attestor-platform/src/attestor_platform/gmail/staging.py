"""Getting an email's questions into the shape the existing pipeline already accepts.

The whole design of the inbound path rests on one decision: **nothing downstream of intake
learns that email exists.** `intake_document` takes a GCS URI and parses a questionnaire;
so an inbound email is turned into a GCS URI here, and the fleet runs exactly as it does
for a browser upload. That is what makes B1 a transport change rather than a second
pipeline.

Two shapes arrive, and both are real:

* **An attachment.** The common first contact — a CAIQ workbook on an email to `trust@`.
  Staged verbatim; the customer's own file is what the export has to hand back, so
  rewriting it here would break the deliverable.
* **Questions in the body.** The common *follow-up* — "Thanks. Three more: do you...".
  There is no attachment at all. A round that could only start from a file would refuse
  the most frequent real follow-up in the domain, so the prose questions are written into
  a small workbook and staged the same way.

The synthesised workbook is marked as synthesised, in the sheet, in the filename, and in
the cover column. When that round is exported, the customer gets back a file they never
sent, and it has to be obvious that Attestor built it from their words rather than
pretending it was their template.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

from attestor_platform.gmail.message import Attachment, InboundMessage, safe_filename
from attestor_platform.storage import StorageClient

logger = logging.getLogger(__name__)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Everything from the mailbox lands under this prefix. One place to look, one place to
#: apply a lifecycle rule, and visibly distinct from `questionnaires/` written by the
#: browser upload path.
PREFIX = "inbound"


def _object_name(message: InboundMessage, filename: str) -> str:
    # Keyed by Gmail's message id, so a redelivered notification stages to the *same*
    # object rather than a second copy. Idempotent by construction, which matters here
    # because the dedup key protects the work and not the bytes.
    return f"{PREFIX}/{message.message_id}/{safe_filename(filename)}"


def stage_attachment(
    message: InboundMessage,
    attachment: Attachment,
    payload: bytes,
    storage: StorageClient | None = None,
) -> str:
    """Put one attachment in GCS and return its URI."""
    client = storage if storage is not None else StorageClient()
    uri = client.upload_bytes(
        _object_name(message, attachment.filename),
        payload,
        content_type=attachment.mime_type or "application/octet-stream",
    )
    logger.info("staged %s (%d bytes) -> %s", attachment.filename, len(payload), uri)
    return uri


def build_questionnaire(questions: tuple[str, ...], *, source: str) -> bytes:
    """Write prose questions into a minimal questionnaire workbook.

    The header is `Question`, which is one of the names `agents.intake._QUESTION_HEADERS`
    recognises — this file has to be parseable by the same parser that reads a customer's
    CAIQ export, not by a special case.
    """
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - openpyxl always creates one
        raise RuntimeError("openpyxl returned a workbook with no active sheet")
    sheet.title = "Follow-up questions"
    sheet.append(["Question", "Source"])
    for question in questions:
        sheet.append([question, source])
    sheet.column_dimensions["A"].width = 100
    sheet.column_dimensions["B"].width = 44

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def stage_body_questions(
    message: InboundMessage,
    questions: tuple[str, ...],
    storage: StorageClient | None = None,
) -> str:
    """Stage questions asked in the email body as a workbook, and return its URI."""
    if not questions:
        raise ValueError("stage_body_questions called with no questions")
    client = storage if storage is not None else StorageClient()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    source = f"Extracted from an email from {message.sender} on {stamp}"
    filename = f"attestor-extracted-{stamp}-{message.message_id[:10]}.xlsx"
    uri = client.upload_bytes(
        _object_name(message, filename),
        build_questionnaire(questions, source=source),
        content_type=XLSX_TYPE,
    )
    logger.info("staged %d body question(s) -> %s", len(questions), uri)
    return uri
