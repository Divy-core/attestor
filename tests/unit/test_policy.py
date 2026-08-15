"""Every branch of the decision layer.

Targets 100% branch coverage on `attestor_core.policy`. No GCP, no credentials, no
network -- which is the entire reason the boundary exists.
"""

from __future__ import annotations

import pytest

from attestor_core.domain.enums import (
    ArmorDecision,
    Confidence,
    ContradictionVerdict,
    Department,
    Residency,
    ToolDecision,
)
from attestor_core.policy import (
    AnswerFacts,
    ArmorVerdict,
    ConfidenceSignals,
    compute_confidence,
    decide_on_armor_verdict,
    decide_tool,
    requires_human,
    residency_permits,
)

D = Department


class TestDecideTool:
    def test_agent_may_read_its_own_corpus(self) -> None:
        assert decide_tool(D.SECURITY, "search_corpus", "corpus/security/encryption.md") is (
            ToolDecision.ALLOW
        )

    def test_security_may_not_read_legal(self) -> None:
        """The denial that gets demoed. Least privilege, enforced not suggested."""
        assert decide_tool(D.SECURITY, "search_corpus", "corpus/legal/dpa.md") is ToolDecision.DENY

    def test_legal_may_not_read_security(self) -> None:
        assert decide_tool(D.LEGAL, "search_corpus", "corpus/security/pentest.md") is (
            ToolDecision.DENY
        )

    def test_unassigned_agent_has_no_corpus_rights(self) -> None:
        assert decide_tool(D.UNASSIGNED, "search_corpus", "corpus/security/x.md") is (
            ToolDecision.DENY
        )

    def test_unscoped_tools_are_allowed(self) -> None:
        assert decide_tool(D.UNASSIGNED, "get_review_count") is ToolDecision.ALLOW

    def test_customer_facing_tools_ask(self) -> None:
        assert decide_tool(D.SECURITY, "send_to_customer") is ToolDecision.ASK
        assert decide_tool(D.SECURITY, "export_response_pack") is ToolDecision.ASK

    def test_ask_wins_over_scope(self) -> None:
        assert decide_tool(D.SECURITY, "close_round", "corpus/legal/x.md") is ToolDecision.ASK

    def test_no_resource_ref_is_allowed(self) -> None:
        assert decide_tool(D.SECURITY, "write_answer") is ToolDecision.ALLOW

    def test_non_corpus_reference_is_not_our_business(self) -> None:
        assert decide_tool(D.SECURITY, "read_review", "reviews/rev-1") is ToolDecision.ALLOW


class TestArmorDecision:
    def test_clean_content_is_allowed(self) -> None:
        assert decide_on_armor_verdict(ArmorVerdict()) is ArmorDecision.ALLOW

    def test_prompt_injection_is_denied(self) -> None:
        v = ArmorVerdict(matched=True, prompt_injection=True)
        assert decide_on_armor_verdict(v) is ArmorDecision.DENY

    def test_malicious_uri_is_denied(self) -> None:
        v = ArmorVerdict(matched=True, malicious_uri=True)
        assert decide_on_armor_verdict(v) is ArmorDecision.DENY

    def test_execution_failure_fails_closed(self) -> None:
        """Unknown is not safe. A guardrail that fails open is decorative."""
        assert decide_on_armor_verdict(ArmorVerdict(execution_failed=True)) is ArmorDecision.DENY

    def test_pii_is_quarantined_not_denied(self) -> None:
        """A customer's data in a cell is a handling problem, not an attack."""
        v = ArmorVerdict(matched=True, sensitive_data=True)
        assert decide_on_armor_verdict(v) is ArmorDecision.QUARANTINE

    def test_responsible_ai_is_quarantined(self) -> None:
        v = ArmorVerdict(matched=True, responsible_ai=True)
        assert decide_on_armor_verdict(v) is ArmorDecision.QUARANTINE

    def test_unmodelled_match_is_quarantined_not_guessed(self) -> None:
        assert decide_on_armor_verdict(ArmorVerdict(matched=True)) is ArmorDecision.QUARANTINE

    def test_injection_beats_pii_when_both_match(self) -> None:
        v = ArmorVerdict(matched=True, prompt_injection=True, sensitive_data=True)
        assert decide_on_armor_verdict(v) is ArmorDecision.DENY


