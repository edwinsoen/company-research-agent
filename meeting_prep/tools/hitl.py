"""Human-In-The-Loop (HITL) tools.

Implements LongRunningFunctionTool gates for entity disambiguation and brief approval.
Source: docs/hld.md §10.2
"""

import logging
from typing import Any, Optional
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools.tool_context import ToolContext
from meeting_prep.callbacks.telemetry import log_intent, log_outcome

logger = logging.getLogger(__name__)


def _approve_brief(
    draft: str,
    tool_context: Optional[ToolContext] = None,
) -> Optional[dict[str, Any]]:
    """Present the brief draft for review and capture the human approval decision.

    Args:
        draft: The generated markdown brief.
        tool_context: ADK tool context.

    Returns:
        None to pause execution as a LongRunningFunctionTool until human decision arrives.
    """
    log_intent(
        logger,
        "approve_brief",
        "Submitting executive brief draft for human review at HITL Gate 2",
        draft_chars=len(draft) if draft else 0,
    )
    log_outcome(
        logger,
        "approve_brief",
        "HITL Gate 2 paused: awaiting executive approval or revision comment",
        status="PENDING_HITL",
    )
    return None


_approve_brief.__name__ = "approve_brief"
approve_brief = LongRunningFunctionTool(_approve_brief)


def _request_disambiguation(
    candidates: list[dict[str, Any]],
    tool_context: Optional[ToolContext] = None,
) -> Optional[dict[str, Any]]:
    """Resolve an ambiguous company name to a single candidate when model confidence is low.

    Args:
        candidates: List of 2-3 candidate entity dictionaries, each with name, domain, and description.
        tool_context: ADK tool context.

    Returns:
        None to pause execution as a LongRunningFunctionTool until human selects a candidate.
    """
    candidate_names = [c.get("name", "") for c in candidates if isinstance(c, dict)]
    log_intent(
        logger,
        "request_disambiguation",
        f"Requesting human disambiguation among {len(candidates)} entity candidates at HITL Gate 1",
        candidate_count=len(candidates),
        candidates=candidate_names,
    )
    log_outcome(
        logger,
        "request_disambiguation",
        "HITL Gate 1 paused: awaiting user entity selection",
        status="PENDING_HITL",
    )
    return None


_request_disambiguation.__name__ = "request_disambiguation"
request_disambiguation = LongRunningFunctionTool(_request_disambiguation)


