"""Drafting on the deployed engines: the parts that must not be got wrong quietly.

Three properties are pinned here, and each one has a failure mode that would produce a
green run with wrong numbers rather than an error:

1. **An engine failure is not an empty answer.** This is the fifth instance of the family
   documented in `attestor_core.errors.ContextUnavailable` and the first that was caught
   before it shipped. `ReviewPipeline.draft` wraps its model call in `except Exception`
   and falls back to "no supporting evidence was found in the corpus" — correct for a
   local model hiccup, catastrophic for a remote executor, because it would file "the
   engine was unreachable" as "we have no policy on this" at `confidence: low` with a
   human flag and no error anywhere.

2. **Passages are de-duplicated.** The engine decides for itself how many searches to run.
   Citation count feeds `compute_confidence`, so a question the engine searched five ways
   would arrive with 25 citations and an inflated confidence.

3. **The drafted text is consumed once.** `_generate` is called again for the consistency
   check; handing it the draft a second time would make the check compare the answer with
   itself and never find a contradiction — which would silently disable the single hardest
   behaviour in the build.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from attestor_core.domain import Department
from dispatcher.remote import EngineUnavailable, _parse_events


def _tool_event(passages: list[dict[str, Any]], queries: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "content": {
            "parts": [
                {
                    "function_response": {
                        "response": {
                            "department": "security",
                            "passages": passages,
                            "queries_run": list(queries),
                        }
                    }
                }
            ]
        }
    }


def _text_event(text: str) -> dict[str, Any]:
    return {"content": {"parts": [{"text": text}]}}


def _passage(section: str, score: float, uri: str = "gs://b/security/a.txt") -> dict[str, Any]:
    return {
        "document": "access-control-standard",
        "section": section,
        "uri": uri,
        "score": score,
        "text": f"body of {section}",
    }


class TestParsing:
    def test_passages_repeated_across_searches_are_merged(self) -> None:
        """Three searches returning the same section is one citation, not three."""
        events = [
            _tool_event([_passage("9. Password requirements", 0.68)]),
            _tool_event([_passage("9. Password requirements", 0.71)]),
            _tool_event([_passage("2. Multi-factor authentication", 0.59)]),
            _text_event("Rotation is not forced on a schedule."),
        ]
        drafted = _parse_events(events, Department.SECURITY)

        assert len(drafted.evidence) == 2
        # The better score survives the merge -- a passage is not made worse by being
        # retrieved a second time by a weaker query.
        assert drafted.evidence[0].section == "9. Password requirements"
        assert drafted.evidence[0].score == pytest.approx(0.71)
        assert drafted.text == "Rotation is not forced on a schedule."

    def test_same_section_in_different_documents_stays_distinct(self) -> None:
        """The merge key is (uri, section). Two documents both having a section 1 is
        ordinary, and collapsing them would drop a real citation."""
        events = [
            _tool_event([_passage("1. Scope", 0.5, uri="gs://b/security/a.txt")]),
            _tool_event([_passage("1. Scope", 0.5, uri="gs://b/security/b.txt")]),
        ]
        assert len(_parse_events(events, Department.SECURITY).evidence) == 2

    def test_evidence_carries_the_scores_the_tool_reported(self) -> None:
        """Citations come from the tool's return value, never from the model's prose."""
        drafted = _parse_events(
            [_tool_event([_passage("9. Passwords", 0.6844)])], Department.SECURITY
        )
        citation = drafted.evidence[0].to_citation()
        assert citation.retrieval_score == pytest.approx(0.6844)
        assert citation.document_uri == "gs://b/security/a.txt"


class TestEngineFailureIsNotAnEmptyAnswer:
    """The one that matters. A 503 must reach the dispatcher's retry path."""

    @staticmethod
    def _pipeline(monkeypatch: pytest.MonkeyPatch, engine: Any) -> Any:
        from dispatcher.remote import RemoteDraftingPipeline

        class _Pool:
            def get(self, department: Department) -> Any:
                del department
                return engine

        return RemoteDraftingPipeline(
            review_id="rev-test",
            run_id="run-test",
            pool=_Pool(),
            screen_ingress=False,
            screen_tool_output=False,
        )

    def test_a_failing_engine_raises_rather_than_returning_no_evidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from attestor_core.domain import Question

        class _Broken:
            def stream_query(self, **_: Any) -> Any:
                raise RuntimeError("503 Service Unavailable")

        pipeline = self._pipeline(monkeypatch, _Broken())
        question = Question(
            question_id="0" * 16,
            raw_text="Do you rotate passwords?",
            text="Do you rotate passwords?",
            department=Department.SECURITY,
        )

        with pytest.raises(EngineUnavailable) as raised:
            pipeline._guarded_retrieve(Department.SECURITY, question)

        # The department and the underlying cause both survive into the message, because
        # a dead-letter entry that says only "drafting failed" is not actionable.
        assert "security" in str(raised.value)
        assert "503" in str(raised.value)

    def test_engine_unavailable_is_not_caught_as_a_retrieval_outage(self) -> None:
        """`ReviewPipeline.draft` catches `SearchUnavailable` and answers the question
        with no evidence. `EngineUnavailable` must not be catchable that way."""
        from attestor_platform.search import SearchUnavailable

        assert not issubclass(EngineUnavailable, SearchUnavailable)


