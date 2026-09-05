"""Parallel researcher agents.

Implements profile_researcher, news_researcher, focus_researcher,
and groups them under research_parallel (ParallelAgent).
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.tools import google_search

from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ResearchFindings


PROFILE_RESEARCHER_INSTRUCTION = """\
You are an expert corporate intelligence researcher focusing on company profile and business fundamentals.

Target company: {resolved_entity.name}
Domain: {resolved_entity.domain}
Context: {resolved_entity.description}

Task:
Use the `google_search` tool to gather factual information about:
- What the company does and core value proposition
- Business model and monetization
- Target customer segments / market
- Company scale (employees, valuation, revenue if public, total funding)
- Key leadership / founders

Output requirements:
- Return a structured ResearchFindings object with up to 8 findings.
- Each finding MUST be a concrete claim with an exact source_url and source_date (YYYY-MM-DD or year).
- Do NOT output prose.
"""


NEWS_RESEARCHER_INSTRUCTION = """\
You are an expert news researcher tracking recent developments for executive briefings.

Target company: {resolved_entity.name}
Domain: {resolved_entity.domain}

Task:
Use the `google_search` tool to investigate recent major news and developments from the last 90 days:
- Major product launches or platform releases
- Strategic partnerships, acquisitions, or expansions
- Executive leadership appointments
- Significant customer or financial milestones

Output requirements:
- Return a structured ResearchFindings object with up to 8 findings.
- Every finding MUST carry a valid date (`source_date`). Undated findings MUST be excluded.
- Each finding must cite the exact source_url.
- Do NOT output prose.
"""


FOCUS_RESEARCHER_INSTRUCTION = """\
You are a specialized researcher investigating custom user-specified focus areas for an executive briefing.

Target company: {resolved_entity.name}
Domain: {resolved_entity.domain}
Focus areas requested: {user_preferences.focus_areas}

Task:
1. Check if the user specified any focus areas.
2. If no focus areas are requested (or the list is empty), return an empty list of findings (`findings: []`). Do NOT invent topics.
3. If focus areas are specified, use `google_search` to investigate how the target company addresses each specific focus area.

Output requirements:
- Return a structured ResearchFindings object with up to 8 findings.
- Each finding must be a concrete claim citing the source_url and source_date.
- Do NOT output prose.
"""


def create_profile_researcher() -> LlmAgent:
    """Create the profile_researcher agent."""
    return LlmAgent(
        name="profile_researcher",
        model=MODEL_NAME,
        instruction=PROFILE_RESEARCHER_INSTRUCTION,
        tools=[google_search],
        output_schema=ResearchFindings,
        output_key="research_profile",
        before_model_callback=enable_server_side_tools_callback,
    )


def create_news_researcher() -> LlmAgent:
    """Create the news_researcher agent."""
    return LlmAgent(
        name="news_researcher",
        model=MODEL_NAME,
        instruction=NEWS_RESEARCHER_INSTRUCTION,
        tools=[google_search],
        output_schema=ResearchFindings,
        output_key="research_news",
        before_model_callback=enable_server_side_tools_callback,
    )


def create_focus_researcher() -> LlmAgent:
    """Create the focus_researcher agent."""
    return LlmAgent(
        name="focus_researcher",
        model=MODEL_NAME,
        instruction=FOCUS_RESEARCHER_INSTRUCTION,
        tools=[google_search],
        output_schema=ResearchFindings,
        output_key="research_focus",
        before_model_callback=enable_server_side_tools_callback,
    )


def create_research_parallel() -> ParallelAgent:
    """Create the ParallelAgent grouping all three concurrent researchers."""
    return ParallelAgent(
        name="research_parallel",
        sub_agents=[
            create_profile_researcher(),
            create_news_researcher(),
            create_focus_researcher(),
        ],
    )
