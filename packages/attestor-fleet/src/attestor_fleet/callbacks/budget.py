"""Turn, token, and cost ceilings.

A runaway loop is the only realistic way to burn the $150 credit. Budget alerts warn
*after* the money is spent; this stops it being spent. Every ceiling raises
`BudgetExceeded` rather than logging and continuing, because a budget you can exceed is
not a budget.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from attestor_core.errors import BudgetExceeded
from attestor_platform.config import FALLBACK_PRICE, PRICE_PER_MTOK

logger = logging.getLogger(__name__)

#: Hard ceiling on orchestrator turns for one review.
MAX_TURNS = 12
#: Token ceiling for one review. A 312-question run at ~2.5k tokens per drafted answer
#: plus triage lands well under this; anything approaching it is a loop.
MAX_TOKENS = 2_000_000
#: Cost ceiling in USD for one review, as a last line of defence.
MAX_COST_USD = 5.0

# Prices live in attestor_platform.config alongside the model constants: the layering
# checker forbids model strings outside that module, and it caught this table here.


@dataclass
class BudgetLedger:
    """Tracks spend for one review. Thread-safe: drafting runs in parallel."""

    review_id: str = ""
    max_turns: int = MAX_TURNS
    max_tokens: int = MAX_TOKENS
    max_cost_usd: float = MAX_COST_USD

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Per-model token counts, so the cheap-triage cost story can be shown, not claimed.
    by_model: dict[str, tuple[int, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        total = 0.0
        for model, (tokens_in, tokens_out) in self.by_model.items():
            price_in, price_out = PRICE_PER_MTOK.get(model, FALLBACK_PRICE)
            total += (tokens_in / 1_000_000) * price_in
            total += (tokens_out / 1_000_000) * price_out
        return total

    def record_turn(self) -> None:
        """Count an orchestrator turn.

        Raises:
            BudgetExceeded: past the turn ceiling.
        """
        with self._lock:
            self.turns += 1
            if self.turns > self.max_turns:
                raise BudgetExceeded(
                    f"turn ceiling exceeded: {self.turns} > {self.max_turns}",
                    review_id=self.review_id,
                    turns=self.turns,
                )

    def record_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for one model call.

        Raises:
            BudgetExceeded: past the token or cost ceiling.
        """
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            previous_in, previous_out = self.by_model.get(model, (0, 0))
            self.by_model[model] = (previous_in + input_tokens, previous_out + output_tokens)

            if self.total_tokens > self.max_tokens:
                raise BudgetExceeded(
                    f"token ceiling exceeded: {self.total_tokens:,} > {self.max_tokens:,}",
                    review_id=self.review_id,
                    total_tokens=self.total_tokens,
                )

            cost = self.estimated_cost_usd
            if cost > self.max_cost_usd:
                raise BudgetExceeded(
                    f"cost ceiling exceeded: ${cost:.2f} > ${self.max_cost_usd:.2f}",
                    review_id=self.review_id,
                    estimated_cost_usd=round(cost, 4),
                )

    def summary(self) -> dict[str, object]:
        """Reportable spend, for PROGRESS.md and the demo numbers."""
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            # Sorted so the output is byte-stable between runs.
            "by_model": {
                model: {"input": self.by_model[model][0], "output": self.by_model[model][1]}
                for model in sorted(self.by_model)
            },
        }
