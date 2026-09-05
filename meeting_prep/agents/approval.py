"""Approval gate, refinement router, and refinement loop agents.

Implements HITL approval checkpoint and routing within a LoopAgent.
Source: docs/hld.md §7.2
"""

from google.adk.agents import LlmAgent, LoopAgent
from meeting_prep.config import MODEL_NAME, enable_server_side_tools_callback
from meeting_prep.schemas import ApprovalDecision
from meeting_prep.tools.hitl import approve_brief
from meeting_prep.agents.composer import create_composer

APPROVAL_GATE_INSTRUCTION = """\
You are an executive approval gate agent.

Your role is to submit the generated briefing draft for user approval.
Current draft:
{brief_draft}

Instructions:
1. Call the `approve_brief` tool, passing the draft string.
2. The tool returns an approval decision dictionary with status and optional comment.
3. Emit the structured ApprovalDecision as your response.
"""

REFINEMENT_ROUTER_INSTRUCTION = """\
You are an intelligent refinement routing agent.

Your role is to analyze revision feedback from the user and determine which specific research area or section needs updating.

User review comment:
{approval_decision.comment}

Available research targets:
- "research_profile": Business model, leadership, company size, funding, valuation.
- "research_news": Recent 90-day announcements, launches, events.
- "research_focus": Custom requested focus topics.
- "all": If feedback affects multiple areas or is general.

Instructions:
1. Classify the user comment to determine the target section.
2. Formulate a precise, actionable search/research directive for that section.
3. Set your response with target and directive.
"""


def create_approval_gate() -> LlmAgent:
    """Create the approval_gate agent."""
    return LlmAgent(
        name="approval_gate",
        model=MODEL_NAME,
        instruction=APPROVAL_GATE_INSTRUCTION,
        tools=[approve_brief],
        output_schema=ApprovalDecision,
        output_key="approval_decision",
        before_model_callback=enable_server_side_tools_callback,
    )


def create_refinement_router() -> LlmAgent:
    """Create the refinement_router agent."""
    return LlmAgent(
        name="refinement_router",
        model=MODEL_NAME,
        instruction=REFINEMENT_ROUTER_INSTRUCTION,
        output_key="refinement_directive",
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