class TestDraftedTextIsConsumedOnce:
    def test_the_second_generate_call_falls_through_to_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First call gets the engine's draft; the consistency check must not."""
        from attestor_fleet.pipeline import ReviewPipeline
        from dispatcher.remote import RemoteDraftingPipeline

        calls: list[str] = []

        def _fallback(self: Any, model: str, prompt: str) -> str:
            del model
            calls.append(prompt)
            return "CONSISTENT"

        # Patching the *parent's* implementation, so the test fails if the pop is removed
        # and the override starts handing the draft back a second time.
        monkeypatch.setattr(ReviewPipeline, "_generate", _fallback)

        pipeline = RemoteDraftingPipeline(review_id="rev", run_id="run", pool=object())
        pipeline._local.drafted = "the engine's answer"

        assert pipeline._generate("model", "drafting prompt") == "the engine's answer"
        assert calls == []

        assert pipeline._generate("model", "consistency prompt") == "CONSISTENT"
        assert calls == ["consistency prompt"]

    def test_the_draft_is_thread_local(self) -> None:
        """`draft_many` fans out over a thread pool. A shared attribute here would hand
        one question's answer to another question's `_generate` -- a data race that
        produces plausible wrong answers rather than a crash."""
        from dispatcher.remote import RemoteDraftingPipeline

        pipeline = RemoteDraftingPipeline(review_id="rev", run_id="run", pool=object())
        pipeline._local.drafted = "main thread draft"
        seen: list[Any] = []

        def _other() -> None:
            seen.append(getattr(pipeline._local, "drafted", None))

        thread = threading.Thread(target=_other)
        thread.start()
        thread.join()

        assert seen == [None]
        assert pipeline._local.drafted == "main thread draft"


class TestAnswersAreStampedWithTheRound:
    """The defect that made every deployed review deliver nothing.

    `AnswerRepository.for_round` queries on `Answer.round_id`. The pipeline stamped every
    answer with the *run* id, so `assemble_round` and `close_round` -- which read by round
    -- found zero answers on a review that had just drafted twelve. Nothing errored: no
    human was ever asked to approve anything, no commitment was ever recorded, and the
    review reported `delivered`.

    It was invisible in Phase 3 because a local run holds its outcomes in memory and never
    queries back by round. It only appears once the answers round-trip through Firestore,
    which is to say only on the deployed path.
    """

    def test_the_round_is_stamped_when_given(self) -> None:
        from attestor_fleet.pipeline import ReviewPipeline

        pipeline = ReviewPipeline(review_id="rev-1", run_id="run-9", round_id="rev-1-r1")
        assert pipeline.round_id == "rev-1-r1"

    def test_it_falls_back_to_the_run_id(self) -> None:
        """Existing callers -- `adk web`, the Phase 3 harnesses -- pass no round."""
        from attestor_fleet.pipeline import ReviewPipeline

        assert ReviewPipeline(review_id="rev-1", run_id="run-9").round_id == "run-9"

    def test_a_no_evidence_answer_carries_the_round_too(self) -> None:
        """The refusal path is the one that matters most: an answer the system declined
        to give still has to be findable by the round that has to explain it."""
        from attestor_core.domain import Department, Question
        from attestor_fleet.pipeline import ReviewPipeline

        pipeline = ReviewPipeline(review_id="rev-1", run_id="run-9", round_id="rev-1-r1")
        question = Question(
            question_id="a" * 16, raw_text="q", text="q", department=Department.SECURITY
        )
        assert pipeline._no_evidence_answer(question).round_id == "rev-1-r1"


