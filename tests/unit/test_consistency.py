"""Commitment matching and the constrained redraft.

The live proof is in `docs/proof/consistency-followup-drift.json`; this pins the
machinery deterministically so a regression shows up in `make check` rather than in a
cloud run. No network: the model client and the retrieval are fakes, and the fake model
is scripted to contradict on the first attempt, which is precisely the case that is hard
to provoke on purpose against an honest corpus.
"""

from __future__ import annotations

from typing import Any

import pytest

from attestor_core.domain import Department, Evidence, Question
from attestor_fleet.callbacks.audit import NullAuditSink
from attestor_fleet.callbacks.budget import BudgetLedger
from attestor_fleet.pipeline import COMMITMENT_MATCH_SCORE, ReviewPipeline
from attestor_platform.search.expansion import RetrievalResult

COMMITMENT = (
    "Kestrel Data does not offer on-premises or self-hosted deployment. Kestrel Insight "
    "is multi-tenant SaaS only."
)
REFRAMED = (
    "Our regulated business unit cannot use multi-tenant SaaS. Please describe the "
    "self-hosted deployment options available and the timeline to provision one."
)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage_metadata = None
        self.prompt_feedback = None


class _Models:
    """Returns scripted replies in order, recording the prompts it was given."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    def generate_content(self, *, model: str, contents: str) -> _Response:
        del model
        self.prompts.append(contents)
        return _Response(self.replies.pop(0) if self.replies else "")


class _Client:
    def __init__(self, *replies: str) -> None:
        self.models = _Models(list(replies))


class _Scorer:
    """Scores by substring rule, so the test states the similarity it means to test."""

    def __init__(self, matches: dict[str, float] | None = None) -> None:
        self.matches = matches or {}
        self.last_method = "embedding"
        self.billable_characters = 0

    def score(self, query: str, passages: list[str]) -> list[float]:
        del query
        return [self.matches.get(passage, 0.1) for passage in passages]


def _pipeline(
    client: _Client,
    commitments: list[tuple[str, str]],
    scorer: _Scorer,
    audit: NullAuditSink,
) -> ReviewPipeline:
    pipeline = ReviewPipeline(
        review_id="rev-test",
        run_id="run-test",
        audit=audit,
        ledger=BudgetLedger(review_id="rev-test"),
        scorer=scorer,  # type: ignore[arg-type]
        prior_commitments=commitments,
    )
    pipeline._client = client
    return pipeline


def _evidence() -> list[Evidence]:
    return [
        Evidence(
            document_uri="gs://c/engineering/deployment-options-update.txt",
            document_title="deployment-options-update",
            section="2. Available deployment models",
            content="On-premises / self-hosted: general availability, 30 business days.",
            score=0.72,
            department=Department.ENGINEERING,
        )
    ]


def _stub_retrieval(pipeline: ReviewPipeline) -> None:
    def _retrieve(agent_department: Department, question: Question) -> RetrievalResult:
        del agent_department, question
        return RetrievalResult(evidence=_evidence(), matched_by={}, queries_run=("q",))

    pipeline._guarded_retrieve = _retrieve  # type: ignore[method-assign]


class TestCommitmentMatching:
    def test_exact_id_match_still_works(self) -> None:
        """The honest re-ask: renumbered, recapitalised, same content id."""
        question = Question.from_text("Do you encrypt customer data at rest?")
        reworded = Question.from_text("12. Do you encrypt customer data at rest?")
        pipeline = _pipeline(
            _Client(),
            [(question.question_id, "Kestrel encrypts all customer data at rest.")],
            _Scorer(),
            NullAuditSink(),
        )

        assert pipeline._commitments_for(reworded) == [
            "Kestrel encrypts all customer data at rest."
        ]

    def test_reframed_question_is_matched_by_meaning(self) -> None:
        """The case id matching cannot reach, and the one the demo turns on."""
        pipeline = _pipeline(
            _Client(),
            [("some-other-id", COMMITMENT)],
            _Scorer({COMMITMENT: 0.68}),
            NullAuditSink(),
        )
        question = Question.from_text(REFRAMED)

        assert pipeline._commitments_for(question) == [COMMITMENT]

    def test_below_threshold_is_not_matched(self) -> None:
        pipeline = _pipeline(
            _Client(),
            [("some-other-id", COMMITMENT)],
            _Scorer({COMMITMENT: COMMITMENT_MATCH_SCORE - 0.01}),
            NullAuditSink(),
        )

        assert pipeline._commitments_for(Question.from_text(REFRAMED)) == []

    def test_a_commitment_is_not_returned_twice(self) -> None:
        """An exact id match that also scores above threshold is one commitment."""
        question = Question.from_text(REFRAMED)
        pipeline = _pipeline(
            _Client(),
            [(question.question_id, COMMITMENT)],
            _Scorer({COMMITMENT: 0.9}),
            NullAuditSink(),
        )

        assert pipeline._commitments_for(question) == [COMMITMENT]


class TestConstrainedRedraft:
    def _run(self, *replies: str) -> tuple[Any, NullAuditSink, _Client]:
        audit = NullAuditSink()
        client = _Client(*replies)
        pipeline = _pipeline(client, [("other-id", COMMITMENT)], _Scorer({COMMITMENT: 0.68}), audit)
        _stub_retrieval(pipeline)
        outcome = pipeline.draft(Question.from_text(REFRAMED, department=Department.ENGINEERING))
        return outcome, audit, client

    def test_a_contradicted_draft_is_redrafted_and_the_commitment_wins(self) -> None:
        outcome, _audit, client = self._run(
            "On-premises deployment is available with a 30 business day timeline. [1]",
            "CONTRADICTION\nThe draft offers what the commitment rules out.",
            "Kestrel does not offer on-premises or self-hosted deployment, as confirmed "
            "in the earlier round. [1]",
            "NO_CONTRADICTION\nThe redraft honours the commitment.",
        )

        assert outcome.answer is not None
        assert "does not offer" in outcome.answer.text
        assert "30 business day" not in outcome.answer.text
        assert outcome.constrained is True
        assert outcome.needs_human is True

        # The rejected draft is fed back to the redrafting model deliberately.
        assert "REJECTED DRAFT" in client.models.prompts[2]
        assert "PRIOR COMMITMENTS (binding)" in client.models.prompts[2]

    def test_both_consistency_passes_are_audited(self) -> None:
        """The audit trail has to show the contradiction that was caught, not only the
        clean answer that replaced it."""
        _, audit, _ = self._run(
            "On-premises deployment is available. [1]",
            "CONTRADICTION\nOffers what the commitment rules out.",
            "Kestrel does not offer on-premises deployment. [1]",
            "NO_CONTRADICTION\nHonours the commitment.",
        )

        passes = [e["detail"]["pass"] for e in audit.events if e["kind"] == "consistency_checked"]
        assert passes == ["initial", "post_redraft"]

        superseded = next(
            e for e in audit.events if e["kind"] == "answer_drafted" and e["detail"].get("redraft")
        )
        assert "available" in superseded["detail"]["superseded_text"]

    def test_a_second_contradiction_is_not_redrafted_again(self) -> None:
        """One redraft, never a loop. A model that will not comply gets a human."""
        outcome, audit, client = self._run(
            "On-premises deployment is available. [1]",
            "CONTRADICTION\nOffers what the commitment rules out.",
            "On-premises deployment is still available. [1]",
            "CONTRADICTION\nStill contradicts.",
        )

        redrafts = [
            e for e in audit.events if e["kind"] == "answer_drafted" and e["detail"].get("redraft")
        ]
        assert len(redrafts) == 1
        assert client.models.replies == []  # exactly four calls, no fifth
        assert outcome.needs_human is True

    def test_no_contradiction_means_no_redraft(self) -> None:
        outcome, audit, client = self._run(
            "Kestrel does not offer on-premises deployment. [1]",
            "NO_CONTRADICTION\nConsistent with the commitment.",
        )

        assert outcome.constrained is False
        assert not [
            e for e in audit.events if e["kind"] == "answer_drafted" and e["detail"].get("redraft")
        ]
        assert client.models.prompts and len(client.models.prompts) == 2

    def test_a_failed_redraft_keeps_the_flagged_original(self) -> None:
        """If the redraft comes back empty the contradicted verdict stands, which caps
        confidence at LOW and forces a human look."""
        outcome, _, _ = self._run(
            "On-premises deployment is available. [1]",
            "CONTRADICTION\nOffers what the commitment rules out.",
            "",
        )

        assert outcome.constrained is True
        assert outcome.contradiction.value == "contradiction"
        assert outcome.needs_human is True


@pytest.mark.parametrize("commitments", [[], [("id", COMMITMENT)]])
def test_questions_without_commitments_cost_no_extra_call(
    commitments: list[tuple[str, str]],
) -> None:
    """A run with no relevant commitment must not pay for a consistency check."""
    audit = NullAuditSink()
    client = _Client("Maintenance runs Sundays 06:00-10:00 UTC. [1]")
    pipeline = _pipeline(client, commitments, _Scorer(), audit)
    _stub_retrieval(pipeline)

    pipeline.draft(
        Question.from_text("What is your maintenance window?", department=Department.ENGINEERING)
    )

    assert len(client.models.prompts) == 1
    assert not [e for e in audit.events if e["kind"] == "consistency_checked"]
