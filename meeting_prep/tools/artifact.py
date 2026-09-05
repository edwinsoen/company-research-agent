"""Artifact saving tools for executive brief drafts.

Source: docs/hld.md §7.2, §8, §9.2
"""

from typing import Any, Optional
from google.adk.tools.tool_context import ToolContext
from google.genai import types


async def save_draft_artifact(
    brief_draft: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Save the generated executive brief draft as a versioned artifact and record state.

    Args:
        brief_draft: Complete markdown brief text.
        tool_context: ADK tool context.

    Returns:
        dict: Summary containing draft_version, filename, and status.
    """
    current_version = 0
    if tool_context and hasattr(tool_context, "state"):
        current_version = int(tool_context.state.get("draft_version", 0) or 0)

    new_version = current_version + 1
    filename = f"brief_draft_v{new_version}.md"
    saved_to_service = False

    if tool_context:
        if hasattr(tool_context, "actions") and tool_context.actions:
            tool_context.actions.state_delta["draft_version"] = new_version
            tool_context.actions.state_delta["brief_draft"] = brief_draft

        try:
            part = types.Part.from_text(text=brief_draft)
            await tool_context.save_artifact(
                filename=filename,
                artifact=part,
                custom_metadata={"draft_version": new_version},
            )
            saved_to_service = True
        except Exception:
            # Fallback if runner does not have an artifact_service configured
            saved_to_service = False

    return {
        "status": "success",
        "draft_version": new_version,
        "artifact_file": filename,
        "saved_to_artifact_service": saved_to_service,
    }
