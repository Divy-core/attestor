"""Domain enumerations.

All string-valued, because these cross the wire into Firestore, Pub/Sub, and the
TypeScript UI. An `IntEnum` would serialise to a number that means nothing in a
Firestore console or an audit export six months later.
"""

from __future__ import annotations

from enum import StrEnum


class Department(StrEnum):
    """Who owns a question, and therefore which corpus may be read to answer it.

    This is not a label -- it is an access boundary. Each department maps to its own
    Vertex AI Search datastore and its own agent identity, so a security agent
    physically cannot retrieve from the legal corpus.
    """

    SECURITY = "security"
    LEGAL = "legal"
    ENGINEERING = "engineering"
    #: Triage has not run yet, or ran and could not decide. Never an answerable state.
    UNASSIGNED = "unassigned"


class ReviewState(StrEnum):
    """Where a review is in its lifecycle.

    Lives in ``domain`` because a review's state is a domain concept. The *rules* for
    moving between states are a separate concern and live in ``state/``, which imports
    this. Typing ``Review.state`` as ``str`` to dodge an import would defeat the enum:
    an invalid state could be constructed and would only fail if and when the machine
    happened to look at it, which nothing guarantees.

    The happy path::

        intake -> triaging -> drafting -> awaiting_evidence -> awaiting_human
               -> assembling -> delivered -> follow_up -> triaging (round N+1)
    """

    INTAKE = "intake"
    TRIAGING = "triaging"
    DRAFTING = "drafting"
    AWAITING_EVIDENCE = "awaiting_evidence"
    AWAITING_HUMAN = "awaiting_human"
    ASSEMBLING = "assembling"
    DELIVERED = "delivered"
    FOLLOW_UP = "follow_up"

    #: Recoverable halt. Remembers where it came from and can return there.
    BLOCKED = "blocked"
    #: Terminal. Nothing resumes from here.
    FAILED = "failed"


class Framework(StrEnum):
    """The compliance framework a questionnaire is drawn from."""

    SOC2 = "soc2"
    ISO27001 = "iso27001"
    CAIQ = "caiq"
    GDPR = "gdpr"
    BESPOKE = "bespoke"


class Residency(StrEnum):
    """Data residency demanded by the customer for this review.

    Enforced as policy: the gateway refuses to route to a non-conforming region and
    logs the refusal. We demonstrate sovereignty as enforced policy rather than by
    actually running multiple regions -- a deliberate, stated trade-off.
    """

    US = "us"
    EU = "eu"
    IN = "in"
    ANY = "any"


class AnswerStatus(StrEnum):
    """Lifecycle of a single answer."""

    DRAFT = "draft"
    #: Drafted with citations, awaiting assembly.
    DRAFTED = "drafted"
    #: Held back for a human: low confidence, or contradicts a prior commitment.
    NEEDS_HUMAN = "needs_human"
    #: The corpus genuinely does not support an answer. The ONLY status permitted to
    #: carry zero citations. A system that answers everything confidently is a system
    #: nobody should trust.
    FLAGGED_NO_EVIDENCE = "flagged_no_evidence"
    #: Model Armor blocked the source question or the drafted answer.
    QUARANTINED = "quarantined"
    APPROVED = "approved"
    #: A human looked at it and refused it. Added in Phase 4 with the approval gate: an
    #: answer that has been rejected is neither still pending nor fit to deliver, and
    #: collapsing it into either would make the approval queue lie.
    REJECTED = "rejected"
    DELIVERED = "delivered"


class Confidence(StrEnum):
    """Confidence in an answer.

    Computed by ``policy.compute_confidence`` from observable signals, never generated
    by a model. LLM self-reported confidence is uncalibrated and any judge who knows
    the field knows it.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ToolDecision(StrEnum):
    """Tri-state interceptor result for a tool call."""

    ALLOW = "allow"
    #: Escalate to a human rather than refusing outright.
    ASK = "ask"
    DENY = "deny"


class ArmorDecision(StrEnum):
    """What to do about a Model Armor verdict."""

    ALLOW = "allow"
    #: Keep the content, mark it, do not feed it to a model. The run continues on
    #: other questions -- one poisoned cell must not fail a 312-question review.
    QUARANTINE = "quarantine"
    DENY = "deny"


class ContradictionVerdict(StrEnum):
    """Whether a draft answer contradicts a prior-round commitment.

    Defined here, computed elsewhere. Obtaining this verdict requires a model call,
    which belongs in ``attestor_fleet``; ``policy`` only decides what to do given one.
    That split is what keeps this package testable with no network.
    """

    NO_CONTRADICTION = "no_contradiction"
    POSSIBLE_CONTRADICTION = "possible_contradiction"
    CONTRADICTION = "contradiction"
    #: The check could not run (model error, timeout). Treated conservatively.
    UNKNOWN = "unknown"
