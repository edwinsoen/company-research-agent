"""InjectionGuardPlugin: Prompt injection and instruction-override guardrail for search results.

Retrieved web content is untrusted input reaching the model. This plugin scans
google_search tool outputs for instruction overrides, system prompt impersonations,
and jailbreak patterns before they reach the agent context.

Attempts to import an external guardrail package (e.g. adk-atr-guardrail),
falling back to a deterministic regex scanner. Offending snippets are sanitized
and a security audit event is logged.

Source: docs/orchestration-and-logic-enhancements.md §2.3
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

RESEARCHER_AGENTS = {
    "profile_researcher",
    "news_researcher",
    "focus_researcher",
    "delta_researcher",
}

# Compile high-confidence prompt injection patterns
INJECTION_PATTERNS = [
    re.compile(
        r"(?i)\b(?:ignore|disregard|forget|bypass|override)\s+(?:all\s+)?(?:previous|prior|above|preceding)\s+(?:instructions?|directives?|prompts?|rules?|guidelines?)\b"
    ),
    re.compile(
        r"(?i)\b(?:you\s+are\s+now|act\s+as|pretend\s+you\s+are|you\s+must\s+now\s+act\s+as)\s+(?:a|an)?\s*(?:unrestricted|jailbroken|evil|new|DAN|system|developer)\b"
    ),
    re.compile(
        r"(?i)<\s*(?:system|instructions?|prompt|override|developer_mode)\s*>"
    ),
    re.compile(
        r"(?i)\[\s*(?:system|instructions?|developer_mode|jailbreak)\s*\]"
    ),
    re.compile(
        r"(?i)\b(?:system\s+prompt|admin\s+override|jailbreak\s+active)\s*:"
    ),
    re.compile(
        r"(?i)\b(?:assistant\s+must\s+output|do\s+not\s+follow\s+safety\s+guidelines)\b"
    ),
]

_EXTERNAL_GUARDRAIL_AVAILABLE = False
try:
    import adk_atr_guardrail  # type: ignore
    _EXTERNAL_GUARDRAIL_AVAILABLE = True
except ImportError:
    pass


class InjectionGuardPlugin(BasePlugin):
    """ADK BasePlugin scanning and neutralizing prompt injection attacks in tool results."""

    def __init__(self) -> None:
        super().__init__(name="injection_guard_plugin")
        self.external_available = _EXTERNAL_GUARDRAIL_AVAILABLE

    def scan_and_sanitize_text(self, text: str) -> tuple[str, bool]:
        """Scan text for injection patterns. Return (sanitized_text, was_injected)."""
        if not text:
            return text, False

        detected = False
        sanitized = text
        for pattern in INJECTION_PATTERNS:
            if pattern.search(sanitized):
                detected = True
                sanitized = pattern.sub("[REDACTED_POTENTIAL_PROMPT_INJECTION]", sanitized)

        return sanitized, detected

    def sanitize_data(self, data: Any) -> tuple[Any, int]:
        """Recursively scan and sanitize strings in arbitrary nested data structures."""
        if isinstance(data, str):
            sanitized, hit = self.scan_and_sanitize_text(data)
            return sanitized, (1 if hit else 0)

        if isinstance(data, dict):
            new_dict = {}
            total_hits = 0
            for k, v in data.items():
                clean_v, hits = self.sanitize_data(v)
                new_dict[k] = clean_v
                total_hits += hits
            return new_dict, total_hits

        if isinstance(data, list):
            new_list = []
            total_hits = 0
            for item in data:
                clean_item, hits = self.sanitize_data(item)
                new_list.append(clean_item)
                total_hits += hits
            return new_list, total_hits

        return data, 0

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Scan tool outputs from google_search for prompt injection attacks."""
        tool_name = getattr(tool, "name", "")
        if tool_name != "google_search" or not result:
            return None

        sanitized_result, injection_hits = self.sanitize_data(result)

        if injection_hits > 0:
            logger.warning(
                "InjectionGuardPlugin DETECTED %d prompt-injection patterns in google_search results. Sanitized payload.",
                injection_hits,
                extra={
                    "event_type": "prompt_injection_neutralized",
                    "tool": tool_name,
                    "hits": injection_hits,
                },
            )
            return sanitized_result if isinstance(sanitized_result, dict) else result

        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Scan model outputs from researcher agents and search grounding for prompt injections."""
        agent_name = getattr(callback_context, "agent_name", "")
        has_grounding = getattr(llm_response, "grounding_metadata", None) is not None

        # Only scan researcher agents or responses that used search grounding
        if agent_name not in RESEARCHER_AGENTS and not has_grounding:
            return None

        if not llm_response or not getattr(llm_response, "content", None):
            return None

        content = llm_response.content
        parts = getattr(content, "parts", None)
        if not parts:
            return None

        total_hits = 0

        for part in parts:
            text = getattr(part, "text", None)
            if text:
                sanitized_text, hits = self.scan_and_sanitize_text(text)
                if hits:
                    part.text = sanitized_text
                    total_hits += (hits if isinstance(hits, int) else (1 if hits else 0))

        # Also inspect grounding metadata web chunks if present
        grounding = getattr(llm_response, "grounding_metadata", None)
        if grounding and getattr(grounding, "grounding_chunks", None):
            for chunk in grounding.grounding_chunks:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "title", None):
                    sanitized_title, hits = self.scan_and_sanitize_text(web.title)
                    if hits:
                        web.title = sanitized_title
                        total_hits += (hits if isinstance(hits, int) else (1 if hits else 0))

        if total_hits > 0:
            logger.warning(
                "InjectionGuardPlugin DETECTED %d prompt-injection patterns in %s model response. Sanitized payload.",
                total_hits,
                agent_name or "grounded_agent",
                extra={
                    "event_type": "prompt_injection_neutralized",
                    "agent": agent_name,
                    "hits": total_hits,
                },
            )
            return llm_response

        return None
