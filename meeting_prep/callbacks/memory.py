"""Post-approval memory write callbacks for Meeting Prep Copilot.

Source of truth: docs/hld.md §9.3 & §9.4
- Two custom topics:
  1. 'briefing_preferences' (focus areas, recipients; standing preferences, TTL: None)
  2. 'company_brief_history' (company, date, headline facts, doc link; TTL: 90 days)
- Fires after_agent on publisher only upon explicit human approval.
- Direct ingestion (add_memory) for structured brief record.
- add_session_to_memory for preference extraction.
"""

import datetime
import inspect
import json
import logging
import time
from typing import Any

from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from meeting_prep.callbacks.telemetry import log_intent, log_outcome

logger = logging.getLogger(__name__)


async def save_memory_after_publish(callback_context) -> None:
    """Save brief to Memory Bank only after human approval (HLD §9.4).

    Fires on after_agent_callback on the publisher agent.
    """
    state = callback_context.state or {}
    start_time = time.perf_counter()

    # Check human approval decision
    decision = state.get("approval_decision") or {}
    if isinstance(decision, dict):
        status = decision.get("status")
    else:
        status = getattr(decision, "status", None)

    # Extract target company name
    resolved_entity = state.get("resolved_entity") or {}
    if isinstance(resolved_entity, dict):
        company_name = resolved_entity.get("name") or state.get("company_input") or "Unknown"
    else:
        company_name = getattr(resolved_entity, "name", None) or state.get("company_input") or "Unknown"

    log_intent(
        logger,
        "memory_write",
        f"Initiating post-approval Memory Bank ingestion for company '{company_name}'",
        company=company_name,
        approval_status=status,
    )

    if status != "approved":
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_outcome(
            logger,
            "memory_write",
            f"Memory write skipped: executive brief was not approved (status='{status}')",
            status="SKIPPED",
            duration_ms=duration_ms,
            company=company_name,
        )
        logger.info("Memory write skipped: executive brief was not approved by human reviewer.")
        return

    # Extract top headline findings
    research_profile = state.get("research_profile") or {}
    if isinstance(research_profile, dict):
        findings = research_profile.get("findings", [])
    else:
        findings = getattr(research_profile, "findings", [])

    facts: list[str] = []
    for f in findings[:5]:
        if isinstance(f, dict):
            claim = f.get("claim")
        else:
            claim = getattr(f, "claim", str(f))
        if claim:
            facts.append(claim)

    doc_url = state.get("published_doc_url", "")
    current_date = datetime.date.today().isoformat()

    # 1. Direct ingestion of structured fact record for 'company_brief_history' (HLD §9.4)
    brief_record = {
        "company": company_name,
        "date": current_date,
        "facts": facts,
        "doc_url": doc_url,
    }

    history_entry = MemoryEntry(
        content=types.Content(
            role="user",
            parts=[types.Part.from_text(text=json.dumps(brief_record))],
        ),
        custom_metadata={
            "topic": "company_brief_history",
            "topics": [{"custom_memory_topic_label": "company_brief_history"}],
            "company": company_name,
            "date": current_date,
            "doc_url": doc_url,
            "ttl": "7776000s",
        },
    )

    history_metadata = {
        "topic": "company_brief_history",
        "topics": [{"custom_memory_topic_label": "company_brief_history"}],
        "company": company_name,
        "date": current_date,
        "doc_url": doc_url,
        "ttl": "7776000s",
    }

    try:
        res = callback_context.add_memory(memories=[history_entry], custom_metadata=history_metadata)
        if inspect.isawaitable(res):
            await res
        logger.info("Successfully ingested structured brief record for '%s' to Memory Bank", company_name)
    except NotImplementedError:
        try:
            from google.adk.events import Event
            mem_service = getattr(getattr(callback_context, "_invocation_context", None), "memory_service", None)
            if mem_service and hasattr(mem_service, "add_events_to_memory"):
                ev = Event(author="publisher", content=history_entry.content)
                session = callback_context._invocation_context.session
                res = mem_service.add_events_to_memory(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    events=[ev],
                )
                if inspect.isawaitable(res):
                    await res
                logger.info("Ingested structured brief record via fallback add_events_to_memory")
        except Exception as fallback_err:
            logger.warning("Fallback add_events_to_memory failed: %s", fallback_err)
    except Exception as err:
        logger.warning("Failed to save brief record to memory: %s", err)

    # 2. Persist user preferences for 'briefing_preferences' topic (with deduplication)
    user_prefs = state.get("user_preferences") or {}
    has_prefs = bool(user_prefs.get("focus_areas") or user_prefs.get("recipients"))
    if has_prefs:
        # Check if identical preferences are already stored to avoid appending duplicate records
        is_duplicate = False
        try:
            if hasattr(callback_context, "search_memory"):
                existing_res = callback_context.search_memory("briefing preferences focus recipients")
                if inspect.isawaitable(existing_res):
                    existing_res = await existing_res
                for mem in (getattr(existing_res, "memories", None) or []):
                    if mem.content and mem.content.parts:
                        for p in mem.content.parts:
                            t = getattr(p, "text", "")
                            if t:
                                try:
                                    parsed = json.loads(t)
                                    if isinstance(parsed, dict) and parsed.get("focus_areas") == user_prefs.get(
                                        "focus_areas"
                                    ) and parsed.get("recipients") == user_prefs.get("recipients"):
                                        is_duplicate = True
                                        break
                                except Exception:
                                    pass
                    if is_duplicate:
                        break
        except Exception as dup_check_err:
            logger.debug("Deduplication check error (continuing with save): %s", dup_check_err)

        if is_duplicate:
            logger.info("Preferences already present in Memory Bank; skipping duplicate ingestion.")
        else:
            prefs_entry = MemoryEntry(
                content=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=json.dumps(user_prefs))],
                ),
                custom_metadata={
                    "topic": "briefing_preferences",
                    "topics": [{"custom_memory_topic_label": "briefing_preferences"}],
                },
            )
            try:
                res = callback_context.add_memory(
                    memories=[prefs_entry],
                    custom_metadata={
                        "topic": "briefing_preferences",
                        "topics": [{"custom_memory_topic_label": "briefing_preferences"}],
                    },
                )
                if inspect.isawaitable(res):
                    await res
                logger.info("Successfully ingested user preferences to Memory Bank")
            except NotImplementedError:
                try:
                    from google.adk.events import Event
                    mem_service = getattr(getattr(callback_context, "_invocation_context", None), "memory_service", None)
                    if mem_service and hasattr(mem_service, "add_events_to_memory"):
                        ev = Event(author="user", content=prefs_entry.content)
                        session = callback_context._invocation_context.session
                        res = mem_service.add_events_to_memory(
                            app_name=session.app_name,
                            user_id=session.user_id,
                            events=[ev],
                        )
                        if inspect.isawaitable(res):
                            await res
                        logger.info("Ingested preferences via fallback add_events_to_memory")
                except Exception as fallback_err:
                    logger.warning("Fallback preferences add_events_to_memory failed: %s", fallback_err)
            except Exception as err:
                logger.warning("Failed to save preferences to memory: %s", err)

    # 3. Trigger session extraction for standing preferences
    try:
        res = callback_context.add_session_to_memory()
        if inspect.isawaitable(res):
            await res
        logger.info("Triggered add_session_to_memory on callback context")
    except Exception as err:
        logger.debug("add_session_to_memory call: %s", err)

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    log_outcome(
        logger,
        "memory_write",
        f"Completed Memory Bank persistence for '{company_name}'",
        status="SUCCESS",
        duration_ms=duration_ms,
        company=company_name,
    )