class TestRateLimitsAreRetriedOnTheCall:
    """A throttled question must not cost the redraft of all 123 in its partition.

    Found by the first full-scale deployed run. Three partitions at 24 workers is 72
    concurrent queries against a quota that turns out to be per *region*, and every
    partition failed inside a second with 429 RESOURCE_EXHAUSTED. The failure handling was
    correct -- `EngineUnavailable` propagated, the dispatcher returned 500, Pub/Sub
    redelivered -- but retrying at the message level re-runs the whole partition and
    arrives back into the same congestion.
    """

    @staticmethod
    def _pipeline(engine: Any) -> Any:
        from dispatcher.remote import RemoteDraftingPipeline

        class _Pool:
            def get(self, department: Department) -> Any:
                del department
                return engine

        return RemoteDraftingPipeline(
            review_id="rev",
            run_id="run",
            pool=_Pool(),
            screen_ingress=False,
            screen_tool_output=False,
        )

    @staticmethod
    def _question() -> Any:
        from attestor_core.domain import Question

        return Question(
            question_id="b" * 16, raw_text="q", text="q", department=Department.SECURITY
        )

    def test_a_429_is_retried_and_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import dispatcher.remote as remote

        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        calls = {"n": 0}

        class _Throttled:
            def stream_query(self, **_: Any) -> Any:
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED Quota exceeded")
                return iter([_text_event("drafted")])

        pipeline = self._pipeline(_Throttled())
        result = pipeline._guarded_retrieve(Department.SECURITY, self._question())

        # Three calls to get past the 429, then two more because the engine came back with
        # prose and NO passages -- which is now retried rather than believed. Written out
        # rather than left as a bare 5, because the interaction between the two retry loops is
        # the kind of thing a later reader would otherwise have to derive.
        assert calls["n"] == 3 + (remote.EMPTY_RETRIEVAL_ATTEMPTS - 1)
        assert pipeline._local.drafted == "drafted"
        assert result.evidence == []
        assert pipeline.empty_retrievals_confirmed == 1
        assert pipeline.empty_retrievals_recovered == 0

    def test_an_empty_retrieval_is_retried_and_can_recover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The eighth failure-impersonating-empty, and the first one in the platform.

        A deployed 312-question run recorded 58 of 88 questions as retrieving zero passages.
        The same corpus queried directly returned passages for five of six of them at a top
        relevance of 0.950, and the same engines queried one at a time returned five passages
        each. The engine's search returns empty under load, and returns it *successfully* — so
        the transient-error retry never saw it and the pipeline recorded "no supporting
        evidence was found in the corpus" about a question the corpus answers.
        """
        import dispatcher.remote as remote

        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        calls = {"n": 0}

        class _EmptyThenFound:
            def stream_query(self, **_: Any) -> Any:
                calls["n"] += 1
                if calls["n"] < 2:
                    # A successful call that returns nothing. Not an exception, which is why
                    # `_query_with_retry` could never have caught it.
                    return iter([_text_event("INSUFFICIENT_EVIDENCE")])
                return iter(
                    [
                        _tool_event([_passage("2. Multi-factor authentication", 0.71)]),
                        _text_event("Yes, enforced by policy."),
                    ]
                )

        pipeline = self._pipeline(_EmptyThenFound())
        result = pipeline._guarded_retrieve(Department.SECURITY, self._question())

        assert calls["n"] == 2
        assert len(result.evidence) == 1
        # The count of false "the corpus has nothing" statements this run did not make.
        assert pipeline.empty_retrievals_recovered == 1
        assert pipeline.empty_retrievals_confirmed == 0

    def test_a_genuinely_empty_corpus_is_still_reported_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retrying must not turn a real refusal into an invented answer.

        "The corpus does not support this" is a load-bearing thing for this system to be able
        to say. The retry exists so that sentence is true when it is said, not so it is never
        said.
        """
        import dispatcher.remote as remote

        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        calls = {"n": 0}

        class _AlwaysEmpty:
            def stream_query(self, **_: Any) -> Any:
                calls["n"] += 1
                return iter([_text_event("INSUFFICIENT_EVIDENCE")])

        pipeline = self._pipeline(_AlwaysEmpty())
        result = pipeline._guarded_retrieve(Department.SECURITY, self._question())

        assert calls["n"] == remote.EMPTY_RETRIEVAL_ATTEMPTS
        assert result.evidence == []
        assert pipeline.empty_retrievals_confirmed == 1

    def test_a_non_throttle_error_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Backing off four times on a permission error wastes four ack deadlines."""
        import dispatcher.remote as remote

        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        calls = {"n": 0}

        class _Broken:
            def stream_query(self, **_: Any) -> Any:
                calls["n"] += 1
                raise RuntimeError("403 PERMISSION_DENIED")

        pipeline = self._pipeline(_Broken())
        with pytest.raises(EngineUnavailable):
            pipeline._guarded_retrieve(Department.SECURITY, self._question())
        assert calls["n"] == 1

    def test_persistent_throttling_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retries are bounded. Exhausted attempts raise -- they never return nothing."""
        import dispatcher.remote as remote

        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        calls = {"n": 0}

        class _AlwaysThrottled:
            def stream_query(self, **_: Any) -> Any:
                calls["n"] += 1
                raise RuntimeError("429 RESOURCE_EXHAUSTED")

        pipeline = self._pipeline(_AlwaysThrottled())
        with pytest.raises(EngineUnavailable) as raised:
            pipeline._guarded_retrieve(Department.SECURITY, self._question())

        assert calls["n"] == remote.ENGINE_RETRY_ATTEMPTS
        assert "429" in str(raised.value)


