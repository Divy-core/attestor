"""Prompts for triage, drafting, assembly, and the consistency check.

Every `*_STATIC` constant is a module-level literal, which is the simplest possible way
to guarantee byte-stability: a constant cannot vary between turns.
"""

from __future__ import annotations

from attestor_core.domain import Department, Evidence
from attestor_fleet.prompts.base import build_prompt, render_list

# --------------------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------------------

TRIAGE_STATIC = """\
You classify vendor security questionnaire questions by which internal department owns \
the answer.

Departments:
- security: technical security controls, encryption, access control, vulnerabilities, \
incidents, monitoring, certifications (SOC 2, ISO 27001), penetration testing, vendor risk
- legal: data protection, GDPR, DPAs, subprocessors, contracts, privacy, data residency, \
retention, transfers, audit rights, liability
- engineering: architecture, availability, backups, disaster recovery, SDLC, change \
management, APIs, tenancy, cloud infrastructure, dependencies

Rules:
- Answer with exactly one department name per question, lowercase.
- Output one line per question, in the form: <index>|<department>
- Do not explain. Do not add commentary. Do not skip a question.
- If a question spans departments, choose the one that owns the PRIMARY evidence.
- If genuinely unclassifiable, use: unassigned"""


def triage_prompt(questions: list[tuple[int, str]]) -> str:
    """Batch-classify questions. `questions` is [(index, text), ...]."""
    lines = [f"{index}. {text}" for index, text in questions]
    return build_prompt(TRIAGE_STATIC, "\n".join(lines))


# --------------------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------------------

_DRAFTING_RULES = """\
You draft answers to vendor security questionnaires for Kestrel Data, Inc.

Absolute rules:
- Answer ONLY from the evidence provided. Never use outside knowledge about Kestrel.
- If the evidence does not support an answer, reply with exactly: INSUFFICIENT_EVIDENCE
- Never invent a number, date, certificate ID, vendor name, or percentage.
- Cite by referring to the evidence numbers you used, e.g. [1] or [1][3].
- Be specific. Prefer the exact figure from the evidence over a paraphrase.
- Answer in 1-4 sentences. A vendor reviewer wants the fact, not an essay.
- Do not hedge if the evidence is clear. Do not overstate if it is not.
- If the honest answer is "no" or "we do not offer this", say so plainly."""

_DEPARTMENT_SCOPE = {
    Department.SECURITY: (
        "You are the Security specialist. You answer from the security corpus only: "
        "encryption, access control, vulnerability management, incident response, "
        "logging, certifications, penetration testing, vendor risk."
    ),
    Department.LEGAL: (
        "You are the Legal and Privacy specialist. You answer from the legal corpus "
        "only: data protection agreements, GDPR, subprocessors, transfers, retention, "
        "privacy, contractual commitments."
    ),
    Department.ENGINEERING: (
        "You are the Engineering specialist. You answer from the engineering corpus "
        "only: architecture, tenancy, availability, backup and restore, SDLC, change "
        "management, dependencies, secrets handling."
    ),
    Department.UNASSIGNED: (
        "You are a generalist specialist answering from whatever corpus was provided."
    ),
}


def drafting_static(department: Department) -> str:
    """The cacheable prefix for one department's drafter.

    One constant per department, so each drafter has its own stable cache entry.
    """
    return f"{_DEPARTMENT_SCOPE[department]}\n\n{_DRAFTING_RULES}"


def format_evidence(evidence: list[Evidence]) -> str:
    """Render retrieved evidence for the prompt, numbered for citation."""
    if not evidence:
        return "(no evidence retrieved)"
    blocks = []
    for index, item in enumerate(evidence, start=1):
        section = f" · {item.section}" if item.section else ""
        blocks.append(f"[{index}] {item.document_title}{section}\n    {item.content.strip()}")
    return "\n".join(blocks)


def drafting_prompt(department: Department, question: str, evidence: list[Evidence]) -> str:
    """Assemble a drafting prompt: static department prefix + this question's evidence."""
    dynamic = f"QUESTION:\n{question}\n\nEVIDENCE:\n{format_evidence(evidence)}"
    return build_prompt(drafting_static(department), dynamic)


# --------------------------------------------------------------------------------------
# Consistency check -- the obtaining side of the ContradictionVerdict port
# --------------------------------------------------------------------------------------

CONSISTENCY_STATIC = """\
You check whether a newly drafted answer contradicts a commitment made to the same \
customer in an earlier round of the same vendor security review.

Contradicting a prior commitment fails the audit. Being cautious here is correct.

Reply with exactly one line, one of:
- NO_CONTRADICTION
- POSSIBLE_CONTRADICTION
- CONTRADICTION

Then, on a second line, one short sentence of justification.

Guidance:
- CONTRADICTION: the draft asserts something the commitment rules out, or offers \
something the commitment says is not offered.
- POSSIBLE_CONTRADICTION: the draft could be read as inconsistent, or hedges in a way \
that undercuts the commitment.
- NO_CONTRADICTION: the draft is consistent with, or unrelated to, the commitment."""


def consistency_prompt(draft: str, commitments: list[str]) -> str:
    """Compare a draft against prior-round commitments."""
    dynamic = (
        f"PRIOR COMMITMENTS:\n{render_list(commitments)}\n\nNEWLY DRAFTED ANSWER:\n{draft.strip()}"
    )
    return build_prompt(CONSISTENCY_STATIC, dynamic)


# --------------------------------------------------------------------------------------
# Hedging detection -- lexical, never a model call
# --------------------------------------------------------------------------------------

#: Phrases that signal the drafter was not confident. Detected lexically because asking
#: a model how sure it is produces an uncalibrated number, and because this must be
#: deterministic to keep `compute_confidence` reproducible.
HEDGE_MARKERS: tuple[str, ...] = (
    "may ",
    "might ",
    "we believe",
    "typically",
    "generally",
    "in most cases",
    "should be",
    "is expected to",
    "appears to",
    "it is likely",
    "we aim to",
    "where possible",
    "as far as we know",
    "to our knowledge",
)


def is_hedged(text: str) -> bool:
    """True when the drafted answer contains hedging language."""
    lowered = f" {text.lower()} "
    return any(marker in lowered for marker in HEDGE_MARKERS)