class TestComputeConfidence:
    def test_no_citations_is_low(self) -> None:
        assert compute_confidence(ConfidenceSignals()) is Confidence.LOW

    def test_strong_single_hit_is_high(self) -> None:
        s = ConfidenceSignals(citation_count=1, max_retrieval_score=0.9, mean_retrieval_score=0.9)
        assert compute_confidence(s) is Confidence.HIGH

    def test_corroborated_moderate_hits_are_high(self) -> None:
        s = ConfidenceSignals(citation_count=3, max_retrieval_score=0.70, mean_retrieval_score=0.65)
        assert compute_confidence(s) is Confidence.HIGH

    def test_weak_best_score_is_low(self) -> None:
        """If even the best passage barely matches, the model is improvising."""
        s = ConfidenceSignals(citation_count=4, max_retrieval_score=0.40, mean_retrieval_score=0.35)
        assert compute_confidence(s) is Confidence.LOW

    def test_hedging_caps_at_medium(self) -> None:
        s = ConfidenceSignals(
            citation_count=3, max_retrieval_score=0.95, mean_retrieval_score=0.9, agent_hedged=True
        )
        assert compute_confidence(s) is Confidence.MEDIUM

    def test_cross_departmental_caps_at_medium(self) -> None:
        s = ConfidenceSignals(
            citation_count=3,
            max_retrieval_score=0.95,
            mean_retrieval_score=0.9,
            cross_departmental=True,
        )
        assert compute_confidence(s) is Confidence.MEDIUM

    @pytest.mark.parametrize(
        "verdict",
        [
            ContradictionVerdict.CONTRADICTION,
            ContradictionVerdict.POSSIBLE_CONTRADICTION,
            ContradictionVerdict.UNKNOWN,
        ],
    )
    def test_any_contradiction_signal_is_low(self, verdict: ContradictionVerdict) -> None:
        """UNKNOWN too: absence of evidence is not evidence of consistency."""
        s = ConfidenceSignals(
            citation_count=5,
            max_retrieval_score=0.99,
            mean_retrieval_score=0.98,
            contradiction=verdict,
        )
        assert compute_confidence(s) is Confidence.LOW

    def test_single_uncorroborated_moderate_hit_is_medium(self) -> None:
        s = ConfidenceSignals(citation_count=1, max_retrieval_score=0.60, mean_retrieval_score=0.60)
        assert compute_confidence(s) is Confidence.MEDIUM

    def test_two_citations_with_poor_mean_is_medium(self) -> None:
        s = ConfidenceSignals(citation_count=2, max_retrieval_score=0.70, mean_retrieval_score=0.56)
        assert compute_confidence(s) is Confidence.MEDIUM

    def test_is_deterministic(self) -> None:
        s = ConfidenceSignals(citation_count=2, max_retrieval_score=0.8, mean_retrieval_score=0.7)
        assert compute_confidence(s) is compute_confidence(s)


class TestRequiresHuman:
    def test_high_confidence_cited_answer_passes(self) -> None:
        facts = AnswerFacts(confidence=Confidence.HIGH, citation_count=3)
        assert requires_human(facts) is False

    def test_low_confidence_escalates(self) -> None:
        assert requires_human(AnswerFacts(confidence=Confidence.LOW, citation_count=2)) is True

    def test_no_evidence_escalates(self) -> None:
        facts = AnswerFacts(confidence=Confidence.HIGH, citation_count=0, flagged_no_evidence=True)
        assert requires_human(facts) is True

    def test_quarantined_escalates(self) -> None:
        facts = AnswerFacts(confidence=Confidence.HIGH, citation_count=3, quarantined=True)
        assert requires_human(facts) is True

    def test_contradiction_escalates(self) -> None:
        facts = AnswerFacts(
            confidence=Confidence.HIGH,
            citation_count=3,
            contradiction=ContradictionVerdict.CONTRADICTION,
        )
        assert requires_human(facts) is True

    def test_touching_a_prior_commitment_escalates(self) -> None:
        """The consistency guarantee: anything near an earlier promise gets a look."""
        facts = AnswerFacts(
            confidence=Confidence.HIGH, citation_count=3, touches_prior_commitment=True
        )
        assert requires_human(facts, prior_commitments=4) is True

    def test_touching_a_commitment_with_none_on_file_does_not_escalate(self) -> None:
        facts = AnswerFacts(
            confidence=Confidence.HIGH, citation_count=3, touches_prior_commitment=True
        )
        assert requires_human(facts, prior_commitments=0) is False

    def test_zero_citations_escalates_even_when_unflagged(self) -> None:
        facts = AnswerFacts(confidence=Confidence.HIGH, citation_count=0)
        assert requires_human(facts) is True


class TestResidency:
    def test_any_permits_everything(self) -> None:
        assert residency_permits(Residency.ANY, "asia-south1") is True

    def test_us_permits_us_central1(self) -> None:
        assert residency_permits(Residency.US, "us-central1") is True

    def test_us_refuses_eu(self) -> None:
        assert residency_permits(Residency.US, "europe-west1") is False

    def test_eu_permits_europe_west4(self) -> None:
        assert residency_permits(Residency.EU, "europe-west4") is True

    def test_in_permits_asia_south1(self) -> None:
        assert residency_permits(Residency.IN, "asia-south1") is True

    def test_is_case_insensitive(self) -> None:
        assert residency_permits(Residency.US, "US-CENTRAL1") is True

    def test_unknown_region_is_refused(self) -> None:
        """A residency check that fails open is not a residency check."""
        assert residency_permits(Residency.US, "mars-north3") is False

    def test_every_residency_is_mapped(self) -> None:
        """The invariant behind the unreachable guard in `residency_permits`.

        If someone adds a Residency member without a region set, this fails here rather
        than silently permitting every region in production.
        """
        from attestor_core.policy.decisions import _RESIDENCY_REGIONS

        unmapped = [r for r in Residency if r is not Residency.ANY and r not in _RESIDENCY_REGIONS]
        assert unmapped == [], f"Residency members with no region mapping: {unmapped}"
