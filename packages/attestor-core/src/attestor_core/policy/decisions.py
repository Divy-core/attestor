"""The decision layer: deny / ask / allow, confidence, escalation, residency.

Pure functions over frozen inputs. No I/O, no network, no clock reads, no randomness.
Every branch here is unit-testable on a free CI runner in well under a second, which is
the entire reason the boundary exists.

Where a decision needs a model call -- notably "does this answer contradict what we
told them in July?" -- this module takes the *verdict* as an input and decides what to
do about it. Computing the verdict belongs in `attestor_fleet`. That is the
ports-and-adapters split: `platform` obtains, `core` decides.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from attestor_core.domain.enums import (
    ArmorDecision,
    Confidence,
    ContradictionVerdict,
    Department,
    Residency,
    ToolDecision,
)

Score = Annotated[float, Field(ge=0.0, le=1.0)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------------
# Tool access: the department boundary
# ---------------------------------------------------------------------------------

#: Corpus resources are addressed as "corpus/<department>/<path>". The department
#: segment is what makes scoping mechanical rather than a prompt instruction.
_CORPUS_REF = re.compile(r"^corpus/(?P<department>[a-z]+)(?:/.*)?$")

#: Tools any agent may call regardless of department.
_UNSCOPED_TOOLS: frozenset[str] = frozenset(
    {"get_review_count", "list_skills", "search_skills", "load_skill"}
)

#: Tools that mutate state outside the agent's own review, or reach the customer.
#: Never auto-allowed -- a human confirms. This is the Mynd tri-state interceptor
#: pattern carried over to ADK's before_tool_callback.
_ASK_TOOLS: frozenset[str] = frozenset({"export_response_pack", "send_to_customer", "close_round"})


def decide_tool(
    agent_dept: Department,
    tool_name: str,
    resource_ref: str | None = None,
) -> ToolDecision:
    """Decide whether an agent may invoke a tool against a resource.

    The rule that matters: an agent scoped to one department may not read another
    department's corpus. Legal answers come from the legal corpus, security answers
    from the security corpus. A single agent with the union of all permissions would be
    a least-privilege violation, and the fleet exists precisely because the access
    boundaries are per-department.

    Args:
        agent_dept: The department the calling agent is scoped to.
        tool_name: The tool being invoked.
        resource_ref: What it is being invoked against, e.g. ``"corpus/legal/dpa.md"``.

    Returns:
        ALLOW, ASK, or DENY.
    """
    if tool_name in _ASK_TOOLS:
        return ToolDecision.ASK

    if tool_name in _UNSCOPED_TOOLS:
        return ToolDecision.ALLOW

    if resource_ref is None:
        # No resource to scope against; the tool itself is not restricted.
        return ToolDecision.ALLOW

    match = _CORPUS_REF.match(resource_ref)
    if match is None:
        # Not a corpus reference. Nothing here claims authority over it.
        return ToolDecision.ALLOW

    target = match.group("department")

    # An unassigned agent has no corpus rights at all. Triage must run first.
    if agent_dept is Department.UNASSIGNED:
        return ToolDecision.DENY

    # The shared Evidence agent is scoped per call, not per agent, so it never reaches
    # here with UNASSIGNED; a mismatch is a genuine cross-department attempt.
    if target != agent_dept.value:
        return ToolDecision.DENY

    return ToolDecision.ALLOW


# ---------------------------------------------------------------------------------
# Model Armor verdicts
# ---------------------------------------------------------------------------------


class ArmorVerdict(_Frozen):
    """A Model Armor result, reduced to what the decision depends on.

    Deliberately not the raw API response: `platform.armor` maps the wire shape
    (``filterMatchState``, ``piAndJailbreakFilterResult``, ``sdpFilterResult``, ...)
    onto this, so a change in Google's response format touches one adapter rather than
    every policy branch.
    """

    #: True when any filter matched.
    matched: bool = False
    #: Prompt injection / jailbreak filter matched.
    prompt_injection: bool = False
    #: Sensitive Data Protection matched (PII, credit cards, secrets).
    sensitive_data: bool = False
    #: Responsible-AI filters matched (dangerous, harassment, hate, sexual).
    responsible_ai: bool = False
    #: Malicious URI filter matched.
    malicious_uri: bool = False
    #: True when the call to Model Armor itself failed.
    execution_failed: bool = False


def decide_on_armor_verdict(verdict: ArmorVerdict) -> ArmorDecision:
    """Decide what to do with content Model Armor has screened.

    Fails closed. If the screening call itself failed we do not know whether the
    content is safe, and treating "unknown" as "fine" is how guardrails end up
    decorative.

    Prompt injection and malicious URIs are DENY: the content is an attack and must
    never enter a model's context. PII is QUARANTINE rather than DENY, because a
    questionnaire cell containing a customer's data is a handling problem, not an
    attack -- we keep it, mark it, and route it to a human.
    """
    if verdict.execution_failed:
        return ArmorDecision.DENY

    if verdict.prompt_injection or verdict.malicious_uri:
        return ArmorDecision.DENY

    if verdict.responsible_ai:
        return ArmorDecision.QUARANTINE

    if verdict.sensitive_data:
        return ArmorDecision.QUARANTINE

    if verdict.matched:
        # A filter we do not model specifically still matched. Do not guess.
        return ArmorDecision.QUARANTINE

    return ArmorDecision.ALLOW


# ---------------------------------------------------------------------------------
# Confidence: computed, never generated
# ---------------------------------------------------------------------------------


class ConfidenceSignals(_Frozen):
    """Observable signals a confidence rating is derived from.

    Every field is something we measured, not something a model asserted about itself.
    """

    #: How many citations the answer carries.
    citation_count: int = Field(default=0, ge=0)
    #: Best retrieval score among them, 0..1.
    max_retrieval_score: Score = 0.0
    #: Mean retrieval score across them, 0..1.
    mean_retrieval_score: Score = 0.0
    #: The drafting agent hedged ("may", "we believe", "typically"). Detected by the
    #: assembler with a lexical check, not by asking the model how sure it is.
    agent_hedged: bool = False
    #: A prior-round commitment may be contradicted.
    contradiction: ContradictionVerdict = ContradictionVerdict.NO_CONTRADICTION
    #: The question spans departments, so no single scoped agent saw the whole picture.
    cross_departmental: bool = False


# -----------------------------------------------------------------------------------
# Thresholds, calibrated against a measured distribution rather than assumed.
#
# These were originally 0.55 / 0.75 / 0.60, chosen against RANK-DERIVED scores -- a scale
# on which the top hit always read 0.95 and which therefore meant nothing. Retrieval now
# scores by cosine similarity between the question and the best-matching sections of the
# retrieved documents (`attestor_platform.search.relevance`), and cosine over
# `text-embedding-005` occupies a much narrower band: same-domain policy prose scores
# ~0.6 even when irrelevant. Carrying the old constants across would have marked almost
# every answer LOW.
#
# Measured over the 63 hand-labelled retrieval pairs against the 46-document corpus
# (docs/proof/confidence-calibration.json), on the two distributions this function
# actually consumes -- per-QUESTION max and mean across the cited passages:
#
#   per-question max   p05 0.57 · p25 0.64 · p50 0.69 · p75 0.72 · p95 0.75
#   per-question mean  p05 0.54 · p25 0.59 · p50 0.63 · p75 0.66 · p95 0.68
#
# Each threshold sits at a named percentile of a measured distribution rather than at a
# round number:
# -----------------------------------------------------------------------------------

#: p05 of the per-question best score. Below the level at which a question whose answer
#: WAS retrieved ever lands, retrieval essentially missed and the model is improvising.
_WEAK_SCORE = 0.57
#: Median per-question best score. A single hit at least this good stands on its own.
_STRONG_MAX_SCORE = 0.69
#: p25 of the per-question mean. A citation set averaging above this is corroboration
#: rather than noise.
_STRONG_MEAN_SCORE = 0.59
#: Corroboration: two independent documents agreeing beats one strong hit.
_CORROBORATING_CITATIONS = 2


def compute_confidence(signals: ConfidenceSignals) -> Confidence:
    """Derive a confidence rating deterministically from observable signals.

    Never ask a model how confident it is. Self-reported LLM confidence is
    uncalibrated, and a judge who knows the field will recognise it instantly. A
    deterministic function over retrieval scores and structural facts is defensible,
    reproducible, and testable.

    The thresholds, and why each is where it is:

    * **No citations -> LOW.** Nothing supports the claim. Structurally such an answer
      must also be flagged, so this is belt and braces.
    * **Any contradiction signal -> LOW.** Contradicting what we told the customer in a
      previous round is the one failure that loses an audit outright. Even
      ``POSSIBLE_CONTRADICTION`` and ``UNKNOWN`` cap at LOW: if the check could not run,
      we have no evidence of consistency, and absence of evidence is not consistency.
    * **Weak best score -> LOW.** If even the best-matching passage scores below
      ``_WEAK_SCORE``, retrieval essentially missed and the model is improvising.
    * **HIGH requires corroboration or a strong single hit, and no hedging.** Two or
      more citations with a solid mean, or one clearly strong hit. Hedged language means
      the drafting agent itself signalled doubt in the text, which we honour.
    * **Cross-departmental caps at MEDIUM.** No single scoped agent saw the whole
      picture, so nobody is in a position to be highly confident.
    * **Everything else -> MEDIUM.**

    ``requires_human`` then escalates LOW answers, so the effect of this function is to
    decide what reaches a human -- which is why it is deterministic.
    """
    if signals.citation_count == 0:
        return Confidence.LOW

    if signals.contradiction is not ContradictionVerdict.NO_CONTRADICTION:
        return Confidence.LOW

    if signals.max_retrieval_score < _WEAK_SCORE:
        return Confidence.LOW

    strong_single = signals.max_retrieval_score >= _STRONG_MAX_SCORE
    corroborated = (
        signals.citation_count >= _CORROBORATING_CITATIONS
        and signals.mean_retrieval_score >= _STRONG_MEAN_SCORE
    )

    if (strong_single or corroborated) and not signals.agent_hedged:
        if signals.cross_departmental:
            return Confidence.MEDIUM
        return Confidence.HIGH

    return Confidence.MEDIUM


# ---------------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------------


class AnswerFacts(_Frozen):
    """The subset of an answer this decision depends on.

    Taking facts rather than the `Answer` model keeps `policy` free of any coupling to
    how answers are stored, and makes these tests trivial to write.
    """

    confidence: Confidence
    citation_count: int = Field(default=0, ge=0)
    flagged_no_evidence: bool = False
    quarantined: bool = False
    contradiction: ContradictionVerdict = ContradictionVerdict.NO_CONTRADICTION
    #: Question touches a commitment made in an earlier round.
    touches_prior_commitment: bool = False


def requires_human(facts: AnswerFacts, prior_commitments: int = 0) -> bool:
    """Decide whether an answer must be held for human approval.

    Escalates when: confidence is LOW, there is no supporting evidence, Model Armor
    quarantined it, a prior-round commitment may be contradicted, or the question
    touches a commitment we have made before and there are commitments on file to
    contradict.

    The last clause is the consistency guarantee. Answering round 2 inconsistently with
    round 1 fails the audit, so any answer in the blast radius of an earlier promise
    gets a human look even when every other signal is green.
    """
    if facts.flagged_no_evidence or facts.quarantined:
        return True

    if facts.confidence is Confidence.LOW:
        return True

    if facts.contradiction is not ContradictionVerdict.NO_CONTRADICTION:
        return True

    if facts.touches_prior_commitment and prior_commitments > 0:
        return True

    # Kept as an explicit branch rather than `return facts.citation_count == 0` so the
    # escalation ladder reads as a list of parallel reasons to escalate.
    if facts.citation_count == 0:  # noqa: SIM103
        return True

    return False


# ---------------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------------

#: Which regions satisfy each residency demand. Enforced as policy: the gateway
#: refuses to route to a non-conforming region and logs the refusal.
_RESIDENCY_REGIONS: dict[Residency, frozenset[str]] = {
    Residency.US: frozenset({"us", "us-central1", "us-east1", "us-east4", "us-west1"}),
    Residency.EU: frozenset(
        {"eu", "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west9"}
    ),
    Residency.IN: frozenset({"asia-south1"}),
}


def residency_permits(residency: Residency, region: str) -> bool:
    """Return whether processing in ``region`` satisfies the review's residency demand.

    ``Residency.ANY`` permits everything. An unknown region is refused rather than
    permitted: a residency check that fails open is not a residency check.
    """
    if residency is Residency.ANY:
        return True
    allowed = _RESIDENCY_REGIONS.get(residency)
    if allowed is None:  # pragma: no cover - guard for a future unmapped Residency
        # Unreachable today: `test_every_residency_is_mapped` asserts the table covers
        # every enum member. Kept so that adding a Residency without a region set fails
        # closed rather than silently permitting every region.
        return False
    return region.lower() in allowed
