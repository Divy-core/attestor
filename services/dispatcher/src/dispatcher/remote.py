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

`RemoteDraftingPipeline` subclasses the Phase 3 `ReviewPipeline` and overrides three
methods, two of which move execution and one of which corrects a statement the base class
cannot know is false. Everything else — per-passage Model Armor screening, the commitment
consistency check, the one-shot constrained redraft, computed confidence, the audit events,
the human-escalation rule — is the *same code* running on the *same objects*. That is not
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

The **seventh** was not caught before it shipped, and it is on this path too: an engine that
retrieves passages and then returns no prose at all. `draft` sees empty text, takes its
honest branch, and records "no supporting evidence was found in the corpus" about a question
the corpus had fifteen passages for. Found by `tools/compare_retrieval.py` rather than by a
stack trace, because nothing failed. Corrected in `_no_evidence_answer` below.

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
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from attestor_core.domain import (
    Answer,
    AnswerStatus,
    Confidence,
    Department,
    Evidence,
    Question,
)
from attestor_core.errors import AttestorError
from attestor_fleet.callbacks.guard import enforce_tool_policy
from attestor_fleet.pipeline import ReviewPipeline
from attestor_platform.retry import TRANSIENT_MARKERS, is_transient
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

#: Remote drafting fan-out, PER PARTITION. Three partitions run at once, so this is a
#: third of the concurrent load on Agent Runtime -- which is the number that matters,
#: because the quota that bites is per region, not per engine. See
#: `RemoteDraftingPipeline.draft_many` and `_query_with_retry`.
REMOTE_DRAFT_CONCURRENCY = int(os.environ.get("ATTESTOR_REMOTE_CONCURRENCY", "8"))

#: Retrying a rate-limited engine call. Same shape as the Model Armor and search clients:
#: transient refusals are retried with exponential backoff before anything gives up.
ENGINE_RETRY_ATTEMPTS = 4
ENGINE_RETRY_BACKOFF_SECONDS = 4.0

#: How the engine is asked for one question. Terse on purpose: the engine already carries
#: its department instruction, its corpus binding, and the INSUFFICIENT_EVIDENCE contract,
#: all pickled into the deployed artifact where a prompt cannot argue with them.
DRAFT_REQUEST = (
    "Answer this vendor security review question. Search your corpus first, then answer "
    "in prose grounded in what you retrieved.\n\nQuestion: {question}"
)


#: Which engine failures are worth trying again. The list itself now lives one layer down,
#: in `attestor_platform.retry`, because it was learned here and needed elsewhere.
#:
#: Two families were found on this path. **Rate limits**, by the first full-scale run.
#: **Dropped streams** — `stream_query` holds a chunked HTTP response open for the whole of
#: a drafting call, and a long-lived stream is a thing that gets cut:
#:
#:     RemoteProtocolError: peer closed connection without sending complete message body
#:     (incomplete chunked read)
#:
#: That was observed on a single-question smoke test and initially mistaken for the harness
#: hanging. On a 123-question partition it is not an oddity, it is a matter of time — and
#: without a retry it fails the entire partition, which then redrafts every question in it.
#:
#: Memory Bank writes go over the same transport to the same service and had none of it,
#: which is how `close_round` came to exhaust five delivery attempts. Shared code goes
#: *down* into a leaf rather than sideways between services, so the classifier moved to
#: `attestor_platform.retry` and both callers use the one list. These names stay as aliases:
#: they are the vocabulary the tests and the surrounding docstrings use.
TRANSIENT_ENGINE_ERRORS = TRANSIENT_MARKERS
_is_transient = is_transient


