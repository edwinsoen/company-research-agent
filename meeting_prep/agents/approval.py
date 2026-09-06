"""Approval gate, refinement router, and refinement loop agents.

Implements HITL approval checkpoint and routing within a LoopAgent.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent, LoopAgent
from google.adk.tools import AgentTool
from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ApprovalDecision, RefinementRouting, RefinementTarget
from meeting_prep.tools.hitl import approve_brief
from meeting_prep.agents.composer import create_composer
from meeting_prep.agents.researchers import (
    create_profile_researcher,
    create_news_researcher,
    create_focus_researcher,
    create_research_parallel,
)
from meeting_prep.callbacks.telemetry import (
    before_agent_telemetry,
    after_agent_telemetry,
    record_router_classification_span,
)

APPROVAL_GATE_INSTRUCTION = """\
You are an executive approval gate agent.

Your role is to submit the generated briefing draft for user review and decision.
Current draft:
{brief_draft}

Instructions:
1. If you have not yet called `approve_brief`, call the `approve_brief` tool passing the draft string.
2. When the tool response arrives with the human review decision, emit structured ApprovalDecision.
"""


def handle_approval_agent_callback(callback_context):
    """Handle approval gate completion.

    On approval, sets escalate = True to exit LoopAgent deterministically (HLD §7.2).
    Also ensures the literal human review feedback from FunctionResponse is preserved in state.
    """
    # Preserve raw, literal human review decision from FunctionResponse if resuming
    user_content = callback_context.user_content
    if user_content and user_content.parts:
        for part in user_content.parts:
            fr = getattr(part, "function_response", None)
            if fr and fr.name == "approve_brief" and isinstance(fr.response, dict):
                curr = callback_context.state.get("approval_decision") or {}
                if isinstance(curr, dict):
                    if fr.response.get("comment") is not None:
                        curr["comment"] = fr.response.get("comment")
                    if fr.response.get("status"):
                        curr["status"] = fr.response.get("status")
                    callback_context.state["approval_decision"] = curr
                break

    decision = callback_context.state.get("approval_decision") or {}
    if isinstance(decision, dict):
        status = decision.get("status")
    else:
        status = getattr(decision, "status", None)

    after_agent_telemetry(callback_context)

    if status == "approved":
        callback_context.actions.escalate = True
        from google.genai import types
        return types.Content(
            role="model",
            parts=[types.Part.from_text(text="Executive brief approved. Exiting refinement loop.")],
        )
    return None


REFINEMENT_ROUTER_INSTRUCTION = """\
You are an intelligent refinement routing agent.

Your role is to analyze revision feedback from the user, determine which specific research area or section needs updating, and invoke the appropriate researcher tool.

User review decision and feedback:
{approval_decision}

Available research tools:
- `profile_researcher`: Business model, pricing models, leadership, company scale, funding, valuation.
- `news_researcher`: Recent 90-day announcements, launches, events, partnerships.
- `focus_researcher`: Custom requested focus topics.
- `research_parallel`: If feedback affects multiple areas, is general, or classification confidence is low (< 0.8).

Instructions:
1. Classify the user comment to determine the target section:
   - If the comment relates to business profile, pricing models, funding, leadership, or company metrics -> Call `profile_researcher`.
   - If the comment relates to news, recent announcements, acquisitions, or launches -> Call `news_researcher`.
   - If the comment relates to user-specific focus areas -> Call `focus_researcher`.
   - If the comment is general, touches multiple areas, or confidence is low (< 0.8) -> Call `research_parallel`.
2. Pass a clear search request to the tool explaining what needs to be investigated based on the user's feedback.
3. After the research tool completes, provide your final routing classification using the required structured schema with target, directive, and confidence.
"""


def sync_routing_to_state(callback_context):
    """Extract validated RefinementRouting and synchronize refinement_target and refinement_directive into session state."""
    state = callback_context.state
    routing = state.get("refinement_routing")
    if not routing:
        state["refinement_target"] = "all"
        return None

    if isinstance(routing, dict):
        target = routing.get("target")
        directive = routing.get("directive", "")
        confidence = float(routing.get("confidence", 1.0))
    else:
        target = getattr(routing, "target", "all")
        directive = getattr(routing, "directive", "")
        confidence = float(getattr(routing, "confidence", 1.0))

    if hasattr(target, "value"):
        target = target.value
    elif isinstance(target, str) and target.startswith("RefinementTarget."):
        target = target.split(".")[-1].lower()

    # Fallback to all if classification confidence is low (HLD §7.2)
    if confidence < 0.8:
        target = "all"

    state["refinement_target"] = target
    state["refinement_directive"] = directive

    iteration = int(state.get("refinement_iteration", 1))
    record_router_classification_span(
        target=target,
        confidence=confidence,
        directive=directive,
        iteration=iteration,
    )
    after_agent_telemetry(callback_context)
    return None


def create_approval_gate() -> LlmAgent:
    """Create the approval_gate agent."""
    return LlmAgent(
        name="approval_gate",
        model=MODEL_NAME,
        instruction=APPROVAL_GATE_INSTRUCTION,
        tools=[approve_brief],
        before_agent_callback=before_agent_telemetry,
        after_agent_callback=handle_approval_agent_callback,
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
        before_agent_callback=before_agent_telemetry,
        output_schema=RefinementRouting,
        output_key="refinement_routing",
        after_agent_callback=sync_routing_to_state,
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

