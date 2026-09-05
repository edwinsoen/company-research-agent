"""Long-term Memory Bank access tools for Meeting Prep Copilot.

Source of truth: docs/hld.md §9.4 & §9.5
- search_memory: Company-scoped retrieval from Memory Bank.
- preload_memory: Turn-start preloading of briefing preferences.
- initialize_briefing_session: Preserves preloaded preferences when not overridden.
"""

import inspect
import json
import logging
from typing import Any, Optional

from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


async def search_memory(
    query: str,
    company: str = "",
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Search long-term memory for prior briefs regarding a specific company.

    Scoped to target company name to avoid mixing facts across entities (HLD §9.5).

    Args:
        query: Search query string.
        company: Target company name.
        tool_context: ADK tool context providing memory search interface.

    Returns:
        dict: Prior brief facts and metadata, or has_prior=False marker if baseline brief.
    """
    target_company = (company or query).strip()
    if not tool_context:
        return {
            "has_prior": False,
            "changes": [],
            "prior_facts": [],
            "message": f"No prior brief found for company '{target_company}'.",
        }

    try:
        # 1. Company-scoped memory search
        search_query = f"{target_company} briefing facts"
        logger.info("Querying long-term memory for target company '%s'", target_company)
        response = tool_context.search_memory(query=search_query)
        if inspect.isawaitable(response):
            response = await response

        matching_entries = []
        for mem in (getattr(response, "memories", None) or []):
            meta = getattr(mem, "custom_metadata", {}) or {}
            mem_company = meta.get("company", "")
            topic = meta.get("topic", "")

            # Extract text
            text_content = ""
            if mem.content and mem.content.parts:
                for part in mem.content.parts:
                    t = getattr(part, "text", "")
                    if t:
                        text_content += t

            # Match company in metadata or text
            is_company_match = (
                (mem_company and target_company.lower() in mem_company.lower())
                or (target_company.lower() in text_content.lower())
            )
            if is_company_match and (topic == "company_brief_history" or "facts" in text_content):
                matching_entries.append((mem, text_content, meta))

        if not matching_entries:
            logger.info("No prior briefing history found for company '%s'", target_company)
            return {
                "has_prior": False,
                "changes": [],
                "prior_facts": [],
                "message": f"No prior brief found for company '{target_company}'.",
            }

        # Select most recent matching entry
        _, text_content, meta = matching_entries[-1]
        prior_facts: list[str] = []
        prior_date = meta.get("date", "recent")
        doc_url = meta.get("doc_url", "")

        try:
            parsed = json.loads(text_content)
            if isinstance(parsed, dict):
                prior_facts = parsed.get("facts", [])
                prior_date = parsed.get("date", prior_date)
                doc_url = parsed.get("doc_url", doc_url)
            elif isinstance(parsed, list):
                prior_facts = [str(x) for x in parsed]
        except Exception:
            prior_facts = [text_content]

        logger.info(
            "Found prior briefing record for '%s' (%d historical facts, date: %s)",
            target_company,
            len(prior_facts),
            prior_date,
        )

        return {
            "has_prior": True,
            "company": target_company,
            "prior_date": prior_date,
            "prior_facts": prior_facts,
            "doc_url": doc_url,
            "message": f"Retrieved prior briefing record for '{target_company}' with {len(prior_facts)} historical facts.",
        }

    except Exception as err:
        logger.warning("Error searching long-term memory: %s", err)
        return {
            "has_prior": False,
            "changes": [],
            "prior_facts": [],
            "message": f"No prior brief found for company '{target_company}'.",
        }


async def preload_memory(
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Preload user briefing preferences from long-term memory (HLD §9.5).

    Args:
        tool_context: ADK tool context.

    Returns:
        dict: Preloaded preferences.
    """
    prefs = {
        "focus_areas": [],
        "recipients": [],
    }
    if not tool_context:
        return prefs

    # Check if preferences already loaded in session state
    if tool_context.state:
        existing = tool_context.state.get("user_preferences")
        if existing and (existing.get("focus_areas") or existing.get("recipients")):
            return existing

    # Search long-term memory for saved preferences
    try:
        response = tool_context.search_memory(query="briefing preferences focus recipients")
        if inspect.isawaitable(response):
            response = await response
        for mem in (getattr(response, "memories", None) or []):
            meta = getattr(mem, "custom_metadata", {}) or {}
            text = ""
            if mem.content and mem.content.parts:
                for p in mem.content.parts:
                    t = getattr(p, "text", "")
                    if t:
                        text += t
            if meta.get("topic") == "briefing_preferences" or ("focus_areas" in text and "recipients" in text):
                if text:
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict) and (parsed.get("focus_areas") or parsed.get("recipients")):
                            prefs["focus_areas"] = parsed.get("focus_areas", [])
                            prefs["recipients"] = parsed.get("recipients", [])
                            logger.info("Preloaded user preferences from Memory Bank: %s", prefs)
                            break
                    except Exception:
                        pass
    except Exception as err:
        logger.debug("Preference search from memory: %s", err)

    if hasattr(tool_context, "actions") and tool_context.actions:
        tool_context.actions.state_delta["user_preferences"] = prefs

    return prefs


def initialize_briefing_session(
    company_input: str,
    focus_areas: Optional[list[str]] = None,
    recipients: Optional[list[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Record target company name, focus areas, and recipients into session state.

    Preserves preloaded preferences when user did not supply explicit overrides (HLD §4).

    Args:
        company_input: Target company name extracted from user prompt.
        focus_areas: Optional list of specific topics to focus research on.
        recipients: Optional list of recipient email addresses.
        tool_context: ADK tool context.

    Returns:
        dict: Confirmation of initialized session state.
    """
    existing_prefs: dict[str, Any] = {}
    if tool_context and hasattr(tool_context, "state"):
        existing_prefs = tool_context.state.get("user_preferences") or {}

    # Merge: explicit overrides take precedence, otherwise fallback to preloaded preferences
    final_focus = (
        focus_areas
        if (focus_areas is not None and len(focus_areas) > 0)
        else existing_prefs.get("focus_areas", [])
    )
    final_recipients = (
        recipients
        if (recipients is not None and len(recipients) > 0)
        else existing_prefs.get("recipients", [])
    )

    if tool_context and hasattr(tool_context, "actions") and tool_context.actions:
        tool_context.actions.state_delta["company_input"] = company_input
        tool_context.actions.state_delta["user_preferences"] = {
            "focus_areas": final_focus,
            "recipients": final_recipients,
        }

    return {
        "status": "initialized",
        "company_input": company_input,
        "focus_areas": final_focus,
        "recipients": final_recipients,
    }