#: How many times an EMPTY retrieval is tried again before it is believed.
#:
#: The eighth instance of the failure-impersonating-empty family, and the first one that lives
#: in the platform rather than in this codebase. It was measured rather than suspected:
#:
#:   - a deployed 312-question run recorded 58 of 88 questions as retrieving **zero** passages;
#:   - querying the same corpus directly, from this process, returned passages for five of six
#:     of them with a top relevance of 0.950;
#:   - querying the deployed engines **one at a time** returned five passages each, from all
#:     three departments.
#:
#: So the engine's own search returns an empty result set under sustained load, and returns it
#: *successfully*. `_query_with_retry` covers calls that raise — rate limits, dropped streams —
#: and a call that succeeds with nothing in it is not one of those. It was taken as
#: authoritative, and `ReviewPipeline.draft` then did the honest thing with it and recorded
#:
#:     No supporting evidence was found in the corpus for this question.
#:
#: about a question the corpus answers at 0.950. Every layer behaved correctly and the
#: aggregate statement was false, which is the signature of this whole family.
#:
#: It is also why the numbers diverged: 86.7% cited when the same 30 questions were compared
#: path-by-path in Phase 6 (`docs/proof/citation-gap-side-by-side.json`), against 20-37% on a
#: full deployed run. A 30-question comparison does not put the engines under the load that
#: produces this, so A1's "no retrieval regression" conclusion was right about what it measured
#: and blind to what it could not.
#:
#: Retrying an empty result is cheap — the questions that are genuinely unsupported pay three
#: extra calls each, and being wrong about them is what the system is built not to be.
EMPTY_RETRIEVAL_ATTEMPTS = int(os.environ.get("ATTESTOR_EMPTY_RETRIEVAL_ATTEMPTS", "3"))
EMPTY_RETRIEVAL_BACKOFF_SECONDS = 3.0


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
                self._engines[department] = client.agent_engines.get(name=engine_name(department))
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

    Three overrides. `_guarded_retrieve` and `_generate` move the work; `_no_evidence_answer`
    corrects a sentence the base class has no way to know is false. The guard, the consistency
    check, the confidence computation, the audit sink and the escalation rule are inherited
    unchanged, so a difference between these numbers and Phase 3's is attributable to the
    execution environment rather than to a second implementation of the same logic.
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
        #: Questions where the engine returned nothing and a retry found passages. This is the
        #: count of false "the corpus has nothing" statements that were NOT made.
        self.empty_retrievals_recovered = 0
        #: Questions still empty after every retry. These are the genuine no-evidence answers,
        #: and reporting them separately is what makes the other number meaningful.
        self.empty_retrievals_confirmed = 0

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

        engine = self._pool.get(agent_department)
        message = DRAFT_REQUEST.format(question=question.text)

        # An empty retrieval is retried, because an empty retrieval is not evidence of an
        # empty corpus. See `EMPTY_RETRIEVAL_ATTEMPTS`.
        drafted = None
        for attempt in range(EMPTY_RETRIEVAL_ATTEMPTS):
            events = self._query_with_retry(engine, agent_department, question, message)
            self.remote_calls += 1
            drafted = _parse_events(events, agent_department)
            if drafted.evidence:
                if attempt:
                    self.empty_retrievals_recovered += 1
                    logger.info(
                        "engine %s returned %d passages on retry %d after an empty result",
                        agent_department.value,
                        len(drafted.evidence),
                        attempt,
                        extra={"review_id": self.review_id, "run_id": self.run_id},
                    )
                break
            if attempt + 1 < EMPTY_RETRIEVAL_ATTEMPTS:
                delay = EMPTY_RETRIEVAL_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1.5)
                logger.warning(
                    "engine %s retrieved nothing for %s (attempt %d/%d); retrying in %.1fs",
                    agent_department.value,
                    question.question_id,
                    attempt + 1,
                    EMPTY_RETRIEVAL_ATTEMPTS,
                    delay,
                    extra={"review_id": self.review_id, "run_id": self.run_id},
                )
                time.sleep(delay)
        assert drafted is not None  # noqa: S101 - the loop runs at least once
        if not drafted.evidence:
            self.empty_retrievals_confirmed += 1
        self._local.drafted = drafted.text
        # An engine that retrieved passages and produced no prose. See
        # `_no_evidence_answer`: this must not become a statement about the corpus.
        self._local.no_prose = bool(drafted.evidence) and not drafted.text.strip()
        self._local.evidence = list(drafted.evidence)

        return RetrievalResult(
            evidence=drafted.evidence,
            matched_by={e.document_uri: "engine" for e in drafted.evidence},
            queries_run=drafted.queries_run,
        )

    def _query_with_retry(
        self,
        engine: Any,
        department: Department,
        question: Question,
        message: str,
    ) -> list[Any]:
        """One engine round-trip, retrying the transient refusals.

        Agent Runtime enforces a **per-minute, per-region quota** on
        `Query Reasoning Engine requests`, and the first full-scale deployed run found it
        the hard way: three partitions at 24 workers each is 72 concurrent queries, and
        every partition died with

            429 RESOURCE_EXHAUSTED  Quota exceeded for quota metric
            'Query Reasoning Engine requests' ... per minute per region

        The failure handling was *correct* — `EngineUnavailable` propagated, the dispatcher
        returned 500, Pub/Sub redelivered — but retrying at the message level means one
        rate-limited question costs the redraft of all 123 in its partition, and the
        retries arrive just as congested. Rate limiting belongs where every other client in
        this codebase already puts it: at the individual call, with backoff.

        Jittered, because 24 threads that back off in lockstep re-collide on the same
        second and reproduce the burst that caused the 429.

        Raises:
            EngineUnavailable: after `ENGINE_RETRY_ATTEMPTS`, or immediately on an error
                that is not a rate limit. Never returns an empty result to stand in for a
                failure.
        """
        last: Exception | None = None
        for attempt in range(ENGINE_RETRY_ATTEMPTS):
            try:
                return list(
                    engine.stream_query(message=message, user_id=f"{self.review_id}:{self.run_id}")
                )
            except Exception as exc:
                last = exc
                if not _is_transient(exc) or attempt == ENGINE_RETRY_ATTEMPTS - 1:
                    break
                # S311: see attestor_platform.retry -- jitter, not a secret.
                delay = ENGINE_RETRY_BACKOFF_SECONDS * (2**attempt) + random.uniform(  # noqa: S311
                    0, 2.0
                )
                logger.warning(
                    "engine %s transient failure on %s (attempt %d/%d); retrying in %.1fs",
                    department.value,
                    question.question_id,
                    attempt + 1,
                    ENGINE_RETRY_ATTEMPTS,
                    delay,
                    extra={"review_id": self.review_id, "run_id": self.run_id},
                )
                time.sleep(delay)

        raise EngineUnavailable(
            f"engine for {department.value!r} failed on {question.question_id} after "
            f"{ENGINE_RETRY_ATTEMPTS} attempt(s): {type(last).__name__}: {last}",
            review_id=self.review_id,
            run_id=self.run_id,
        ) from last

    def draft_many(
        self,
        questions: list[Question],
        on_outcome: Callable[[Any], None] | None = None,
        deadline: float | None = None,
    ) -> list[Any]:
        """Fan out wider than the in-process pipeline, because the work is now waiting.

        `DRAFT_CONCURRENCY = 8` was sized for in-process drafting, where each worker holds
        a thread that is genuinely computing and the ceiling is the local machine. A remote
        call is a thread parked on a socket while an Agent Runtime instance does the work,
        so the ceiling is the platform's, not ours.

        The number is not a knob, and it was settled by being wrong twice.

        At **8** the arithmetic said the 123-question security partition would run ~690s
        against a ~45s-per-question cost — past the 600s ack deadline. So the first
        full-scale attempt used **24**, and every partition died inside a second:

            429 RESOURCE_EXHAUSTED  Quota exceeded for quota metric
            'Query Reasoning Engine requests' ... per minute per region

        Three partitions at 24 is 72 concurrent queries, and the binding limit turned out
        to be regional rather than per-engine — so the fan-out that fixed the deadline
        broke the quota. Back to **8 per partition, 24 in total**, with the rate limit
        handled where it belongs: `_query_with_retry` backs off on the individual call
        rather than letting one throttled question cost a redraft of all 123.

        A partition may still run past the ack deadline. That is what the lease is for:
        the redelivery at 600s finds a live, heartbeated claim and is refused with 409
        rather than starting a second copy — the 900s-over-600s ordering doing exactly the
        job `docs/proof/ack-deadline-margin.md` sized it for, on the first run that
        actually needed it.

        This is the "different kind of parallelism, measure it on its own terms" that
        `docs/proof/permission-surfaces-and-composition.md` predicted would be needed once
        drafting moved off-process. The prediction was right; the first two numbers were
        not.

        ## `on_outcome` has to be honoured here, not only on the base class

        This override existed before incremental persistence did, and it originally took only
        `questions`. Adding the callback to `ReviewPipeline.draft_many` and stopping there
        would have wired the resume into the in-process runner and left the **deployed** path
        -- the one that has the 600s deadline problem -- silently unchanged: the dispatcher
        would pass a callback, this method would drop it, and every partition would still
        restart from zero while the audit trail reported a resume that never happened.

        `mypy --strict` caught it as an incompatible override. Recorded because the failure
        mode is the interesting part: the code would have run, the tests over the base class
        would have passed, and the artefact would have looked like a fix.
        """
        if not questions:
            return []
        workers = min(REMOTE_DRAFT_CONCURRENCY, len(questions))

        def one(question: Question) -> Any:
            if deadline is not None and time.monotonic() >= deadline:
                from attestor_fleet.pipeline import QuestionOutcome

                return QuestionOutcome(question=question, error="deadline")
            outcome = self.draft(question)
            if on_outcome is not None:
                on_outcome(outcome)
            return outcome

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, questions))

    def _no_evidence_answer(self, question: Question) -> Answer:
        """Distinguish "the corpus has nothing" from "the engine returned nothing".

        The seventh instance of the failure-impersonating-empty family, and the first found by
        a measurement harness rather than by a stack trace. `tools/compare_retrieval.py` ran
        the same 30 questions through both paths and found four on which the engine had
        retrieved good passages -- one of them fifteen, top relevance 0.744, better than the
        local path managed -- returned **no prose at all**, and had the answer recorded as:

            No supporting evidence was found in the corpus for this question.

        That sentence is false. The corpus answered; the engine did not speak. `stream_query`
        had already emitted the `function_response` parts carrying the passages, and the final
        text part never arrived -- the same truncated-stream shape as the dropped-connection
        family, minus the exception. `ReviewPipeline.draft` cannot tell the difference, and
        should not have to: it checks `if not text` and takes the honest branch for the case it
        knows about.

        So the branch is corrected here, where the cause is known. The answer says what
        happened, keeps the citations the engine did retrieve so a human can read them, and is
        `NEEDS_HUMAN` rather than `FLAGGED_NO_EVIDENCE` -- because a person can answer this
        question from the passages on screen, and telling them the corpus is empty would send
        them looking for a document that is already in front of them.

        This is a third override, against the two-method discipline the class docstring sets
        out. That constraint is there to keep the deployed figures comparable with Phase 3's,
        and it is worth breaking for exactly one reason: the alternative is the system making
        a false statement about its own evidence. Nothing about the comparison changes -- the
        questions that took this branch were miscounted as no-evidence before and are counted
        as held-for-human now, which is what they always were.
        """
        if not getattr(self._local, "no_prose", False):
            return super()._no_evidence_answer(question)

        self._local.no_prose = False
        evidence: list[Evidence] = list(getattr(self._local, "evidence", []) or [])
        return Answer(
            question_id=question.question_id,
            round_id=self.round_id,
            text=(
                "Held for a human. The department engine retrieved supporting passages for "
                "this question but returned no drafted answer, so there is evidence to work "
                "from and no draft to review. The passages are cited below."
            ),
            # Citations kept deliberately. Zero citations is legal only when the system
            # genuinely has nothing, and here it has fifteen passages.
            citations=[e.to_citation(e.content[:400]) for e in evidence],
            confidence=Confidence.LOW,
            status=AnswerStatus.NEEDS_HUMAN,
            authored_by=f"{self.__class__.__name__}",
        )

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
