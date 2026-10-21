"""The cost of a request to the assistant: every paid call, written to the spend and added up.

A request can make several: Jev's decision, the language model's answer, a web search. Each is one
row of the spend with the purpose "agent", so the monthly limit counts them, and the total is what
the turn cost, for the history.
"""

import logging
import math

from ..brain.spend import SpendTracker

logger = logging.getLogger(__name__)

PURPOSE = "agent"


class Ledger:
    """One request's paid calls. Use it on the thread that owns the spend's database."""

    def __init__(self, spend: SpendTracker) -> None:
        self._spend = spend
        self.total = 0.0
        # The model of the latest paid call: the one that worded the answer, for the history.
        self.model: str | None = None

    def add(
        self,
        *,
        model: str | None,
        cost_usd: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Write down a call that was paid for. A cost the spend refuses (negative, not a number)
        is counted as nothing, and a database that fails does not lose the request: both are
        logged by the type of the error. A cost that was reported is added to the total even when
        it could not be written, since it was spent."""
        if not (math.isfinite(cost_usd) and cost_usd >= 0):
            logger.warning("A cost the spend cannot take was counted as nothing: ValueError")
            return
        self.total += cost_usd
        if model:
            self.model = model
        try:
            self._spend.record(
                purpose=PURPOSE,
                model=model,
                cost_usd=cost_usd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as exc:
            logger.warning("Could not record what the assistant spent: %s", type(exc).__name__)
