"""Long-term Memory Bank access tools for Meeting Prep Copilot.

Source of truth: docs/hld.md §9.4 & §9.5
- search_memory: Company-scoped retrieval from Memory Bank.
- preload_memory: Turn-start preloading of briefing preferences.
- initialize_briefing_session: Preserves preloaded preferences when not overridden.
"""

import inspect
import json
import logging
import re
import time
from typing import Any, Optional

from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.tool_context import ToolContext
from typing_extensions import override

from meeting_prep.callbacks.telemetry import log_intent, log_outcome

logger = logging.getLogger(__name__)


def _company_matches(target_company: str, record_company: str) -> bool:
    """Check whether record_company matches target_company using word boundaries.

    Allows name variants (e.g. 'Meta' vs 'Meta Platforms') while preventing
    substring collisions (e.g. 'Box' vs 'Boxed').
    """
    t = target_company.strip().lower()
    r = record_company.strip().lower()
    if not t or not r:
        return False
    if t == r:
        return True
    pattern_r = rf"\b{re.escape(r)}\b"
    pattern_t = rf"\b{re.escape(t)}\b"
    return bool(re.search(pattern_r, t) or re.search(pattern_t, r))


async def search_memory(
    query: str,
    company: str = "",
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Search long-term memory for prior briefs regarding a specific company.

    Scoped to target company name to avoid mixing facts across entities (HLD §9.5).
    Directly parses structured brief records from JSON text to handle Memory Bank
    cloud behavior where custom_metadata is stripped on retrieval.

    Args:
        query: Search query string.
        company: Target company name.
        tool_context: ADK tool context providing memory search interface.

    Returns:
        dict: Prior brief facts and metadata, or has_prior=False marker if baseline brief.
    """
    target_company = (company or query).strip()
    if not tool_context:
        raise ValueError("tool_context is required for memory search.")

    start_time = time.perf_counter()
    log_intent(
        logger,
        "search_memory",
        f"Querying long-term memory for prior briefs on '{target_company}'",
        company=target_company,
        query=query,
    )

    search_query = f"{target_company} briefing facts"
    logger.info("Querying long-term memory for target company '%s'", target_company)

    # 1. Company-scoped memory search; differentiate failures from empty hits (Finding 7)
    try:
        response = tool_context.search_memory(query=search_query)
        if inspect.isawaitable(response):
            response = await response
    except Exception as err:
        logger.error("Error searching long-term memory for '%s': %s", target_company, err, exc_info=True)
        raise RuntimeError(f"Memory service failure while searching for '{target_company}': {err}") from err

    # 2. Match only structured brief records (Finding 2)
    # A brief record is created as: {"company": ..., "date": ..., "facts": [...], "doc_url": ...}
    # In cloud deployments, custom_metadata is stripped by Memory Bank retrieve, so we parse JSON directly.
    # Narrative extraction memories from add_session_to_memory do NOT match this schema.
    matching_entries: list[dict[str, Any]] = []
    for mem in (getattr(response, "memories", None) or []):
        meta = getattr(mem, "custom_metadata", {}) or {}
        text_content = ""
        if mem.content and mem.content.parts:
            for part in mem.content.parts:
                t = getattr(part, "text", "")
                if t:
                    text_content += t

        if not text_content:
            continue

        try:
            parsed = json.loads(text_content)
            if not isinstance(parsed, dict):
                continue

            record_company = str(parsed.get("company") or meta.get("company") or "")
            facts = parsed.get("facts")

            # Must be a structured brief record containing facts list and company
            if isinstance(facts, list) and record_company:
                # Enforce company-scoped match using word boundaries (prevent Box vs Boxed)
                if _company_matches(target_company, record_company):
                    # Fall back to empty string so dated records sort above undated records
                    date_str = parsed.get("date") or meta.get("date") or ""
                    doc_url = parsed.get("doc_url") or meta.get("doc_url") or ""
                    timestamp = getattr(mem, "timestamp", "") or ""
                    matching_entries.append({
                        "mem": mem,
                        "facts": facts,
                        "date": str(date_str),
                        "doc_url": str(doc_url),
                        "timestamp": str(timestamp),
                    })
        except (json.JSONDecodeError, TypeError):
            # Narrative blobs from add_session_to_memory or non-JSON entries are rejected
            continue

    if not matching_entries:
        logger.info("No prior briefing history found for company '%s'", target_company)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_outcome(
            logger,
            "search_memory",
            f"No prior brief found for company '{target_company}' (baseline brief)",
            status="SUCCESS",
            duration_ms=duration_ms,
            company=target_company,
            has_prior=False,
        )
        return {
            "has_prior": False,
            "changes": [],
            "prior_facts": [],
            "message": f"No prior brief found for company '{target_company}'.",
        }

    # 3. Select most recent matching entry by date and timestamp descending (Finding 6)
    matching_entries.sort(
        key=lambda e: (e["date"], e["timestamp"]),
        reverse=True,
    )
    most_recent = matching_entries[0]
    prior_facts = [str(f) for f in most_recent["facts"]]
    prior_date = most_recent["date"] or "recent"
    doc_url = most_recent["doc_url"]

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    log_outcome(
        logger,
        "search_memory",
        f"Retrieved prior briefing record for '{target_company}' with {len(prior_facts)} historical facts",
        status="SUCCESS",
        duration_ms=duration_ms,
        company=target_company,
        has_prior=True,
        prior_facts_count=len(prior_facts),
    )
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
            text = ""
            if mem.content and mem.content.parts:
                for p in mem.content.parts:
                    t = getattr(p, "text", "")
                    if t:
                        text += t
            if not text:
                continue
            # Parse JSON directly to handle stripped custom_metadata in Memory Bank
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
        logger.error("Error preloading memory preferences: %s", err, exc_info=True)
        raise RuntimeError(f"Memory service failure while preloading preferences: {err}") from err

    if hasattr(tool_context, "state") and tool_context.state is not None:
        tool_context.state["user_preferences"] = prefs
    if hasattr(tool_context, "actions") and tool_context.actions:
        tool_context.actions.state_delta["user_preferences"] = prefs

    return prefs


class BriefingPreloadMemoryTool(PreloadMemoryTool):
    """PreloadMemoryTool primitive for briefing preferences (HLD §9.5, §2.1).

    Fires automatically at turn start before LLM execution, inserting past conversations
    transiently into prompt, and populating state_delta["user_preferences"].
    """

    @override
    async def process_llm_request(
        self,
        *,
        tool_context: ToolContext,
        llm_request: Any,
    ) -> None:
        await super().process_llm_request(tool_context=tool_context, llm_request=llm_request)
        await preload_memory(tool_context=tool_context)


preload_memory_tool = BriefingPreloadMemoryTool()


async def initialize_briefing_session(
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
    start_time = time.perf_counter()
    log_intent(
        logger,
        "initialize_briefing_session",
        f"Initializing briefing session state for '{company_input}'",
        company=company_input,
        focus_areas=focus_areas,
        recipients=recipients,
    )

    existing_prefs: dict[str, Any] = {}
    if tool_context and hasattr(tool_context, "state"):
        existing_prefs = tool_context.state.get("user_preferences") or {}
    if (
        (not existing_prefs or not existing_prefs.get("focus_areas"))
        and tool_context
        and hasattr(tool_context, "actions")
        and tool_context.actions
    ):
        existing_prefs = tool_context.actions.state_delta.get("user_preferences") or existing_prefs

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

    # If focus_areas or recipients were not supplied in prompt or state, retrieve preloaded preferences from memory
    if (not final_focus or not final_recipients) and tool_context:
        try:
            mem_prefs = await preload_memory(tool_context=tool_context)
            if not final_focus:
                final_focus = mem_prefs.get("focus_areas", [])
            if not final_recipients:
                final_recipients = mem_prefs.get("recipients", [])
        except Exception as e:
            logger.debug("initialize_briefing_session preload exception: %s", e)

    if tool_context and hasattr(tool_context, "actions") and tool_context.actions:
        tool_context.actions.state_delta["company_input"] = company_input
        tool_context.actions.state_delta["user_preferences"] = {
            "focus_areas": final_focus,
            "recipients": final_recipients,
        }

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    log_outcome(
        logger,
        "initialize_briefing_session",
        f"Initialized briefing session state for '{company_input}'",
        status="SUCCESS",
        duration_ms=duration_ms,
        company=company_input,
        focus_areas=final_focus,
        recipients=final_recipients,
    )

    return {
        "status": "initialized",
        "company_input": company_input,
        "focus_areas": final_focus,
        "recipients": final_recipients,
    }

