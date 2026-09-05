"""Approval gate, refinement router, and refinement loop agents.

Implements HITL approval checkpoint and routing within a LoopAgent.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent, LoopAgent
from google.adk.tools import AgentTool, exit_loop
from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ApprovalDecision
from meeting_prep.tools.hitl import approve_brief
from meeting_prep.agents.composer import create_composer
from meeting_prep.agents.researchers import (
    create_profile_researcher,
    create_news_researcher,
    create_focus_researcher,
    create_research_parallel,
)

APPROVAL_GATE_INSTRUCTION = """\
You are an executive approval gate agent.

Your role is to submit the generated briefing draft for user review and decision.
Current draft:
{brief_draft}

Instructions:
1. If you have not yet called `approve_brief`, call the `approve_brief` tool passing the draft string.
2. When the tool response arrives with the human review decision:
   - If status is 'approved': Call the `exit_loop` tool to exit the refinement loop and proceed to publishing. Also emit structured ApprovalDecision with status='approved'.
   - If status is 'revise': Do NOT call `exit_loop`. Emit structured ApprovalDecision with status='revise' and comment set to the user's feedback.
"""

REFINEMENT_ROUTER_INSTRUCTION = """\
You are an intelligent refinement routing agent.

Your role is to analyze revision feedback from the user, determine which specific research area or section needs updating, and invoke the appropriate researcher tool.

User review comment:
{approval_decision.comment}

Available research tools:
- `profile_researcher`: Business model, leadership, company scale, funding, valuation.
- `news_researcher`: Recent 90-day announcements, launches, events, partnerships.
- `focus_researcher`: Custom requested focus topics.
- `research_parallel`: If feedback affects multiple areas, is general, or classification confidence is low.

Instructions:
1. Classify the user comment to determine the target section:
   - If the comment relates to business profile, funding, leadership, or company metrics -> Call `profile_researcher`.
   - If the comment relates to news, recent announcements, acquisitions, or launches -> Call `news_researcher`.
   - If the comment relates to user-specific focus areas -> Call `focus_researcher`.
   - If the comment is general, touches multiple areas, or you are unsure -> Call `research_parallel`.
2. Pass a clear search request to the tool explaining what needs to be investigated based on the user's feedback.
3. State your directive and target in your final response.
"""


def sync_refinement_target(callback_context):
    """Ensure refinement_target and refinement_directive are synchronized in session state."""
    state = callback_context.state
    comment = (state.get("approval_decision") or {}).get("comment", "").lower()

    if "refinement_target" not in state or not state["refinement_target"]:
        if any(w in comment for w in ["funding", "valuation", "profile", "model", "size", "employee", "founder", "ceo"]):
            state["refinement_target"] = "research_profile"
        elif any(w in comment for w in ["news", "recent", "launch", "partnership", "acquisition", "quarter", "month", "announce"]):
            state["refinement_target"] = "research_news"
        elif any(w in comment for w in ["focus", "custom", "special"]):
            state["refinement_target"] = "research_focus"
        else:
            state["refinement_target"] = "all"


def create_approval_gate() -> LlmAgent:
    """Create the approval_gate agent."""
    return LlmAgent(
        name="approval_gate",
        model=MODEL_NAME,
        instruction=APPROVAL_GATE_INSTRUCTION,
        tools=[approve_brief, exit_loop],
        output_schema=ApprovalDecision,
        output_key="approval_decision",
        before_model_callback=enable_server_side_tools_callback,
    )


def create_refinement_router() -> LlmAgent:
    """Create the refinement_router agent."""
    profile_tool = AgentTool(agent=create_profile_researcher())
    news_tool = AgentTool(agent=create_news_researcher())
    focus_tool = AgentTool(agent=create_focus_researcher())
    all_tool = AgentTool(agent=create_research_parallel())

    return LlmAgent(
        name="refinement_router",
        model=MODEL_NAME,
        instruction=REFINEMENT_ROUTER_INSTRUCTION,
        tools=[profile_tool, news_tool, focus_tool, all_tool],
        output_key="refinement_directive",
        after_agent_callback=sync_refinement_target,
        before_model_callback=enable_server_side_tools_callback,
    )


def create_refinement_loop() -> LoopAgent:
    """Create the LoopAgent coordinating composition, approval, and targeted refinement."""
    return LoopAgent(
        name="refinement_loop",
        max_iterations=3,
        sub_agents=[
            create_composer(),
            create_approval_gate(),
            create_refinement_router(),
        ],
    )

