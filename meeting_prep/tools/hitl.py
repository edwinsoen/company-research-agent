"""Human-In-The-Loop (HITL) tools.

Implements LongRunningFunctionTool gates for entity disambiguation and brief approval.
Source: docs/hld.md §10.2
"""

from typing import Any, Optional
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools.tool_context import ToolContext


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
    return None


_request_disambiguation.__name__ = "request_disambiguation"
request_disambiguation = LongRunningFunctionTool(_request_disambiguation)


