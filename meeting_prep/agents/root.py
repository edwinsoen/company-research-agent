"""Root coordinator and brief pipeline.

Source: docs/hld.md §7.1 & §7.2
"""

import re
from google.adk.agents import LlmAgent, SequentialAgent
from meeting_prep.config import enable_server_side_tools_callback
from meeting_prep.models import MODEL_ROUTING, FLASH_LITE
from meeting_prep.tools.memory import (
    preload_memory_tool,
    initialize_briefing_session,
)
from meeting_prep.agents.disambiguator import create_entity_disambiguator
from meeting_prep.agents.researchers import create_research_parallel
from meeting_prep.agents.delta import create_delta_agent
from meeting_prep.agents.approval import create_refinement_loop
from meeting_prep.agents.publisher import create_publisher
from meeting_prep.callbacks.telemetry import before_agent_telemetry, after_agent_telemetry


def ensure_briefing_state(callback_context):
    """Ensure company_input and user_preferences exist in session state before pipeline runs."""
    state = callback_context.state
    if "user_preferences" not in state or not state["user_preferences"]:
        state["user_preferences"] = {"focus_areas": [], "recipients": []}
    if "company_input" not in state or not state["company_input"]:
        if callback_context.user_content and callback_context.user_content.parts:
            for part in callback_context.user_content.parts:
                text = getattr(part, "text", "")
                if text:
                    match = re.search(
                        r"(?:meeting with|briefing for|brief for|about|with|for)\s+([A-Za-z0-9\.\,\s\-]+?)(?:\.|\,|$|\n|Focus|focus|Please|please)",
                        text,
                        re.IGNORECASE,
                    )
                    if match:
                        state["company_input"] = match.group(1).strip()
                    else:
                        state["company_input"] = text.strip()
                    break


def create_brief_pipeline() -> SequentialAgent:
    """Create the sequential brief pipeline containing all core phases."""
    return SequentialAgent(
        name="brief_pipeline",
        sub_agents=[
            create_entity_disambiguator(),
            create_research_parallel(),
            create_delta_agent(),
            create_refinement_loop(),
            create_publisher(),
        ],
        before_agent_callback=ensure_briefing_state,
    )


ROOT_COORDINATOR_INSTRUCTION = """\
You are the entry-point coordinator for the Meeting Prep Copilot.

Your task is to parse the user's research request, initialize session state, and hand off to the brief pipeline.

Note: Saved user preferences from prior sessions are automatically preloaded into session state by preload_memory_tool before model execution.

Instructions:
1. Parse the target company name from the user input.
2. Check if the user specified any explicit focus areas or recipient emails in their prompt.
   - If explicit focus areas/recipients are provided in the prompt, pass them as overrides to initialize_briefing_session.
   - If NOT specified in the prompt, pass None or empty list so preloaded preferences in session state are preserved.
3. Call `initialize_briefing_session` with `company_input`, `focus_areas`, and `recipients` to record them into session state.
4. After calling `initialize_briefing_session`, output a brief confirmation message (e.g. "Session initialized for <company>. Proceeding with research.") and stop. Do not call any other tools.
"""


def create_root_coordinator() -> SequentialAgent:
    """Create the root_coordinator agent."""
    coordinator_step = LlmAgent(
        name="root_coordinator_step",
        model=FLASH_LITE,
        instruction=ROOT_COORDINATOR_INSTRUCTION,
        tools=[preload_memory_tool, initialize_briefing_session],
        before_agent_callback=before_agent_telemetry,
        after_agent_callback=after_agent_telemetry,
        before_model_callback=enable_server_side_tools_callback,
    )
    pipeline = create_brief_pipeline()
    return SequentialAgent(
        name="root_coordinator",
        sub_agents=[coordinator_step, pipeline],
    )

