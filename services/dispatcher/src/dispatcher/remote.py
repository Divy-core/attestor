"""Drafting executed by the deployed department engines, under their own identities.

Phase 5 sessions one and two deployed five engines, gave each its own Agent Identity, and
proved the platform refuses a cross-department read from one of them
(`docs/proof/iam-runtime-denial.json`). Session two then had to record the consequence
honestly: **the engines were not on the drafting path**. `PipelineFleetRunner` ran the
whole pipeline in the dispatcher process, under the dispatcher's service account, so the
IAM scoping that had just been proven protected a component that was idle.

This module closes that gap. The mapping was already 1:1 and that is what makes it
tractable: a `draft_answer` message carries a department partition (ADR-0005), and each
department has exactly one deployed engine.

    draft_answer(partition=security)
      -> stream_query against reasoningEngines/6333637226001334272
      -> the engine searches ITS datastore and drafts, under ITS Agent Identity

## What is deliberately reused rather than rewritten

`RemoteDraftingPipeline` subclasses the Phase 3 `ReviewPipeline` and overrides exactly two
methods. Everything else — per-passage Model Armor screening, the commitment consistency
check, the one-shot constrained redraft, computed confidence, the audit events, the
human-escalation rule — is the *same code* running on the *same objects*. That is not
laziness; it is the only way the deployed numbers can be compared with Phase 3's at all.
Reimplementing the surrounding logic would mean any difference in the measured figures
could be the new code rather than the new execution environment.

## Why the whole remote call happens in `_guarded_retrieve`

An engine failure must not be able to look like an answered question with no evidence.
`ReviewPipeline.draft` wraps its `_generate` call in `except Exception` and falls back to
`_no_evidence_answer` — correct for a model hiccup on a local call, catastrophic here,
because "the engine was unreachable" would be recorded as "the corpus has nothing on
this". So the remote round-trip is made in `_guarded_retrieve`, whose only caught
exceptions are `PolicyViolation` and `SearchUnavailable`; `EngineUnavailable` is neither
and propagates out of `draft_many`, out of the handler, and into the dispatcher's
retry path as a **500**. A 503 from an engine is transient work, not a drafted answer.

This is the sixth instance of the failure-impersonating-empty family recorded in
`attestor_core.errors.ContextUnavailable`, and the first one caught before it shipped.

## Precisely which model calls move, and which do not

Stated exactly, because "drafting runs on the deployed engines" is the claim this module
exists to make true and a vague version of it would be worse than none:

| Call | Where it runs | Under whose identity |
|---|---|---|
| Corpus retrieval | the department engine | the engine's Agent Identity |
| The draft itself | the department engine | the engine's Agent Identity |
| Triage classification | the dispatcher | the dispatcher's service account |
| Commitment consistency check | the dispatcher | the dispatcher's service account |
| Constrained redraft | the dispatcher | the dispatcher's service account |

Triage is not a department's work — it is what *decides* the department, so routing it to
one would be circular. The consistency check and the redraft are compliance controls over
the answer rather than authorship of it, and they read Memory Bank commitments the
department engines have no business holding. The two calls that touch the corpus and
produce the customer-facing text are the two that moved, and those are the two the IAM
scoping covers.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from attestor_core.domain import Department, Evidence, Question
from attestor_core.errors import AttestorError
from attestor_fleet.callbacks.guard import enforce_tool_policy
from attestor_fleet.pipeline import ReviewPipeline
from attestor_platform.search import RetrievalResult

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent.parent.parent
DEPLOYMENT = ROOT / "docs" / "proof" / "fleet-deployment.json"

LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")

#: Per-department engine resource names, by environment variable. Cloud Run has no repo
#: checkout, so the deployment record is a local convenience and the environment is the
#: real source -- `infra/deploy.sh` sets these from the same file.
ENGINE_ENV = {
    Department.SECURITY: "ATTESTOR_ENGINE_SECURITY",
    Department.LEGAL: "ATTESTOR_ENGINE_LEGAL",
    Department.ENGINEERING: "ATTESTOR_ENGINE_ENGINEERING",
}

#: Remote drafting fan-out. See `RemoteDraftingPipeline.draft_many` for why this is 24
#: rather than the in-process 8, and why the number is load-bearing rather than a knob.
REMOTE_DRAFT_CONCURRENCY = int(os.environ.get("ATTESTOR_REMOTE_CONCURRENCY", "24"))

#: How the engine is asked for one question. Terse on purpose: the engine already carries
#: its department instruction, its corpus binding, and the INSUFFICIENT_EVIDENCE contract,
#: all pickled into the deployed artifact where a prompt cannot argue with them.
DRAFT_REQUEST = (
    "Answer this vendor security review question. Search your corpus first, then answer "
    "in prose grounded in what you retrieved.\n\nQuestion: {question}"
)


class EngineUnavailable(AttestorError):
    """A deployed engine could not be reached, or failed while drafting.

    Deliberately not a subclass of `SearchUnavailable`: `ReviewPipeline.draft` catches
    that one and converts it into an outcome with no answer, which is the right shape for
    a retrieval outage on a question but the wrong shape for a partition whose executor
    is down. This one propagates so the message is redelivered and the partition is
    retried whole.
    """


@dataclass
class _RemoteDraft:
    """What one engine round-trip produced, before the pipeline judges it."""

    text: str
    evidence: list[Evidence]
    queries_run: tuple[str, ...]


def engine_name(department: Department) -> str:
    """Resolve one department's deployed engine resource name.

    Raises:
        EngineUnavailable: if no engine is configured. An unset variable must fail here,
            loudly, rather than fall back to some other department's engine.
    """
    variable = ENGINE_ENV.get(department)
    if variable is not None:
        configured = os.environ.get(variable)
        if configured:
            return configured

    if DEPLOYMENT.exists():
        record = json.loads(DEPLOYMENT.read_text(encoding="utf-8"))
        for engine in record.get("engines", []):
            if engine.get("role") == department.value:
                return str(engine["resource_name"])

    raise EngineUnavailable(
        f"no deployed engine configured for {department.value!r}; "
        f"set {variable} or run the fleet deploy"
    )


class EnginePool:
    """One engine handle per department, built on first use and shared across threads.

    Constructing a client per question would open a fresh channel on every one of the
    ~123 calls a partition makes, which would measure as latency and read as the remote
    path being inherently slow.
    """

    def __init__(self, project: str | None = None, location: str = LOCATION) -> None:
        self._project = project or os.environ.get("PROJECT_ID")
        self._location = location
        self._engines: dict[Department, Any] = {}
        self._lock = threading.Lock()

    def get(self, department: Department) -> Any:
        with self._lock:
            if department not in self._engines:
                import agentplatform

                client = agentplatform.Client(project=self._project, location=self._location)
                self._engines[department] = client.agent_engines.get(
                    name=engine_name(department)
                )
            return self._engines[department]


def _parse_events(events: Any, department: Department) -> _RemoteDraft:
    """Turn one engine's event stream into evidence plus drafted text.

    The tool's own return value is the evidence, not the model's paraphrase of it: the
    passages come from the `function_response` parts, with the document, section, URI and
    retrieval score the engine's search tool actually reported. Reconstructing citations
    from prose would be exactly the unsourced-assertion failure the whole system exists to
    prevent.
    """
    texts: list[str] = []
    #: Keyed by (uri, section) because the engine decides for itself how many searches to
    #: run, and a question it searches three ways returns the same passage three times.
    #: Left undeduplicated, a well-retrieved question reports 25 citations where the local
    #: path reports 5 -- and citation count feeds `compute_confidence`, so the duplicates
    #: would not merely look untidy, they would inflate confidence.
    merged: dict[tuple[str, str | None], Evidence] = {}
    queries: tuple[str, ...] = ()

    for event in events:
        payload = event if isinstance(event, dict) else {}
        for part in (payload.get("content") or {}).get("parts") or []:
            if isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"].strip())

            response = (part.get("function_response") or {}).get("response")
            if not isinstance(response, dict):
                continue
            queries = tuple(response.get("queries_run") or ()) or queries
            for passage in response.get("passages") or []:
                if not isinstance(passage, dict):
                    continue
                item = Evidence(
                    document_uri=str(passage.get("uri") or ""),
                    document_title=str(passage.get("document") or ""),
                    section=passage.get("section") or None,
                    content=str(passage.get("text") or ""),
                    score=float(passage.get("score") or 0.0),
                    department=department,
                )
                key = (item.document_uri, item.section)
                current = merged.get(key)
                if current is None or item.score > current.score:
                    merged[key] = item

    evidence = sorted(merged.values(), key=lambda e: e.score, reverse=True)
    return _RemoteDraft(text="\n\n".join(texts).strip(), evidence=evidence, queries_run=queries)


class RemoteDraftingPipeline(ReviewPipeline):
    """The Phase 3 pipeline with retrieval and drafting executed on a deployed engine.

    Two overrides and nothing else. The guard, the consistency check, the confidence
    computation, the audit sink and the escalation rule are inherited unchanged, so a
    difference between these numbers and Phase 3's is attributable to the execution
    environment rather than to a second implementation of the same logic.
    """

    def __init__(self, *args: Any, pool: EnginePool | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pool = pool if pool is not None else EnginePool()
        #: Thread-local because `draft_many` fans out over a ThreadPoolExecutor: the text
        #: the engine produced for *this* question must not be handed to another thread's
        #: `_generate`. An instance attribute here would be a data race that produced
        #: plausible-looking wrong answers rather than a crash.
        self._local = threading.local()
        #: How many remote calls were made, and how long they took, so the deployed run
        #: can report its own concurrency rather than inheriting Phase 3's figure.
        self.remote_calls = 0

    # -- the two overrides ------------------------------------------------------------

    def _guarded_retrieve(
        self, agent_department: Department, question: Question
    ) -> RetrievalResult:
        """Ask the department's engine to retrieve and draft, in one round-trip.

        The policy interceptor still runs first. It is not made redundant by IAM: it
        refuses earlier, it refuses with an audit event naming the agent and the resource,
        and it is the layer that still applies on the datastore surface, where per-agent
        IAM is not expressible (`docs/proof/permission-surfaces-and-composition.md`).

        Raises:
            PolicyViolation: agent department and target corpus disagree.
            EngineUnavailable: the engine could not be reached or failed mid-stream.
        """
        enforce_tool_policy(
            agent_department,
            "search_corpus",
            f"corpus/{agent_department.value}",
            agent_name=f"{agent_department.value.capitalize()}Agent",
        )

        try:
            engine = self._pool.get(agent_department)
            events = list(
                engine.stream_query(
                    message=DRAFT_REQUEST.format(question=question.text),
                    user_id=f"{self.review_id}:{self.run_id}",
                )
            )
        except EngineUnavailable:
            raise
        except Exception as exc:
            # Transient by default. The dispatcher decides whether attempts remain; what
            # must never happen is this being recorded as a question with no evidence.
            raise EngineUnavailable(
                f"engine for {agent_department.value!r} failed on "
                f"{question.question_id}: {type(exc).__name__}: {exc}",
                review_id=self.review_id,
                run_id=self.run_id,
            ) from exc

        self.remote_calls += 1
        drafted = _parse_events(events, agent_department)
        self._local.drafted = drafted.text

        return RetrievalResult(
            evidence=drafted.evidence,
            matched_by={e.document_uri: "engine" for e in drafted.evidence},
            queries_run=drafted.queries_run,
        )

    def draft_many(self, questions: list[Question]) -> list[Any]:
        """Fan out wider than the in-process pipeline, because the work is now waiting.

        `DRAFT_CONCURRENCY = 8` was sized for in-process drafting, where each worker holds
        a thread that is genuinely computing and the ceiling is the local machine. A remote
        call is a thread parked on a socket while an Agent Runtime instance does the work,
        so the ceiling is the platform's, not ours.

        The number matters rather than being a knob. Measured against the deployed security
        engine, one question costs ~45s end to end. At 8 workers the 123-question security
        partition runs ~690s — **past the 600s Pub/Sub ack deadline**, which means a
        redelivery mid-partition every time, five of them, and the message dead-lettered
        while the handler that owns it is still working and about to succeed. At 24 it runs
        ~230s and the margin `docs/proof/ack-deadline-margin.md` reasons about is real
        again.

        This is exactly the "different kind of parallelism, measure it on its own terms"
        that `docs/proof/permission-surfaces-and-composition.md` predicted would be needed
        once drafting moved off-process.
        """
        if not questions:
            return []
        workers = min(REMOTE_DRAFT_CONCURRENCY, len(questions))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.draft, questions))

    def _generate(self, model: str, prompt: str) -> str:
        """Return the engine's draft, or fall through for the calls that stay local.

        The drafted text is *popped*, not read: a second call for the same question is
        the consistency check or the redraft, and handing either of them the first
        draft's text back would silently make the check compare the answer with itself.
        """
        drafted = getattr(self._local, "drafted", None)
        if drafted is not None:
            self._local.drafted = None
            return str(drafted)
        return str(super()._generate(model, prompt))
