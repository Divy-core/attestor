"""The deployable fleet: one module, five agents, five Agent Identities.

This replaces the Phase 0 probe (`runtime_app.py`). What ships to Agent Runtime is the
*drafting fleet* — the agents that actually call a model — while the dispatcher on Cloud
Run routes work between them by message. That split is deliberate and it is the same one
ADR-0002 argues for: the sequence between stages is known and belongs in a workflow, and
the model is reserved for the parts that are judgement.

## Why five engines rather than one with `sub_agents`

Agent Registry catalogues **deployed engines**. Sub-agents nested inside a single engine
are invisible to it, and — more importantly — they share one Agent Identity, which means
one service account, which means the union of every department's permissions on a single
credential. That is precisely the least-privilege violation the fleet exists to avoid.

Deploying each department as its own engine gives each one a distinct identity that IAM
can scope independently, so `SecurityAgent` cannot read `corpus/legal/**` because the
*credential* is refused — not because a prompt asked it not to, and not only because the
`before_tool` interceptor said no. Three layers, and this module is what makes the third
one possible.

## The module name

Not `app.py`. The Agent Runtime container has its own top-level `app` package, and
cloudpickle resolves tool functions by module reference, so a bundle module with that name
unpickles against Google's package and the engine dies at startup with an error naming
none of this. `tools/check_layering.py` enforces the ban; the Phase 0 discovery has the
full failure transcript.
"""

from __future__ import annotations

import os
from typing import Any

from attestor_core.domain import Department
from attestor_platform.config import REASONING_MODEL, gemini_model

#: Which department an engine is bound to. Read at *build* time, not at request time:
#: cloudpickle bakes the constructed agent into the bundle, so the binding is fixed at
#: deploy and cannot be swapped by an environment variable on a running instance. That is
#: the point — an engine's scope is a deployment fact, not a runtime argument.
AGENT_ROLE = os.environ.get("AGENT_ROLE", "orchestrator")

DEPARTMENT_ROLES: dict[str, Department] = {
    "security": Department.SECURITY,
    "legal": Department.LEGAL,
    "engineering": Department.ENGINEERING,
}

#: Display names, and therefore Agent Registry entries.
ROLES: tuple[str, ...] = ("orchestrator", "security", "legal", "engineering", "evidence")


# ---------------------------------------------------------------------------------
# Tools. Each is a plain function with a docstring, because ADK derives the tool
# schema from the signature and the docstring -- they are the contract, not decoration.
# ---------------------------------------------------------------------------------


def _search(department: Department, question: str) -> dict[str, Any]:
    """Retrieve evidence for one question from one department's corpus.

    Shared by the department agents and the evidence agent. The department is bound by
    the caller, never taken from the model: a tool that let the model choose its own
    corpus would make the scoping advisory.
    """
    from attestor_platform.search import ExpandingCorpusSearch

    result = ExpandingCorpusSearch(department).retrieve(question)
    return {
        "department": department.value,
        "passages": [
            {
                "document": item.document_title,
                "section": item.section,
                "uri": item.document_uri,
                "score": round(item.score, 4),
                "text": item.content[:1200],
            }
            for item in result.evidence
        ],
        "queries_run": list(result.queries_run),
    }


def search_security_corpus(question: str) -> dict[str, Any]:
    """Search the security policy corpus for evidence answering a question.

    Args:
        question: The vendor-review question to find evidence for.

    Returns:
        Retrieved passages with their document, section, URI, and relevance score.
    """
    return _search(Department.SECURITY, question)


def search_legal_corpus(question: str) -> dict[str, Any]:
    """Search the legal and privacy corpus for evidence answering a question.

    Args:
        question: The vendor-review question to find evidence for.

    Returns:
        Retrieved passages with their document, section, URI, and relevance score.
    """
    return _search(Department.LEGAL, question)


def search_engineering_corpus(question: str) -> dict[str, Any]:
    """Search the engineering and infrastructure corpus for evidence.

    Args:
        question: The vendor-review question to find evidence for.

    Returns:
        Retrieved passages with their document, section, URI, and relevance score.
    """
    return _search(Department.ENGINEERING, question)


