"""The seam between "what stage runs next" and "how the fleet runs it".

Handlers know the state machine. They do not know whether the fleet executes in this
process, in an Agent Runtime session, or in a test double — and that is deliberate: Phase
4 proves the *transport* is durable, Phase 5 moves execution onto Agent Runtime, and only
this file changes when it does.

`PipelineFleetRunner` wraps the Phase 3 `ReviewPipeline` unchanged. The measured 7.84-of-8
in-process drafting concurrency, the section reranking, the guardrail surfaces, and the
constrained redraft all come along exactly as they were, because none of them was coupled
to how the work was scheduled.

`AgentRuntimeFleetRunner` is the other implementation, added in Phase 5 session three: the
same pipeline with retrieval and drafting executed by the deployed department engine, so
the work runs under the Agent Identity the conditioned IAM bindings actually scope. Which
one runs is a configuration choice (`ATTESTOR_FLEET_RUNNER`), defaulting to Agent Runtime,
and `PipelineFleetRunner` stays selectable because it is what produced Phase 3's
authoritative numbers and is the documented fallback if the remote path misbehaves.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

from attestor_core.domain import Answer, AnswerStatus, Commitment, Department, Question
from attestor_core.domain.ids import make_dedup_key
from attestor_platform.firestore import AnswerRepository, CommitmentRepository
from attestor_platform.memory import MemoryBankCommitments

logger = logging.getLogger(__name__)

#: The Agent Engine that scopes Memory Bank. Phase 0 created this one; Phase 5 replaces it
#: with the deployed fleet. Memory Bank contents are scoped per engine, so that swap is a
#: migration -- recorded here rather than discovered then.
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID", "8598754324522205184")


class FleetRunner(Protocol):
    """What a handler needs the fleet to do. Nothing about how."""

    def parse(self, gcs_uri: str) -> list[Question]: ...

    def triage(self, review_id: str, run_id: str, questions: list[Question]) -> list[Question]: ...

    def draft(
        self, review_id: str, run_id: str, department: Department, questions: list[Question]
    ) -> list[Answer]: ...

    def load_commitments(self, review_id: str) -> list[tuple[str, str]]: ...

    def record_commitments(self, review_id: str, round_id: str, answers: list[Answer]) -> int: ...

    def apply_decision(
        self,
        round_id: str,
        question_id: str,
        *,
        approved: bool,
        resolved_by: str,
        edited_text: str | None = None,
    ) -> bool: ...


class PipelineFleetRunner:
    """Runs the Phase 3 fleet in this process.

    One `ReviewPipeline` per (review, run) rather than one per message: the pipeline holds
    the relevance scorer whose passage cache is what keeps embedding cost negligible, and
    a fresh pipeline per message would throw that cache away three times per round.
    """

    def __init__(
        self,
        memory: MemoryBankCommitments | None = None,
        answers: AnswerRepository | None = None,
        commitments: CommitmentRepository | None = None,
        engine_id: str = AGENT_ENGINE_ID,
    ) -> None:
        self._memory = memory
        self._answers = answers
        self._commitments = commitments
        self._engine_id = engine_id
        self._pipelines: dict[tuple[str, str], Any] = {}
        #: What the last `draft` call measured. Read by the handler so the figures end up
        #: in the audit trail rather than only in a log line nobody correlates.
        self.last_draft_stats: dict[str, Any] = {}

    # -- lazy dependencies -------------------------------------------------------------

    @property
    def memory(self) -> MemoryBankCommitments:
        if self._memory is None:
            self._memory = MemoryBankCommitments(engine_id=self._engine_id)
        return self._memory

    @property
    def answers(self) -> AnswerRepository:
        if self._answers is None:
            self._answers = AnswerRepository()
        return self._answers

    @property
    def commitments(self) -> CommitmentRepository:
        if self._commitments is None:
            self._commitments = CommitmentRepository()
        return self._commitments

    def _pipeline(self, review_id: str, run_id: str) -> Any:
        from attestor_fleet.callbacks.audit import FirestoreAuditSink
        from attestor_fleet.callbacks.budget import BudgetLedger
        from attestor_fleet.callbacks.guard import ArmorGuard
        from attestor_fleet.pipeline import ReviewPipeline

        key = (review_id, run_id)
        if key not in self._pipelines:
            self._pipelines[key] = ReviewPipeline(
                review_id=review_id,
                run_id=run_id,
                guard=ArmorGuard(),
                audit=FirestoreAuditSink(),
                ledger=BudgetLedger(review_id=review_id),
                prior_commitments=self.load_commitments(review_id),
            )
        return self._pipelines[key]

    # -- the fleet ---------------------------------------------------------------------

    def parse(self, gcs_uri: str) -> list[Question]:
        """Download the questionnaire and parse it into questions."""
        from attestor_fleet.agents.intake import parse_xlsx
        from attestor_platform.storage.gcs import download_to_temp

        local: Path = download_to_temp(gcs_uri)
        parsed: list[Question] = parse_xlsx(local)
        return parsed

    def triage(self, review_id: str, run_id: str, questions: list[Question]) -> list[Question]:
        triaged: list[Question] = self._pipeline(review_id, run_id).triage(questions)
        return triaged

    def draft(
        self, review_id: str, run_id: str, department: Department, questions: list[Question]
    ) -> list[Answer]:
        """Draft one department's slice with the fan-out intact."""
        del department  # the questions are already scoped; kept for the log line
        pipeline = self._pipeline(review_id, run_id)
        started = time.perf_counter()
        outcomes = pipeline.draft_many(questions)
        wall = time.perf_counter() - started

        # Measured here rather than inferred later. `achieved_concurrency` lives on
        # `RunReport`, which only `ReviewPipeline.run` builds -- and the dispatcher never
        # calls `run`, it calls `draft_many` one partition at a time. Without this, the
        # deployed run could only report a configured `max_workers`, which proves nothing:
        # if the remote calls serialised on a connection pool this lands near 1.
        latencies = [o.draft_seconds for o in outcomes if o.draft_seconds]
        self.last_draft_stats = {
            "questions": len(questions),
            "wall_seconds": round(wall, 2),
            "latency_sum_seconds": round(sum(latencies), 2),
            "achieved_concurrency": round(sum(latencies) / wall, 2) if wall > 0 else 0.0,
            "slowest_question_seconds": round(max(latencies), 2) if latencies else 0.0,
            "remote_calls": getattr(pipeline, "remote_calls", 0),
            "degraded_commitment_matches": pipeline.degraded_commitment_matches,
        }
        return [o.answer for o in outcomes if o.answer is not None]

    def load_commitments(self, review_id: str) -> list[tuple[str, str]]:
        """Prior commitments from Memory Bank — canonical, and raising when unreachable."""
        return self.memory.for_review(review_id)

    def record_commitments(self, review_id: str, round_id: str, answers: list[Answer]) -> int:
        """Write this round's commitments to Memory Bank, mirroring into Firestore.

        Memory Bank is canonical; Firestore is the queryable mirror the UI and the audit
        trail read. The order matters: canonical first, so a mirror write that fails
        leaves a recoverable state rather than a commitment that exists only in the UI.
        """
        recorded = 0
        for answer in answers:
            if answer.status is AnswerStatus.FLAGGED_NO_EVIDENCE or not answer.text.strip():
                # Nothing was promised, so there is nothing to be held to.
                continue
            commitment = Commitment(
                commitment_id=make_dedup_key(review_id, round_id, answer.question_id),
                review_id=review_id,
                round_id=round_id,
                question_id=answer.question_id,
                statement=answer.text.strip(),
            )
            self.memory.record(commitment)
            self.commitments.put(commitment)
            recorded += 1
        return recorded

    def apply_decision(
        self,
        round_id: str,
        question_id: str,
        *,
        approved: bool,
        resolved_by: str,
        edited_text: str | None = None,
    ) -> bool:
        """Apply a human's approval to one answer."""
        answer = self.answers.get(round_id, question_id)
        if answer is None:
            return False
        # The reviewer's identity lives in the audit event rather than on the answer:
        # the domain model has no reviewer field, and the audit trail is where "who
        # decided this, and when" is authoritative anyway.
        del resolved_by
        updated = answer.model_copy(
            update={
                "text": edited_text or answer.text,
                "status": AnswerStatus.APPROVED if approved else AnswerStatus.REJECTED,
            }
        )
        self.answers.put(updated)
        return True


