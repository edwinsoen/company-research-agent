"""Entity disambiguator agent.

Resolves the user's company input to a single unambiguous company entity.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent
from google.adk.tools import google_search

from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ResolvedEntity
from meeting_prep.tools.hitl import request_disambiguation
from meeting_prep.callbacks.telemetry import before_agent_telemetry, after_agent_telemetry

DISAMBIGUATOR_INSTRUCTION = """\
You are an expert entity disambiguation agent for company research.

Your task is to resolve the user's company input:
Company input: {company_input?}

Instructions:
1. If company input is provided above, resolve that company. If empty or missing, inspect the conversation history for the target company name requested by the user.
2. Use the `google_search` tool to search for the official company, its primary website domain, and business description.
3. Determine resolution certainty:
   - HIGH CONFIDENCE (>= 0.85): If there is a single prominent, definitive company entity unambiguously matching the input (e.g. "Stripe", "Google", "Airbnb"), do NOT call `request_disambiguation`. Directly output the structured `ResolvedEntity`.
   - LOW CONFIDENCE (< 0.85) OR AMBIGUOUS: If multiple distinct prominent companies share the name (e.g. "Acme", "Apex", generic names), call the `request_disambiguation` tool passing a list of 2-3 candidate dictionaries:
     `candidates=[{"name": ..., "domain": ..., "description": ...}, ...]`.
4. When `request_disambiguation` returns with the human's selection, emit that chosen entity as the structured `ResolvedEntity`.
"""


def create_entity_disambiguator() -> LlmAgent:
    """Create the entity_disambiguator agent."""
    return LlmAgent(
        name="entity_disambiguator",
        model=MODEL_NAME,
        instruction=DISAMBIGUATOR_INSTRUCTION,
        tools=[google_search, request_disambiguation],
        before_agent_callback=before_agent_telemetry,
        after_agent_callback=after_agent_telemetry,
        output_schema=ResolvedEntity,
        output_key="resolved_entity",
        before_model_callback=enable_server_side_tools_callback,
    )