class TestTransientClassification:
    """Which engine failures earn another attempt.

    The rate limits were found by the first full-scale run. The *dropped stream* was found
    by a single-question smoke test that looked like a hang and was initially written off
    as output buffering:

        RemoteProtocolError: peer closed connection without sending complete message body
        (incomplete chunked read)

    `stream_query` holds a chunked response open for a whole drafting call. On one question
    that is a curiosity; on a 123-question partition it is a matter of time, and without
    retrying it the whole partition fails and every question in it is redrafted.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "429 RESOURCE_EXHAUSTED Quota exceeded",
            "RemoteProtocolError: peer closed connection without sending complete "
            "message body (incomplete chunked read)",
            "503 Service Unavailable",
            "504 DEADLINE_EXCEEDED",
            "ConnectionResetError: [Errno 104] Connection reset by peer",
        ],
    )
    def test_transient_failures_are_retried(self, message: str) -> None:
        from dispatcher.remote import _is_transient

        assert _is_transient(RuntimeError(message))

    @pytest.mark.parametrize(
        "message",
        [
            "403 PERMISSION_DENIED",
            "404 not found",
            "400 INVALID_ARGUMENT: malformed request",
        ],
    )
    def test_permanent_failures_are_not(self, message: str) -> None:
        """Backing off four times on a denial burns four ack deadlines to reach it."""
        from dispatcher.remote import _is_transient

        assert not _is_transient(RuntimeError(message))

    def test_the_exception_type_counts_too(self) -> None:
        """httpx raises `RemoteProtocolError` whose str() can be empty-ish, so the type
        name is part of what is matched."""
        from dispatcher.remote import _is_transient

        class RemoteProtocolError(Exception):
            pass

        assert _is_transient(RemoteProtocolError(""))


class TestEvidenceWithoutProseIsNotAnEmptyCorpus:
    """The seventh instance of failure-impersonating-empty, and the first found by measuring.

    `tools/compare_retrieval.py` ran 30 questions through both paths. Four came back from the
    engine with good passages -- one with fifteen, top relevance 0.744, better than the local
    path managed on the same question -- and **no prose at all**. Nothing raised. `draft` saw
    empty text, took its honest branch, and recorded:

        No supporting evidence was found in the corpus for this question.

    Which is a false statement about the corpus, at `confidence: low`, with a human flag and
    no error anywhere -- the exact signature of this family.
    """

    @staticmethod
    def _pipeline(events: list[dict[str, Any]]) -> Any:
        from dispatcher.remote import RemoteDraftingPipeline

        class _Engine:
            def stream_query(self, **_: Any) -> Any:
                return iter(events)

        class _Pool:
            def get(self, department: Department) -> Any:
                del department
                return _Engine()

        return RemoteDraftingPipeline(
            review_id="rev-test",
            run_id="run-test",
            pool=_Pool(),
            screen_ingress=False,
            screen_tool_output=False,
        )

    @staticmethod
    def _question() -> Any:
        from attestor_core.domain import Question

        return Question(
            question_id="7" * 16,
            raw_text="Describe your subprocessor review process.",
            text="Describe your subprocessor review process.",
            department=Department.SECURITY,
        )

    def test_passages_with_no_prose_are_held_for_a_human_not_filed_as_no_evidence(self) -> None:
        pipeline = self._pipeline([_tool_event([_passage("Rotation", 0.74)])])
        question = self._question()

        outcome = pipeline.draft(question)
        answer = outcome.answer

        assert answer is not None
        # The distinction the whole family is about.
        assert answer.status.value == "needs_human"
        assert answer.status.value != "flagged_no_evidence"
        assert "no supporting evidence" not in answer.text.lower()
        # The retrieved passage is kept, so the person who picks this up can read the
        # evidence rather than being sent looking for a document already on their screen.
        assert len(answer.citations) == 1
        assert outcome.needs_human is True

    def test_a_genuinely_empty_corpus_still_says_so(self) -> None:
        """The override must not swallow the real case. No passages and no prose is exactly
        what `flagged_no_evidence` is for, and it has to keep working."""
        pipeline = self._pipeline([_tool_event([])])

        outcome = pipeline.draft(self._question())
        answer = outcome.answer

        assert answer is not None
        assert answer.status.value == "flagged_no_evidence"
        assert answer.citations == []

    def test_prose_with_passages_is_untouched(self) -> None:
        """The ordinary path. If this override changed the normal case, every deployed figure
        would move and none of them would be comparable with Phase 3's."""
        pipeline = self._pipeline(
            [
                _tool_event([_passage("Rotation", 0.74)]),
                _text_event("Passwords rotate every 90 days per the access control standard."),
            ]
        )

        outcome = pipeline.draft(self._question())
        answer = outcome.answer

        assert answer is not None
        assert answer.status.value in {"drafted", "needs_human"}
        assert "rotate every 90 days" in answer.text

    def test_the_flag_does_not_leak_to_the_next_question_on_the_same_thread(self) -> None:
        """`draft_many` reuses worker threads. A no-prose flag left set would make the next
        genuinely-empty question on that thread report as held-for-human with no citations,
        which is the same bug pointing the other way."""
        pipeline = self._pipeline([_tool_event([_passage("Rotation", 0.74)])])
        first = pipeline.draft(self._question())
        assert first.answer is not None
        assert first.answer.status.value == "needs_human"

        # Same pipeline, same thread, now a genuinely empty retrieval.
        pipeline._pool = type(
            "_P",
            (),
            {
                "get": lambda _self, _d: type(
                    "_E", (), {"stream_query": lambda _s, **_k: iter([_tool_event([])])}
                )()
            },
        )()
        second = pipeline.draft(self._question())
        assert second.answer is not None
        assert second.answer.status.value == "flagged_no_evidence"
