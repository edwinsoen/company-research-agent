"""Entity disambiguator agent.

Resolves the user's company input to a single unambiguous company entity.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent
from google.adk.tools import google_search

from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ResolvedEntity

DISAMBIGUATOR_INSTRUCTION = """\
You are an expert entity disambiguation agent for company research.

Your task is to resolve the user's company input:
Company input: {company_input?}

Instructions:
1. If company input is provided above, resolve that company. If empty or missing, inspect the conversation history for the target company name requested by the user.
2. Use the `google_search` tool to search for the official company, its primary website domain, and business description.
3. Resolve to the single most prominent, definitive company entity matching the input.
4. Emit a structured ResolvedEntity with:
   - `name`: Official canonical company name (e.g., "Stripe, Inc." or "Stripe")
   - `domain`: Primary website domain (e.g., "stripe.com")
   - `description`: A clear one-sentence summary of what the company does
   - `confidence`: Confidence score (0.0 to 1.0) of this resolution (should be >= 0.9 for well-known companies)
"""


def create_entity_disambiguator() -> LlmAgent:
    """Create the entity_disambiguator agent."""
    return LlmAgent(
        name="entity_disambiguator",
        model=MODEL_NAME,
        instruction=DISAMBIGUATOR_INSTRUCTION,
        tools=[google_search],
        output_schema=ResolvedEntity,
        output_key="resolved_entity",
        before_model_callback=enable_server_side_tools_callback,
    )