def search_any_corpus(department: str, question: str) -> dict[str, Any]:
    """Search a named department's corpus. Used by the shared evidence agent.

    Args:
        department: One of `security`, `legal`, `engineering`.
        question: The vendor-review question to find evidence for.

    Returns:
        Retrieved passages, or an error describing which departments exist.
    """
    resolved = DEPARTMENT_ROLES.get(department.strip().lower())
    if resolved is None:
        return {"error": f"unknown department {department!r}", "known": sorted(DEPARTMENT_ROLES)}
    return _search(resolved, question)


def recall_commitments(review_id: str) -> dict[str, Any]:
    """Recall what was promised to this customer in earlier rounds.

    Args:
        review_id: The review whose prior commitments to load.

    Returns:
        The commitments on file, from Memory Bank.
    """
    from attestor_platform.memory import MemoryBankCommitments

    engine_id = os.environ.get("AGENT_ENGINE_ID", "")
    if not engine_id:
        # Deliberately an error rather than an empty list. "No prior commitments" is a
        # claim the consistency check acts on, and a misconfigured engine id must not be
        # able to make it -- see attestor_core.errors.ContextUnavailable.
        return {"error": "AGENT_ENGINE_ID is not configured; cannot read Memory Bank"}
    pairs = MemoryBankCommitments(engine_id=engine_id).for_review(review_id)
    return {"commitments": [{"question_id": q, "statement": s} for q, s in pairs]}


# ---------------------------------------------------------------------------------
# Instructions. Byte-stable: no timestamps, no counts, no ordering that varies.
# ---------------------------------------------------------------------------------

_DEPARTMENT_INSTRUCTION = """\
You are the {title} specialist in the Attestor fleet, answering vendor security review \
questions for Kestrel Data.

You may search ONLY the {department} corpus. You have no access to any other department's \
documents and must not speculate about their contents.

For every question:
1. Search the corpus before answering. Never answer from memory.
2. Ground every claim in a retrieved passage and cite it by document and section.
3. If the retrieved passages do not support an answer, reply exactly \
INSUFFICIENT_EVIDENCE. A confident answer with no evidence behind it is the single worst \
outcome in this system -- worse than no answer.
4. Answer in the company's voice, in prose, without hedging language.
"""

_EVIDENCE_INSTRUCTION = """\
You are the shared Evidence agent in the Attestor fleet. You retrieve, you do not opine.

Given a question and a department, search that department's corpus and return the \
passages that bear on it, with their document, section, and URI. Do not draft an answer, \
do not summarise beyond what the passages say, and do not fill gaps from your own \
knowledge. Provenance is the product.
"""


def _title(role: str) -> str:
    return {"engineering": "Engineering", "legal": "Legal & Privacy", "security": "Security"}[role]


def build_agent(role: str) -> Any:
    """Construct the agent for one role. Called at deploy time, pickled into the bundle.

    Raises:
        ValueError: If the role is not one this fleet deploys.
    """
    from google.adk.agents import LlmAgent

    if role in DEPARTMENT_ROLES:
        tool = {
            "security": search_security_corpus,
            "legal": search_legal_corpus,
            "engineering": search_engineering_corpus,
        }[role]
        return LlmAgent(
            name=f"{role}_agent",
            model=gemini_model(REASONING_MODEL),
            description=(
                f"Department-scoped drafter for {role} vendor-review questions. "
                f"Bound to the {role} corpus only."
            ),
            instruction=_DEPARTMENT_INSTRUCTION.format(title=_title(role), department=role),
            tools=[tool, recall_commitments],
        )

    if role == "evidence":
        return LlmAgent(
            name="evidence_agent",
            model=gemini_model(REASONING_MODEL),
            description="Shared retrieval agent. Returns cited passages, never opinions.",
            instruction=_EVIDENCE_INSTRUCTION,
            tools=[search_any_corpus],
        )

    if role == "orchestrator":
        # The orchestrator's judgement prompt and turn cap live in the fleet package,
        # which is where they are tested. Importing rather than restating keeps one
        # copy of the instruction that the byte-stability test actually pins.
        from attestor_fleet.orchestrator import ROOT_AGENT_INSTRUCTION

        return LlmAgent(
            name="orchestrator",
            model=gemini_model(REASONING_MODEL),
            description=(
                "Root judgement for a vendor security review: which pipeline to run, "
                "what to retry, when to escalate to a human, and when to stop."
            ),
            instruction=ROOT_AGENT_INSTRUCTION,
            tools=[search_any_corpus, recall_commitments],
        )

    raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")


#: What `deploy.py` picks up when no role is passed explicitly.
root_agent = build_agent(AGENT_ROLE)
