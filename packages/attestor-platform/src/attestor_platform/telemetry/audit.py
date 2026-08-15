"""Two observability planes, deliberately kept separate.

* **Cloud Trace / OTel** -- *engineering* observability: latency, token cost, tool
  spans. Short retention, read by us while building.
* **Firestore `audit_events`** -- *compliance* observability: immutable, queryable,
  exportable. Read by an auditor asking "why did we answer yes to Q112?" in six months.

Different consumers, different retention, different schemas. Most entrants conflate
them; the distinction is worth stating in the write-up.

The audit writer is **non-fatal by contract**. A failed audit write logs loudly and
never blocks a run.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from attestor_platform.firestore.repositories import ArmorEventRepository, AuditEventRepository

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("attestor")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Open an OTel span with correlation attributes attached.

    Attributes are set individually rather than passed wholesale so a None value never
    turns into the string "None" in the trace UI.
    """
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current


def current_trace_id() -> str | None:
    """The active trace id as hex, for correlating an audit row with Cloud Trace."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


class AuditWriter:
    """Append-only compliance log, safe to call from any hot path."""

    def __init__(self, repository: AuditEventRepository | None = None) -> None:
        self._repo = repository if repository is not None else AuditEventRepository()

    def write(
        self,
        *,
        kind: str,
        review_id: str,
        run_id: str,
        round_id: str | None = None,
        question_id: str | None = None,
        actor: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str | None:
        """Append one audit event. Never raises.

        Returns the event id, or None when the write failed -- callers ignore the
        return value on hot paths, which is the point.
        """
        event: dict[str, Any] = {
            "kind": kind,
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": question_id,
            "actor": actor,
            "trace_id": current_trace_id(),
            "occurred_at": datetime.now(UTC).isoformat(),
            "detail": detail or {},
        }
        return self._repo.append_safe(event)


class ArmorEventWriter:
    """Append-only record of every Model Armor verdict, including allows."""

    def __init__(self, repository: ArmorEventRepository | None = None) -> None:
        self._repo = repository if repository is not None else ArmorEventRepository()

    def write(
        self,
        *,
        review_id: str,
        run_id: str,
        surface: str,
        decision: str,
        matched_filters: list[str] | None = None,
        question_id: str | None = None,
        chunk_index: int | None = None,
        excerpt: str | None = None,
    ) -> str | None:
        """Append one armor event. Never raises.

        `excerpt` is expected to be already truncated by the caller -- the full
        payload of a blocked document has no business in the audit log.
        """
        event: dict[str, Any] = {
            "review_id": review_id,
            "run_id": run_id,
            "question_id": question_id,
            "surface": surface,
            "decision": decision,
            "matched_filters": matched_filters or [],
            "chunk_index": chunk_index,
            "excerpt": excerpt,
            "trace_id": current_trace_id(),
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        return self._repo.append_safe(event)