class AgentRuntimeFleetRunner(PipelineFleetRunner):
    """Drafting executed by the deployed department engines.

    The only difference from `PipelineFleetRunner` is which pipeline class it builds, and
    that is the point: everything about how a review advances — the stages, the claim, the
    join, the commitments — is unchanged, because none of it was ever coupled to where the
    drafting ran. What changes is the identity the corpus is read under.

    Why that matters rather than being an implementation detail: sessions one and two
    proved the platform refuses `attestor-security` a legal object, using the engine's own
    Agent Identity. With drafting in this process that proof described a component that was
    idle. With drafting on the engines it describes the production path, and the object
    surface is defended in depth *where the work happens*.
    """

    def _pipeline(self, review_id: str, run_id: str) -> Any:
        from attestor_fleet.callbacks.audit import FirestoreAuditSink
        from attestor_fleet.callbacks.budget import BudgetLedger
        from attestor_fleet.callbacks.guard import ArmorGuard
        from dispatcher.remote import RemoteDraftingPipeline

        key = (review_id, run_id)
        if key not in self._pipelines:
            self._pipelines[key] = RemoteDraftingPipeline(
                review_id=review_id,
                run_id=run_id,
                guard=ArmorGuard(),
                audit=FirestoreAuditSink(),
                ledger=BudgetLedger(review_id=review_id),
                prior_commitments=self.load_commitments(review_id),
            )
        return self._pipelines[key]


#: Which implementation the dispatcher builds. Agent Runtime by default -- the fleet
#: exists to run the review, and a default that quietly kept execution in the dispatcher
#: would make the architecture diagram a claim rather than a description.
FLEET_RUNNER = os.environ.get("ATTESTOR_FLEET_RUNNER", "agent_runtime").strip().lower()

_RUNNERS: dict[str, type[PipelineFleetRunner]] = {
    "agent_runtime": AgentRuntimeFleetRunner,
    "in_process": PipelineFleetRunner,
}


def build_fleet_runner(name: str | None = None) -> FleetRunner:
    """Construct the configured fleet runner.

    Raises:
        ValueError: on an unknown name. Deliberately not a silent fallback to the
            in-process runner: a typo in a Cloud Run environment variable must not
            quietly move 312 questions off the deployed engines and leave the run
            reporting success.
    """
    selected = (name or FLEET_RUNNER).strip().lower()
    implementation = _RUNNERS.get(selected)
    if implementation is None:
        raise ValueError(f"unknown ATTESTOR_FLEET_RUNNER {selected!r}; expected {sorted(_RUNNERS)}")
    logger.info("fleet runner: %s", selected)
    return implementation()
