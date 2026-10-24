"""The cost of a request to the assistant: every paid call, written to the spend and added up.

A request can make several: Jev's decision, the language model's answer, a web search. Each is one
row of the spend with the purpose "agent", so the monthly limit counts them, and the total is what
the turn cost, for the history. The memory's own calls (folding the conversation into its summary,
the reflection pass) are written the same way, with the purpose "memory".
"""

import logging
import math

from ..brain.spend import SpendTracker

logger = logging.getLogger(__name__)

PURPOSE = "agent"


class Ledger:
    """One request's paid calls. Use it on the thread that owns the spend's database."""

    def __init__(self, spend: SpendTracker, purpose: str = PURPOSE) -> None:
        self._spend = spend
        self._purpose = purpose
        self.total = 0.0
        # The model of the latest paid call: the one that worded the answer, for the history.
        self.model: str | None = None
        self.calls: list[dict] = []

    def add(
        self,
        *,
        model: str | None,
        cost_usd: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ms: int = 0,
        outcome: str = "ok",
    ) -> None:
        """Write down a call that was paid for. A cost the spend refuses (negative, not a number)
        is counted as nothing, and a database that fails does not lose the request: both are
        logged by the type of the error. A cost that was reported is added to the total even when
        it could not be written, since it was spent."""
        if not (math.isfinite(cost_usd) and cost_usd >= 0):
            logger.warning("A cost the spend cannot take was counted as nothing: ValueError")
            return
        self.total += cost_usd
        self.calls.append({"model": model or "", "at_ms": self._spend._now_ms(), "ms": max(0, int(ms)),
                           "tokens": max(0, prompt_tokens) + max(0, completion_tokens),
                           "cost_usd": cost_usd, "outcome": outcome})
        if model:
            self.model = model
        try:
            self._spend.record(
                purpose=self._purpose,
                model=model,
                cost_usd=cost_usd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as exc:
            logger.warning("Could not record what the assistant spent: %s", type(exc).__name__)
