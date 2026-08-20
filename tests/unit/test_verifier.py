"""The agent that checks the work, and the ways that check could be theatre.

A groundedness check is easy to build and easy to render meaningless. Each of these pins
one way it would look like it was working and not be:

* the verifier reviewing its own department's answer;
* an unreachable verifier reading as a pass;
* a model abstaining and having that read as an outage, or an outage reading as an
  abstention;
* a finding the verifier cannot point at, escalating an answer to a human who then has
  nothing to look at;
* a "verbatim" quote that is not in the answer -- a fabricated finding on the one page
  whose purpose is provenance.

The last of those is the sharpest. A verifier that invents the sentence it objects to is
worse than no verifier, because the objection is what a human is asked to act on.
"""

from __future__ import annotations

from typing import Any

import pytest

from attestor_core.domain import Citation, SupportVerdict
from attestor_core.domain.enums import Confidence
from attestor_core.errors import PolicyViolation
from attestor_core.policy import (
    AnswerFacts,
    ConfidenceSignals,
    compute_confidence,
    requires_human,
)
from attestor_fleet.agents.verifier import (
    VerificationResult,
    VerifierAgent,
    assert_separation,
    distribution,
)

ANSWER = (
    "Yes. Customer data is encrypted at rest using AES-256, and encryption keys are "
    "customer-managed through an external KMS."
)


def _citation(title: str = "encryption-policy", snippet: str = "") -> Citation:
    return Citation(
        document_uri=f"gs://corpus/security/{title}.md",
        document_title=title,
        section="Section 3",
        snippet=snippet or "All customer data is encrypted at rest using AES-256.",
        retrieval_score=0.82,
    )


class _StubModels:
    def __init__(self, text: str | Exception) -> None:
        self._text = text
        self.prompts: list[str] = []

    def generate_content(self, *, model: str, contents: str) -> Any:
        del model
        self.prompts.append(contents)
        if isinstance(self._text, Exception):
            raise self._text
        return type("Response", (), {"text": self._text})()


class _StubClient:
    def __init__(self, text: str | Exception) -> None:
        self.models = _StubModels(text)


def _agent(text: str | Exception, identity: str = "VerifierAgent") -> VerifierAgent:
    return VerifierAgent(identity=identity, _client=_StubClient(text))


# ---------------------------------------------------------------------------------
# Separation of duties
# ---------------------------------------------------------------------------------


class TestSeparation:
    def test_an_agent_cannot_review_its_own_answer(self) -> None:
        with pytest.raises(PolicyViolation) as caught:
            assert_separation("SecurityAgent", "SecurityAgent")
        assert "not a reviewer" in str(caught.value)

    def test_the_comparison_ignores_case_and_whitespace(self) -> None:
        # Otherwise the control is defeated by a capitalisation difference, which is not a
        # difference in who is doing the reviewing.
        with pytest.raises(PolicyViolation):
            assert_separation("SecurityAgent", "  securityagent ")

    def test_a_different_agent_is_permitted(self) -> None:
        # Returns nothing; the assertion is that it does not raise.
        assert_separation("SecurityAgent", "VerifierAgent")

    def test_verify_refuses_rather_than_returning_unknown(self) -> None:
        """The refusal must be loud.

        A self-review that quietly downgrades to "could not check" is the worst of the
        three outcomes: it looks like the control ran and found nothing.
        """
        agent = _agent('{"verdict": "supported", "reason": "fine"}', identity="SecurityAgent")
        with pytest.raises(PolicyViolation):
            agent.verify(
                question="Do you encrypt at rest?",
                answer=ANSWER,
                citations=[_citation()],
                drafted_by="SecurityAgent",
            )


# ---------------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------------


