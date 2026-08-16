"""Append-only audit trail for the fleet.

Non-fatal by contract: a failed audit write logs loudly and never blocks a run. Losing
one audit line is bad; failing a 312-question review because the audit collection had a
blip is worse, and the run is still reconstructable from Cloud Trace.

Every event carries `review_id`, `run_id`, and where applicable `question_id`, because
the Production Bar requires correlation IDs on every line and the cheapest way to
guarantee that is to make it structural.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    """What the fleet needs from an audit writer.

    A Protocol rather than a concrete class so the fleet does not depend on Firestore
    being reachable -- `adk web` runs locally, and Phase 4 swaps the sink for one that
    also emits SSE without touching agent code.
    """

    def write(
        self,
        *,
        kind: str,
        review_id: str,
        run_id: str,
        round_id: str | None = ...,
        question_id: str | None = ...,
        actor: str | None = ...,
        detail: dict[str, Any] | None = ...,
    ) -> str | None: ...


class NullAuditSink:
    """Records to memory only. Used by `adk web` and by tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

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
        event = {
            "kind": kind,
            "review_id": review_id,
            "run_id": run_id,
            "round_id": round_id,
            "question_id": question_id,
            "actor": actor,
            "detail": detail or {},
        }
        self.events.append(event)
        logger.debug("audit: %s", event)
        return str(len(self.events))

    def for_question(self, question_id: str) -> list[dict[str, Any]]:
        """Every event for one question, in order -- the reasoning chain."""
        return [e for e in self.events if e["question_id"] == question_id]


#: Event kinds. Named constants so a typo cannot silently create a new kind that no
#: query will ever find.
QUESTION_PARSED = "question_parsed"
QUESTION_TRIAGED = "question_triaged"
QUERY_EXPANDED = "query_expanded"
EVIDENCE_RETRIEVED = "evidence_retrieved"
ARMOR_SCREENED = "armor_screened"
ARMOR_BLOCKED = "armor_blocked"
TOOL_DENIED = "tool_denied"
ANSWER_DRAFTED = "answer_drafted"
CONSISTENCY_CHECKED = "consistency_checked"
ANSWER_ASSEMBLED = "answer_assembled"
HUMAN_REQUIRED = "human_required"
RUN_COMPLETED = "run_completed"
