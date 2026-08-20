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
#: Orchestrator judgement. These are audit-only kinds: `attestor_core.protocol` is FROZEN
#: at 14 SSE variants and this does not touch it. The audit trail is allowed to record
#: more than the wire streams, and "which pipeline was chosen, by what, and why" is
#: exactly the kind of decision an auditor asks about after the fact.
PLAN_SELECTED = "plan_selected"
RETRY_DECIDED = "retry_decided"

QUESTION_PARSED = "question_parsed"
QUESTION_TRIAGED = "question_triaged"
QUERY_EXPANDED = "query_expanded"
EVIDENCE_RETRIEVED = "evidence_retrieved"
ARMOR_SCREENED = "armor_screened"
ARMOR_BLOCKED = "armor_blocked"
TOOL_DENIED = "tool_denied"
ANSWER_DRAFTED = "answer_drafted"
#: A SEPARATE agent checked the answer against its own citations. Its own kind rather than
#: a field on `answer_drafted`, because it is a different actor making a different claim,
#: and folding it in would attribute the verdict to the agent that wrote the answer.
ANSWER_VERIFIED = "answer_verified"
CONSISTENCY_CHECKED = "consistency_checked"
ANSWER_ASSEMBLED = "answer_assembled"
HUMAN_REQUIRED = "human_required"
RUN_COMPLETED = "run_completed"


class FirestoreAuditSink:
    """Writes the audit trail where the UI and the auditor can read it.

    Phase 3 ran with `NullAuditSink` and reported from memory, which is fine for a
    harness that prints its own summary. Phase 4 runs the same stages across separate
    dispatcher instances and separate messages, so "in memory" means "in whichever
    instance happened to handle that stage" -- the trail has to be durable to exist at
    all.

    **Never raises.** A failed audit write is logged and the run continues, via
    `append_safe`. That is a deliberate inversion of the usual rule here: an audit sink
    that can abort a 12-minute review is a worse outcome than a gap in the trail, and the
    gap is itself logged. The append-only guarantee is structural in the repository --
    there is no update and no delete -- so a write that lands can never be edited later.
    """

    def __init__(self, repository: Any | None = None) -> None:
        self._repository = repository

    @property
    def repository(self) -> Any:
        if self._repository is None:
            from attestor_platform.firestore import AuditEventRepository

            self._repository = AuditEventRepository()
        return self._repository

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
        written = self.repository.append_safe(
            {
                "kind": kind,
                "review_id": review_id,
                "run_id": run_id,
                "round_id": round_id,
                "question_id": question_id,
                "actor": actor,
                "detail": detail or {},
            }
        )
        return str(written) if written is not None else None
