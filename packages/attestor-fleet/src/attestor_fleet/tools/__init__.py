"""Tools the fleet may call, and the one it may not.

This package is empty, and that is the state it is meant to be read in. Every tool the
fleet uses today is a bound method on a session object next to the agent that calls it --
`RunSession.execute_pipeline`, the department retrieval tools, the verifier's passage
reader. There is no registry to browse because there is nothing general enough to need one.

What lives here is the rule that governs anything added later.

## Attestor never reaches the web to answer a question

A questionnaire asks *"do you encrypt customer data at rest?"*. The answer is a fact about
the company Attestor works for, and the only place that fact legitimately exists is that
company's own corpus. An agent that searched the web for it would be **guessing about its
own employer**, and the failure mode is not an obvious blank: it is a fluent, plausible,
well-cited answer sourced from a competitor's trust page, a marketing site, or a five-year-
old blog post -- returned to a customer under this company's name, with a citation that
makes it look more trustworthy than an honest refusal.

`flagged_no_evidence` is the correct answer to a question the corpus cannot support. The
whole escalation ladder exists to produce it, and a web tool would quietly convert every
one of those into a confident answer nobody can stand behind.

## What research *is* allowed, and where

Learning about the **customer** at intake: who they are, what framework they sent, what
industry they are in. That informs triage and the covering note, and none of it becomes an
answer. The boundary is not the tool, it is the destination: research may shape *how the
work is routed*, and may never become the text of an answer or a citation on one.

Anything added to this package must state which side of that line it is on, and
`tests/unit/test_no_web_answers.py` fails the build if a retrieval or drafting agent
acquires a tool that can reach the network for answer content.
"""

#: Substrings that mark a tool as reaching outside the corpus.
#:
#: Matched against tool names by the test rather than by anything at runtime -- a runtime
#: check would be a guard the fleet could be reconfigured past, and this is a property of
#: the codebase rather than of a request.
WEB_TOOL_MARKERS: tuple[str, ...] = (
    "google_search",
    "web_search",
    "websearch",
    "browse",
    "fetch_url",
    "http_get",
    "url_context",
    "grounding_with_google",
)

#: The agents that produce answer text or citations. None of them may hold a web tool.
ANSWERING_AGENTS: tuple[str, ...] = (
    "SecurityAgent",
    "LegalAgent",
    "EngineeringAgent",
    "EvidenceAgent",
    "VerifierAgent",
)

__all__ = ["ANSWERING_AGENTS", "WEB_TOOL_MARKERS"]
