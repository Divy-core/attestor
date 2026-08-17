"""Typed settings and the single Gemini model factory.

**This module is the only place in the codebase where a model string is written or a
Gemini/ADK model client is constructed.** `tools/check_layering.py` enforces both
mechanically. That is not stylistic: the `location="global"` trap below cost a full
diagnose-fix-rerun cycle in Phase 0, and a constant that can be bypassed is a constant
that will be bypassed at 2am on day 14.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Final

# ---------------------------------------------------------------------------------
# Model tiering. Three tiers, one place.
# ---------------------------------------------------------------------------------

#: Drafting, intake, multimodal parse. Verified invocable 14 Aug 2026.
REASONING_MODEL: Final = "gemini-3.7-flash"
#: Used if the primary is unavailable or unstable. Both satisfy the hackathon's
#: "Gemini 3.5 or newer" requirement, so falling back costs nothing in eligibility.
REASONING_FALLBACK: Final = "gemini-3.5-flash"
#: High-volume classification: ~300 cheap triage calls against ~40 expensive drafts.
#: No 3.6 or 3.7 Flash-Lite exists as of 14 Aug 2026 (measured, see PROGRESS.md), so
#: 3.5-lite is the current floor of the cheap tier.
TRIAGE_MODEL: Final = "gemini-3.5-flash-lite"

#: Retrieval relevance. Discovery Engine's standard edition returns no relevance score,
#: so one is measured here instead: cosine similarity between the question and the
#: retrieved passage. 768 dimensions, asymmetric query/document task types, verified
#: invocable from `global` on 16 Aug 2026 alongside `gemini-embedding-001` (3072-dim) and
#: `text-multilingual-embedding-002`. The 768-dim model is chosen deliberately -- four
#: times the vector for no measured gain in separation is four times the latency.
EMBEDDING_MODEL: Final = "text-embedding-005"

#: PHASE 0 FINDING — NON-NEGOTIABLE.
#: Every Gemini 3.x model is served ONLY from `global`. A regional call returns
#:   404 Publisher model projects/<p>/locations/us-central1/publishers/google/models/
#:   gemini-3.5-flash was not found or your project does not have access to it
#: which reads as an entitlement problem and is not one. Worse, `models.list()` from a
#: region cheerfully lists models it cannot invoke, so "it's in the list" proves
#: nothing. A fully-qualified `.../locations/global/...` model path does NOT fix it,
#: because the *client's* location selects the endpoint.
GEMINI_LOCATION: Final = "global"

#: PHASE 0 FINDING. Model Armor's regional operations are served from a regional
#: endpoint. The global host answers `global` only and returns a misleading
#: `403 PERMISSION_DENIED: Read access to project ... was denied` for every regional
#: location, even to a project Owner with the API enabled.
MODEL_ARMOR_ENDPOINT_TEMPLATE: Final = "https://modelarmor.{region}.rep.googleapis.com"

#: Everything except the Gemini client is pinned here.
DEFAULT_REGION: Final = "us-central1"

#: Published per-million-token prices, (input, output) in USD. Lives here with the model
#: constants because `check_layering.py` forbids model strings anywhere else -- and that
#: rule caught this table sitting in the fleet package, which is exactly its job.
#: Used only for an in-run cost estimate, so the demo cost number is derived from a
#: stated rate rather than guessed.
PRICE_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    REASONING_MODEL: (0.30, 2.50),
    REASONING_FALLBACK: (0.30, 2.50),
    TRIAGE_MODEL: (0.10, 0.40),
    #: Embeddings are priced per input token and produce no output tokens. Published
    #: rate for `text-embedding-005`; the output price is 0 so a mis-recorded output
    #: count cannot inflate the reported cost.
    EMBEDDING_MODEL: (0.025, 0.0),
}
FALLBACK_PRICE: Final[tuple[float, float]] = (0.30, 2.50)


def model_armor_endpoint(region: str | None = None) -> str:
    """Return the regional Model Armor endpoint. Never call the global host regionally."""
    return MODEL_ARMOR_ENDPOINT_TEMPLATE.format(region=region or default_region())


def project_id() -> str:
    """The GCP project. Fail fast rather than defaulting to something wrong."""
    value = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not value:
        raise RuntimeError("PROJECT_ID (or GOOGLE_CLOUD_PROJECT) must be set")
    return value


def default_region() -> str:
    return os.environ.get("REGION", DEFAULT_REGION)


#: The engine Memory Bank hangs off. Not a model or a region, but the same kind of fact:
#: one value that several callers need and none of them should carry their own copy of.
#:
#: This is here because the copies drifted. Memory Bank is addressed by a `reasoningEngine`
#: resource, and Phase 5 migrated the commitments off the Phase 0 probe engine
#: (`8598754324522205184`) onto the deployed orchestrator. The dispatcher was updated. Five
#: tools were not, including `seed.py` — so seeding without `AGENT_ENGINE_ID` in the
#: environment would have written the round-1 commitments to the abandoned engine while the
#: dispatcher read the live one, and the 22-day resume would have woken with no history at
#: all. The resume harness checks the commitment *count* for exactly this reason, so it
#: would have been caught; it should not have needed catching.
MEMORY_BANK_ENGINE_ID: Final = "511608807718125568"


def memory_bank_engine_id() -> str:
    """Which engine holds this project's commitments. Overridable, single-sourced."""
    return os.environ.get("AGENT_ENGINE_ID") or MEMORY_BANK_ENGINE_ID


# ---------------------------------------------------------------------------------
# The one factory
# ---------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def gemini_model(model_name: str = REASONING_MODEL) -> Any:
    """Build an ADK Gemini model, always pinned to the `global` location.

    **The only sanctioned way to construct a Gemini model anywhere in Attestor.**
    Constructing one directly bypasses the location pin and produces a 404 that looks
    like a permissions problem.

    Args:
        model_name: One of `REASONING_MODEL`, `REASONING_FALLBACK`, `TRIAGE_MODEL`.

    Returns:
        A `google.adk.models.google_llm.Gemini` bound to `location="global"`.
    """
    from google.adk.models.google_llm import Gemini

    return Gemini(model=model_name, client_kwargs={"location": GEMINI_LOCATION})


def reasoning_model() -> Any:
    """The drafting/intake tier."""
    return gemini_model(REASONING_MODEL)


def triage_model() -> Any:
    """The cheap high-volume classification tier."""
    return gemini_model(TRIAGE_MODEL)


@lru_cache(maxsize=2)
def genai_client(location: str = GEMINI_LOCATION) -> Any:
    """Build a raw google-genai client, also pinned to `global` by default.

    Used for direct `generate_content` calls that do not go through ADK -- the
    contradiction check in Phase 3, for instance.
    """
    from google import genai

    return genai.Client(vertexai=True, project=project_id(), location=location)
