"""BudgetPlugin: Token and model call budget tracking and ceiling enforcement.

Accumulates:
1. Model-call count per session.
2. Input, output, and total token usage from LLM response usage metadata.
3. Refinement iteration count tracking.

Aborts execution gracefully before model calls if configured ceilings are exceeded.
Source: docs/orchestration-and-logic-enhancements.md §2.2
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

logger = logging.getLogger(__name__)

DEFAULT_MAX_MODEL_CALLS = int(os.getenv("BUDGET_MAX_MODEL_CALLS", "25"))
DEFAULT_MAX_TOKENS = int(os.getenv("BUDGET_MAX_TOKENS", "150000"))


class BudgetExceededError(RuntimeError):
    """Raised by BudgetPlugin when model call or token ceilings are exceeded."""
    pass


class BudgetPlugin(BasePlugin):
    """ADK plugin enforcing resource limits and tracking LLM usage metrics."""

    def __init__(
        self,
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS,
        max_total_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        super().__init__(name="budget_plugin")
        self.max_model_calls = max_model_calls
        self.max_total_tokens = max_total_tokens

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        """Check budget ceilings before model invocation; aborts with BudgetExceededError if breached."""
        state = callback_context.state

        current_calls = int(state.get("budget_model_calls", 0))
        current_tokens = int(state.get("budget_total_tokens", 0))

        if current_calls >= self.max_model_calls:
            msg = (
                f"Execution terminated: Model-call ceiling exceeded "
                f"({current_calls} calls >= {self.max_model_calls} limit)."
            )
            logger.warning("BudgetPlugin CEILING BREACH: %s", msg, extra={"event_type": "budget_breach", "calls": current_calls})
            state["budget_breached"] = True
            state["budget_breach_message"] = msg
            raise BudgetExceededError(msg)

        if current_tokens >= self.max_total_tokens:
            msg = (
                f"Execution terminated: Total token ceiling exceeded "
                f"({current_tokens} tokens >= {self.max_total_tokens} limit)."
            )
            logger.warning("BudgetPlugin CEILING BREACH: %s", msg, extra={"event_type": "budget_breach", "tokens": current_tokens})
            state["budget_breached"] = True
            state["budget_breach_message"] = msg
            raise BudgetExceededError(msg)

        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Record model call and aggregate token metrics from LLM response."""
        state = callback_context.state

        # Increment call count
        calls = int(state.get("budget_model_calls", 0)) + 1
        state["budget_model_calls"] = calls

        # Track refinement iteration if routing or approval gate is active
        agent_name = getattr(callback_context, "agent_name", "")
        if agent_name == "refinement_router":
            iteration = int(state.get("refinement_iteration", 0)) + 1
            state["refinement_iteration"] = iteration

        # Accumulate tokens if usage metadata exists on response
        usage = getattr(llm_response, "usage_metadata", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
            candidate_tokens = getattr(usage, "candidates_token_count", 0) or 0
            total_tokens = getattr(usage, "total_token_count", 0) or (prompt_tokens + candidate_tokens)

            state["budget_input_tokens"] = int(state.get("budget_input_tokens", 0)) + prompt_tokens
            state["budget_output_tokens"] = int(state.get("budget_output_tokens", 0)) + candidate_tokens
            state["budget_total_tokens"] = int(state.get("budget_total_tokens", 0)) + total_tokens

            logger.debug(
                "BudgetPlugin usage: +%d tokens (total: %d tokens, %d calls)",
                total_tokens,
                state["budget_total_tokens"],
                calls,
                extra={
                    "event_type": "budget_usage",
                    "agent": agent_name,
                    "prompt_tokens": prompt_tokens,
                    "candidate_tokens": candidate_tokens,
                    "total_tokens": state["budget_total_tokens"],
                    "calls": calls,
                },
            )

        return None
