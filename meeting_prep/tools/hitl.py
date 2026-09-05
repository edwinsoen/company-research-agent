"""Human-In-The-Loop (HITL) tools and stubs.

In Phase 1, approve_brief auto-approves to allow end-to-end execution in adk web,
escalating the LoopAgent after draft v1.
In Phase 2, this will be upgraded to LongRunningFunctionTool.
"""

from typing import Any, Optional
from google.adk.tools.tool_context import ToolContext


def approve_brief(
    draft: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Present the brief draft for review and capture the approval decision.

    Args:
        draft: The generated markdown brief.
        tool_context: ADK tool context for actions and state modifications.

    Returns:
        dict: Decision dictionary with status ('approved' or 'revise') and optional comment.
    """
    decision = {
        "status": "approved",
        "comment": None,
    }
    if tool_context and hasattr(tool_context, "actions"):
        # Escalate to terminate the LoopAgent on approval
        tool_context.actions.escalate = True
        tool_context.actions.state_delta["approval_decision"] = decision

    return decision


def request_disambiguation(
    candidates: list[dict[str, Any]],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Resolve an ambiguous company name to a single candidate.

    Args:
        candidates: List of candidate entity dictionaries.
        tool_context: ADK tool context.

    Returns:
        dict: Selected entity candidate.
    """
    selected = candidates[0] if candidates else {}
    if tool_context and hasattr(tool_context, "actions"):
        tool_context.actions.state_delta["resolved_entity"] = selected

    return selected
