"""Delta agent.

Compares current findings against prior briefs from memory for the target company.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent
from meeting_prep.config import enable_server_side_tools_callback
from meeting_prep.models import MODEL_ROUTING
from meeting_prep.schemas import DeltaSummary
from meeting_prep.tools.memory import search_memory
from meeting_prep.callbacks.telemetry import before_agent_telemetry, after_agent_telemetry

DELTA_AGENT_INSTRUCTION = """\
You are an expert intelligence analyst responsible for computing what has changed since prior briefings on a company.

Target company: {resolved_entity.name}

Current findings:
- Profile findings: {research_profile}
- News findings: {research_news}
- Focus findings: {research_focus}

Instructions:
1. Call `search_memory` with query="{resolved_entity.name}" and company="{resolved_entity.name}".
2. Inspect the memory result:
   - If no prior brief exists (`has_prior` is False), emit a DeltaSummary with:
     `has_prior`: False
     `changes`: ["No prior briefing record found for this company. This is the initial baseline brief."]
   - If prior briefs exist (`has_prior` is True), compare the current findings against the prior facts and list 3-5 concise bullet points highlighting key developments, metric changes, or strategy shifts.
3. Emit a structured DeltaSummary strictly conforming to the schema.
"""


def create_delta_agent() -> LlmAgent:
    """Create the delta_agent."""
    return LlmAgent(
        name="delta_agent",
        model=MODEL_ROUTING["delta_agent"],
        instruction=DELTA_AGENT_INSTRUCTION,
        tools=[search_memory],
        before_agent_callback=before_agent_telemetry,
        after_agent_callback=after_agent_telemetry,
        output_schema=DeltaSummary,
        output_key="delta_summary",
        before_model_callback=enable_server_side_tools_callback,
    )
