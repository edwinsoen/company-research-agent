"""Memory access tools and stubs.

In Phase 1, memory tools return empty results to test graceful first-run degradation.
In Phase 5, these will be wired to VertexAiMemoryBankService.
"""

from typing import Any, Optional
from google.adk.tools.tool_context import ToolContext


def search_memory(
    query: str,
    company: str = "",
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Search long-term memory for prior briefs regarding a specific company.

    Args:
        query: Search query string.
        company: Target company name.
        tool_context: ADK tool context.

    Returns:
        dict: Prior briefs or empty marker if none found.
    """
    return {
        "has_prior": False,
        "changes": [],
        "prior_facts": [],
        "message": f"No prior brief found for company '{company or query}'.",
    }


def preload_memory(
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Preload user briefing preferences from long-term memory.

    Args:
        tool_context: ADK tool context.

    Returns:
        dict: Preloaded preferences.
    """
    prefs = {
        "focus_areas": [],
        "recipients": [],
    }
    if tool_context and hasattr(tool_context, "actions"):
        if "user_preferences" not in tool_context.actions.state_delta:
            tool_context.actions.state_delta["user_preferences"] = prefs

    return prefs


def initialize_briefing_session(
    company_input: str,
    focus_areas: Optional[list[str]] = None,
    recipients: Optional[list[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Record the target company name, focus areas, and recipients into session state.

    Args:
        company_input: Target company name extracted from user prompt.
        focus_areas: Optional list of specific topics to focus research on.
        recipients: Optional list of recipient email addresses.
        tool_context: ADK tool context.

    Returns:
        dict: Confirmation of initialized session state.
    """
    focus = focus_areas or []
    recips = recipients or []
    if tool_context and hasattr(tool_context, "actions"):
        tool_context.actions.state_delta["company_input"] = company_input
        tool_context.actions.state_delta["user_preferences"] = {
            "focus_areas": focus,
            "recipients": recips,
        }
    return {
        "status": "initialized",
        "company_input": company_input,
        "focus_areas": focus,
        "recipients": recips,
    }
