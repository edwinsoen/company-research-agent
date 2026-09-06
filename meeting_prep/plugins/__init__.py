"""ADK guardrail and policy plugins for Meeting Prep Copilot.

Provides:
- RedactionPlugin: Multi-entity PII, token, and credential sanitizer.
- PublishPolicyPlugin: Hard gate requiring human approval, allowed recipient domains, and idempotency.
- GroundingGuardPlugin: Zero-LLM deterministic citation validator against research findings with Pro escalation.
- BudgetPlugin: Model-call and token usage tracker with ceiling-based early termination.
- InjectionGuardPlugin: Prompt injection and instruction-override detection on retrieved search content.

Source: docs/orchestration-and-logic-enhancements.md §2
"""

from meeting_prep.plugins.redaction import (
    RedactionPlugin,
    RedactionPipeline,
    RedactionFilter,
    redact_text,
    redact_data,
    redact_email,
)
from meeting_prep.plugins.publish_policy import PublishPolicyPlugin
from meeting_prep.plugins.grounding import GroundingGuardPlugin
from meeting_prep.plugins.budget import BudgetPlugin, BudgetExceededError
from meeting_prep.plugins.injection import InjectionGuardPlugin

__all__ = [
    "RedactionPlugin",
    "RedactionPipeline",
    "RedactionFilter",
    "redact_text",
    "redact_data",
    "redact_email",
    "PublishPolicyPlugin",
    "GroundingGuardPlugin",
    "BudgetPlugin",
    "BudgetExceededError",
    "InjectionGuardPlugin",
]