class TestVerdicts:
    def test_a_supported_answer_is_supported(self) -> None:
        agent = _agent(
            '{"verdict": "supported", "unsupported_claims": [], "reason": "All claims cited."}'
        )
        result = agent.verify(
            question="Do you encrypt at rest?",
            answer=ANSWER,
            citations=[_citation()],
            drafted_by="SecurityAgent",
        )
        assert result.verdict is SupportVerdict.SUPPORTED
        assert result.verified_by == "VerifierAgent"
        assert result.passages == 1

    def test_an_overreaching_claim_is_partially_supported_and_is_quoted(self) -> None:
        # The failure this component exists to catch: five passages at high relevance
        # establishing encryption at rest, and an answer that also asserts customer-managed
        # keys. Every other signal is green.
        agent = _agent(
            '{"verdict": "partially_supported", "unsupported_claims": '
            '["encryption keys are customer-managed through an external KMS"], '
            '"reason": "The passages establish encryption at rest but not key ownership."}'
        )
        result = agent.verify(
            question="Do you encrypt at rest?",
            answer=ANSWER,
            citations=[_citation()],
            drafted_by="SecurityAgent",
        )
        assert result.verdict is SupportVerdict.PARTIALLY_SUPPORTED
        assert result.unsupported_claims == (
            "encryption keys are customer-managed through an external KMS",
        )

    def test_a_claim_that_is_not_in_the_answer_is_dropped(self) -> None:
        """A fabricated finding is worse than a missed one.

        The prompt asks for verbatim quotes and a model paraphrases anyway. A "quote" that
        is not in the answer is an invention on the one surface whose purpose is provenance,
        and it is what a human is being asked to act on.
        """
        agent = _agent(
            '{"verdict": "partially_supported", "unsupported_claims": '
            '["they claim SOC 2 Type II certification"], "reason": "overreach"}'
        )
        result = agent.verify(
            question="Do you encrypt at rest?",
            answer=ANSWER,
            citations=[_citation()],
            drafted_by="SecurityAgent",
        )
        # Nothing locatable survived, so the finding is not actionable and the answer is
        # not held on the strength of it.
        assert result.unsupported_claims == ()
        assert result.verdict is SupportVerdict.SUPPORTED
        assert "could not quote" in result.reason

    def test_the_verifier_never_sees_the_corpus_only_the_cited_passages(self) -> None:
        agent = _agent('{"verdict": "supported", "unsupported_claims": [], "reason": "ok"}')
        agent.verify(
            question="Do you encrypt at rest?",
            answer=ANSWER,
            citations=[_citation(snippet="AES-256 at rest.")],
            drafted_by="SecurityAgent",
        )
        prompt = agent.client.models.prompts[0]
        assert "AES-256 at rest." in prompt
        assert "must not rewrite" in prompt


class TestDegradation:
    @pytest.mark.parametrize(
        "response",
        [
            RuntimeError("engine unreachable"),
            "I could not determine that.",
            "{not json at all",
            '{"verdict": "unknown", "reason": "abstaining"}',
            '{"verdict": "probably fine", "reason": "x"}',
        ],
    )
    def test_every_failure_reads_as_unknown_and_never_as_supported(
        self, response: str | Exception
    ) -> None:
        """The ninth instance of the family, refused in advance.

        An unreachable verifier, an unparseable reply and a model that abstains are all
        "the check did not run". None of them is "the check passed", and a system that
        collapsed them would report a groundedness figure it had not measured.
        """
        agent = _agent(response)
        result = agent.verify(
            question="Do you encrypt at rest?",
            answer=ANSWER,
            citations=[_citation()],
            drafted_by="SecurityAgent",
        )
        assert result.verdict is SupportVerdict.UNKNOWN

    def test_a_model_may_not_return_unknown_itself(self) -> None:
        # UNKNOWN means "our infrastructure did not run the check", which is a fact about
        # us. Letting the model reach for it would give it a way to abstain and have that
        # read as an outage in the reported distribution.
        agent = _agent('{"verdict": "unknown", "reason": "not sure"}')
        result = agent.verify(
            question="q", answer=ANSWER, citations=[_citation()], drafted_by="SecurityAgent"
        )
        assert result.verdict is SupportVerdict.UNKNOWN
        assert "unusable verdict" in result.reason

    def test_no_citations_is_unknown_not_unsupported(self) -> None:
        # An uncited answer is already refused structurally by `Answer`'s validator.
        # Reporting a groundedness failure here would double-count it.
        agent = _agent('{"verdict": "supported", "reason": "x"}')
        result = agent.verify(question="q", answer=ANSWER, citations=[], drafted_by="SecurityAgent")
        assert result.verdict is SupportVerdict.UNKNOWN
        assert result.passages == 0


