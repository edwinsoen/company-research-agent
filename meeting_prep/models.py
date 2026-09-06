"""Strategic model routing and tier configuration for Meeting Prep Copilot.

Maps agents to specific model tiers based on task shape and workload characteristics:
- Flash-Lite: entity_disambiguator, approval_gate, publisher
  (Structured, near-deterministic work with fixed schemas and no synthesis)
- Flash: researchers (profile, news, focus), refinement_router
  (High volume extraction where parallel execution multiplies cost)
- Pro: delta_agent, composer (escalated)
  (Genuine multi-input synthesis, delta analysis, and citation preservation)

Source: docs/orchestration-and-logic-enhancements.md §1
"""

import os
from typing import Any, Optional

# Concrete model identifiers with environment overrides
FLASH_LITE = os.getenv("MODEL_FLASH_LITE", "gemini-3.5-flash-lite")
FLASH = os.getenv("MODEL_FLASH", os.getenv("MODEL_NAME", "gemini-3.7-flash"))
PRO = os.getenv("MODEL_PRO", "gemini-3.1-pro-preview")

# Baseline static routing table
MODEL_ROUTING: dict[str, str] = {
    "entity_disambiguator": FLASH_LITE,
    "profile_researcher":   FLASH,
    "news_researcher":      FLASH,
    "focus_researcher":     FLASH,
    "delta_agent":          PRO,
    "composer":             FLASH,
    "approval_gate":        FLASH_LITE,
    "refinement_router":    FLASH,
    "publisher":            FLASH_LITE,
}


def get_agent_model(agent_name: str, state: Optional[dict[str, Any]] = None) -> str:
    """Resolve the active model for an agent, accounting for dynamic escalation.

    For the composer:
    - Initially defaults to FLASH.
    - If grounding self-check fails, state['composer_model'] is set to PRO.
    """
    if agent_name == "composer" and state:
        dynamic_model = state.get("composer_model")
        if dynamic_model:
            return dynamic_model

    return MODEL_ROUTING.get(agent_name, FLASH)
