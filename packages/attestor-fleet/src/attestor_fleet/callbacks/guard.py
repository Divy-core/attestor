"""Model Armor screening and the deny/ask/allow tool interceptor.

Three surfaces, because there are three ways poisoned content reaches a model:

* **ingress** (`screen_prompt`) — the question text and assembled prompt. This is the
  obvious one; almost everyone implements it.
* **egress on tool output** (`screen_tool_output`) — retrieved corpus content, screened
  **before it enters context**. This is the tool-poisoning defence Track 3 names
  explicitly and the one almost nobody implements: an attacker who can get text into
  your corpus owns your agent otherwise.
* **egress on the drafted answer** (`screen_answer`) — before it leaves the system.

`platform.armor` obtains the verdict; `core.policy` decides on it. This module wires the
two together and records what happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from attestor_core.domain import ArmorDecision, Department, ToolDecision
from attestor_core.errors import ArmorBlocked, PolicyViolation
from attestor_core.policy import decide_on_armor_verdict, decide_tool
from attestor_platform.armor import ArmorClient, LongTextVerdict

logger = logging.getLogger(__name__)

#: Excerpt length recorded on a block. The full payload of a blocked document has no
#: business in an audit log or an SSE event.
EXCERPT_CHARS = 280


@dataclass(frozen=True)
class ScreenOutcome:
    """What screening decided, and enough detail to render and audit it."""

    decision: ArmorDecision
    surface: str
    matched_filters: tuple[str, ...] = ()
    chunk_index: int | None = None
    excerpt: str | None = None

    @property
    def blocked(self) -> bool:
        return self.decision is not ArmorDecision.ALLOW


def _outcome_from_long_text(result: LongTextVerdict, surface: str) -> ScreenOutcome:
    decision = decide_on_armor_verdict(result.verdict)
    first = result.first_match
    return ScreenOutcome(
        decision=decision,
        surface=surface,
        matched_filters=first.matched_filters if first else (),
        chunk_index=first.index if first else None,
        excerpt=(first.excerpt[:EXCERPT_CHARS] if first and first.excerpt else None),
    )


class ArmorGuard:
    """Screens the three surfaces. Construction binds one Armor client."""

    def __init__(self, armor: ArmorClient | None = None) -> None:
        self._armor = armor if armor is not None else ArmorClient()

    def screen_prompt(self, text: str) -> ScreenOutcome:
        """Ingress: question text or assembled prompt, before the model sees it.

        Uses the chunker unconditionally. A questionnaire cell can be arbitrarily long,
        and the injection filter only inspects ~512 tokens, so a single call would leave
        everything past that window unscreened.
        """
        return _outcome_from_long_text(self._armor.screen_long_text(text), "prompt")

    def screen_tool_output(self, text: str) -> ScreenOutcome:
        """Egress on retrieved content, BEFORE it enters model context.

        The tool-poisoning defence. Retrieved corpus documents are an untrusted input
        too: an attacker who can write into the corpus -- a compromised policy doc, a
        malicious PR to an internal wiki -- would otherwise have a direct channel into
        the model's instructions.
        """
        return _outcome_from_long_text(self._armor.screen_long_text(text), "tool_output")

    def screen_answer(self, text: str) -> ScreenOutcome:
        """Egress on the drafted answer, before it leaves the system."""
        return _outcome_from_long_text(
            self._armor.screen_long_text(text, egress=True), "draft_answer"
        )


def enforce_tool_policy(
    agent_department: Department,
    tool_name: str,
    resource_ref: str | None = None,
    agent_name: str = "",
) -> ToolDecision:
    """`before_tool` interceptor: ALLOW / ASK / DENY.

    A DENY raises. The demo beat is `SecurityAgent` reaching for the legal corpus and
    being refused -- and a refusal that merely logs and continues is not a refusal.

    Raises:
        PolicyViolation: on DENY.
    """
    decision = decide_tool(agent_department, tool_name, resource_ref)

    if decision is ToolDecision.DENY:
        logger.warning(
            "TOOL DENIED",
            extra={
                "agent": agent_name,
                "agent_department": agent_department.value,
                "tool_name": tool_name,
                "resource_ref": resource_ref,
            },
        )
        raise PolicyViolation(
            f"{agent_name or agent_department.value} may not call {tool_name!r} against "
            f"{resource_ref!r}: cross-department access is denied by least-privilege policy",
            agent=agent_name,
            agent_department=agent_department.value,
            tool_name=tool_name,
            resource_ref=resource_ref,
        )

    return decision


def raise_if_blocked(outcome: ScreenOutcome, *, question_id: str | None = None) -> None:
    """Turn a blocking verdict into an exception.

    Raises:
        ArmorBlocked: when the decision is not ALLOW.
    """
    if not outcome.blocked:
        return
    raise ArmorBlocked(
        f"Model Armor returned {outcome.decision.value} on {outcome.surface}",
        question_id=question_id,
        surface=outcome.surface,
        decision=outcome.decision.value,
        matched_filters=list(outcome.matched_filters),
        chunk_index=outcome.chunk_index,
    )