# ---------------------------------------------------------------------------------
# What a verdict does
# ---------------------------------------------------------------------------------


class TestPolicyEffect:
    def _signals(self, support: SupportVerdict) -> ConfidenceSignals:
        return ConfidenceSignals(
            citation_count=3,
            max_retrieval_score=0.82,
            mean_retrieval_score=0.68,
            support=support,
        )

    def test_unsupported_is_low_and_is_held(self) -> None:
        confidence = compute_confidence(self._signals(SupportVerdict.UNSUPPORTED))
        assert confidence is Confidence.LOW
        assert requires_human(
            AnswerFacts(confidence=confidence, citation_count=3, support=SupportVerdict.UNSUPPORTED)
        )

    def test_unsupported_is_held_on_its_own_reason(self) -> None:
        """Escalated by the verdict, not only through the confidence rating.

        Otherwise the reason arrives in the queue as an undifferentiated LOW and the
        operator cannot tell it apart from weak retrieval.
        """
        assert requires_human(
            AnswerFacts(
                confidence=Confidence.HIGH,
                citation_count=3,
                support=SupportVerdict.UNSUPPORTED,
            )
        )

    def test_high_requires_someone_who_did_not_write_it(self) -> None:
        strong = ConfidenceSignals(
            citation_count=3, max_retrieval_score=0.90, mean_retrieval_score=0.70
        )
        assert (
            compute_confidence(strong.model_copy(update={"support": SupportVerdict.SUPPORTED}))
            is Confidence.HIGH
        )
        for lesser in (SupportVerdict.PARTIALLY_SUPPORTED, SupportVerdict.UNKNOWN):
            assert (
                compute_confidence(strong.model_copy(update={"support": lesser}))
                is Confidence.MEDIUM
            )

    def test_an_unreachable_verifier_does_not_hold_the_whole_round(self) -> None:
        """UNKNOWN caps rather than escalates, and that asymmetry is deliberate.

        `contradiction` treats its own UNKNOWN as LOW, because an unrun consistency check
        means round two might contradict round one in front of the customer. An unrun
        groundedness check leaves citations, retrieval scores and a contradiction verdict
        all still measured -- so dropping to LOW would escalate every answer in the round
        the moment the verifier engine blinked, which is a fail-closed that stops the
        product rather than protecting it.
        """
        facts = AnswerFacts(
            confidence=compute_confidence(self._signals(SupportVerdict.UNKNOWN)),
            citation_count=3,
            support=SupportVerdict.UNKNOWN,
        )
        assert facts.confidence is Confidence.MEDIUM
        assert requires_human(facts) is False

    def test_the_default_is_unknown_not_supported(self) -> None:
        # An answer written before the verifier existed must not read as verified.
        assert ConfidenceSignals().support is SupportVerdict.UNKNOWN
        assert AnswerFacts(confidence=Confidence.MEDIUM).support is SupportVerdict.UNKNOWN


def test_the_distribution_counts_unknown_rather_than_dropping_it() -> None:
    """ "0 unsupported of 200" and "0 unsupported of 200, 180 unchecked" differ."""
    results = [
        VerificationResult(verdict=SupportVerdict.SUPPORTED),
        VerificationResult(verdict=SupportVerdict.SUPPORTED),
        VerificationResult(verdict=SupportVerdict.PARTIALLY_SUPPORTED),
        VerificationResult(verdict=SupportVerdict.UNKNOWN),
    ]
    counts = distribution(results)
    assert counts == {
        "supported": 2,
        "partially_supported": 1,
        "unsupported": 0,
        "unknown": 1,
    }
    assert sum(counts.values()) == len(results)
