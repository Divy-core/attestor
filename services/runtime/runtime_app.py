"""The smallest real Attestor agent, for proving Agent Runtime works end to end.

This is Phase 0 proof-of-life scaffolding, NOT the fleet. The real agents
(Orchestrator, Intake, Security, Legal, Evidence, Assembler) are built in
`packages/attestor-fleet` in Phase 3. Nothing here should grow.

It exists to answer five questions with evidence:
  1. Does a reasoningEngine resource deploy at all?
  2. Does a query round-trip?
  3. Does *tool calling* work -- not just text generation?
  4. Does the agent get a distinct Agent Identity?
  5. Do spans reach Cloud Trace?

Hence exactly one tool. A text-only agent would prove none of question 3.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

# Confirmed available on this project in docs/proof/PHASE-0-DISCOVERY.md section 2.4.
# The hackathon brief names 3.5 Flash explicitly; 3.6 and 3.7 are available swaps.
MODEL = "gemini-3.5-flash"

#: Deliberately a constant. The demo narrative uses 312 questions, so the number is
#: recognisable in the trace, and a constant makes "did the tool actually run?"
#: unambiguous -- the model cannot produce this by inference.
OPEN_REVIEW_COUNT = 312


def get_review_count() -> int:
    """Return the number of vendor security review questions currently open.

    Returns:
        The count of open questions across all in-flight vendor security reviews.
    """
    return OPEN_REVIEW_COUNT


root_agent = LlmAgent(
    name="attestor_probe",
    model=MODEL,
    description="Phase 0 proof-of-life probe for the Attestor fleet.",
    instruction=(
        "You are the Attestor deployment probe. "
        "When asked how many review questions are open, you MUST call the "
        "get_review_count tool and report the number it returns. "
        "Never guess or infer the number. State it plainly, in one short sentence."
    ),
    tools=[get_review_count],
)
