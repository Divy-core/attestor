"""Turning Gmail's `messages.get` JSON into something the fleet can reason about.

Gmail's message shape is a recursive MIME tree with base64url bodies and headers as a list
of `{name, value}` pairs. None of that is a useful vocabulary for "a customer sent us a
questionnaire", so it is flattened exactly once, here, and everything downstream sees a
frozen record.

**The body is data, never instruction.** An inbound email is the least trusted input this
system has: anyone can send one. `InboundMessage.body_text` is passed to the classifier as
content to be *described*, is screened by Model Armor on the same surface as questionnaire
cells, and nothing in it is ever executed, followed, or treated as configuration. The
attachment path is stricter still -- the filename is sanitised before it is used as a GCS
object name, because a filename is attacker-controlled and object names are a path.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: What we are willing to treat as a questionnaire. Everything else on the message is
#: recorded and ignored -- a signature image must not become an intake document.
QUESTIONNAIRE_TYPES: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/csv",
    }
)

QUESTIONNAIRE_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".xls", ".pdf", ".docx", ".csv"})

#: Filenames arrive from outside. Anything not on this whitelist is replaced, so a
#: filename can never climb out of its prefix or smuggle a second path segment.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: How much of an email body is worth classifying. A quoted thread can run to hundreds of
#: kilobytes and the signal is always in the first screen.
MAX_BODY_CHARS = 20_000


def safe_filename(raw: str, *, fallback: str = "attachment.bin") -> str:
    """Reduce an attacker-controlled filename to something usable as an object name."""
    name = _UNSAFE.sub("_", (raw or "").strip().replace("\\", "/").rsplit("/", 1)[-1])
    name = name.lstrip(".") or fallback
    return name[:120]


def decode_b64url(data: str) -> bytes:
    """Gmail encodes every body and attachment as base64url, sometimes unpadded."""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return b""


@dataclass(frozen=True)
class Attachment:
    """One part of an inbound message that might be a questionnaire."""

    filename: str
    mime_type: str
    size_bytes: int
    #: Gmail's handle for fetching the bytes. Absent on very small inline parts, whose
    #: data arrives in the part itself.
    attachment_id: str | None = None
    inline_data: bytes | None = None

    @property
    def looks_like_a_questionnaire(self) -> bool:
        extension = self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""
        return self.mime_type in QUESTIONNAIRE_TYPES or f".{extension}" in QUESTIONNAIRE_EXTENSIONS


@dataclass(frozen=True)
class InboundMessage:
    """One email, flattened."""

    message_id: str
    thread_id: str
    history_id: str
    sender: str
    sender_domain: str
    to: str
    subject: str
    body_text: str
    received_at: datetime
    label_ids: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    #: RFC 2822 `Message-ID`, needed to thread a reply correctly. Gmail's own `threadId`
    #: puts the reply in the right conversation for *us*; `In-Reply-To` is what puts it in
    #: the right place in the customer's client.
    rfc822_message_id: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def questionnaires(self) -> tuple[Attachment, ...]:
        return tuple(a for a in self.attachments if a.looks_like_a_questionnaire)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers") or []
    }


def _address(raw: str) -> str:
    """Pull the bare address out of `Display Name <a@b.com>`."""
    if "<" in raw and ">" in raw:
        return raw[raw.rindex("<") + 1 : raw.rindex(">")].strip().lower()
    return raw.strip().lower()


def _walk(part: dict[str, Any], text: list[str], attachments: list[Attachment]) -> None:
    """Depth-first over the MIME tree, collecting readable text and attachments."""
    mime = str(part.get("mimeType") or "")
    body = part.get("body") or {}
    filename = str(part.get("filename") or "")

    if filename:
        attachments.append(
            Attachment(
                filename=safe_filename(filename),
                mime_type=mime,
                size_bytes=int(body.get("size") or 0),
                attachment_id=body.get("attachmentId"),
                inline_data=decode_b64url(body["data"]) if body.get("data") else None,
            )
        )
    elif mime == "text/plain" and body.get("data"):
        text.append(decode_b64url(str(body["data"])).decode("utf-8", "replace"))
    elif mime == "text/html" and body.get("data") and not text:
        # Only when there is no plain part. Tags stripped rather than rendered: this text
        # is classifier input, and an HTML renderer on untrusted input is a liability we
        # have no reason to take on.
        html = decode_b64url(str(body["data"])).decode("utf-8", "replace")
        text.append(re.sub(r"<[^>]+>", " ", html))

    for child in part.get("parts") or []:
        _walk(child, text, attachments)


def parse_message(raw: dict[str, Any]) -> InboundMessage:
    """Flatten one `users.messages.get(format="full")` response."""
    payload = raw.get("payload") or {}
    headers = _headers(payload)
    text: list[str] = []
    attachments: list[Attachment] = []
    _walk(payload, text, attachments)

    body = "\n".join(t.strip() for t in text if t.strip())[:MAX_BODY_CHARS]
    sender = _address(headers.get("from", ""))
    epoch_ms = int(raw.get("internalDate") or 0)

    return InboundMessage(
        message_id=str(raw.get("id") or ""),
        thread_id=str(raw.get("threadId") or ""),
        history_id=str(raw.get("historyId") or ""),
        sender=sender,
        sender_domain=sender.rsplit("@", 1)[-1] if "@" in sender else "",
        to=_address(headers.get("to", "")),
        subject=headers.get("subject", "(no subject)"),
        body_text=body,
        received_at=datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
        if epoch_ms
        else datetime.now(UTC),
        label_ids=tuple(str(x) for x in raw.get("labelIds") or []),
        attachments=tuple(attachments),
        rfc822_message_id=headers.get("message-id", ""),
        headers=headers,
    )
