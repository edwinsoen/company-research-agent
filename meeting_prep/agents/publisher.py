"""Publisher agent.

Publishes approved briefs to Google Drive and shares them with requested recipients.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent
from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.tools.drive import create_google_doc, share_doc

PUBLISHER_INSTRUCTION = """\
You are an executive publishing agent.

Your role is to publish the approved briefing document to Google Drive and share it with the requested recipients.

Inputs:
Company name: {resolved_entity.name}
Approved draft:
{brief_draft}

Recipients to share with: {user_preferences.recipients}

Instructions:
1. Call `create_google_doc` with:
   - title: "Executive Brief: {resolved_entity.name}"
   - markdown: {brief_draft}
   - brief_id: "{resolved_entity.name}"
   - version: 1
2. If recipients are provided, call `share_doc` with the returned doc_id and recipient emails.
3. Return a confirmation message with the published doc_url.
"""


def check_approval_before_publish(callback_context):
    """Ensure publisher only runs if brief was explicitly approved by the human reviewer (HLD §9.4, §10.1)."""
    decision = callback_context.state.get("approval_decision") or {}
    if isinstance(decision, dict):
        status = decision.get("status")
    else:
        status = getattr(decision, "status", None)

    if status != "approved":
        from google.genai import types
        return types.Content(
            role="model",
            parts=[types.Part.from_text(text="Publishing skipped: executive brief was not approved by human reviewer.")],
        )
    return None


def create_publisher() -> LlmAgent:
    """Create the publisher agent."""
    return LlmAgent(
        name="publisher",
        model=MODEL_NAME,
        instruction=PUBLISHER_INSTRUCTION,
        tools=[create_google_doc, share_doc],
        before_agent_callback=check_approval_before_publish,
        before_model_callback=enable_server_side_tools_callback,
    )
