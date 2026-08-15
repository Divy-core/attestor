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
from google.adk.models.google_llm import Gemini

# The hackathon brief names 3.5 Flash explicitly; 3.6 and 3.7 are available swaps.
MODEL_NAME = "gemini-3.5-flash"

# Every Gemini 3.x model is served ONLY from the `global` location. Calling any
# regional endpoint for one returns:
#   404 Publisher model `projects/<p>/locations/us-central1/publishers/google/models/
#   gemini-3.5-flash` was not found or your project does not have access to it.
# which reads as an entitlement problem and is not one -- `models.list()` happily
# lists all of them from us-central1, because listing the catalogue is not the same
# as being able to invoke it. Verified in docs/proof/PHASE-0-DISCOVERY.md 2.4:
# only 2.5-era models answer regionally; 3.x answers only on `global`.
#
# A fully-qualified `projects/.../locations/global/...` model path does NOT fix this
# -- the *client's* location picks the endpoint, so the client itself must be global.
# client_kwargs is the surgical fix: the model client goes to `global` while the
# reasoningEngine resource stays in us-central1 with everything else.
MODEL_LOCATION = "global"

MODEL = Gemini(model=MODEL_NAME, client_kwargs={"location": MODEL_LOCATION})

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
