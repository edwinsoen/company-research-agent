"""GroundingGuardPlugin: Deterministic self-evaluation policy for citation fidelity.

Source: docs/orchestration-and-logic-enhancements.md §1.3 & §2.2
Enforces:
1. Extract every claim line from the draft.
2. Assert each carries a source URL.
3. Assert each URL appears in the research_* findings in session state.
4. On failure, reject draft and flag retry with corrective instruction on PRO tier.
5. On repeated failure, surface draft with unsourced claims listed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Set

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from meeting_prep.models import PRO

logger = logging.getLogger(__name__)

# Pattern to extract markdown links [anchor text](url)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
URL_PATTERN = re.compile(r"https?://[^\s\)\>\]]+")


def extract_known_research_urls(state: dict[str, Any]) -> Set[str]:
    """Collect all valid source URLs present in research_profile, research_news, and research_focus."""
    known_urls: set[str] = set()
    for key in ("research_profile", "research_news", "research_focus"):
        findings_obj = state.get(key)
        if not findings_obj:
            continue

        items = []
        if isinstance(findings_obj, dict):
            items = findings_obj.get("findings") or []
        elif hasattr(findings_obj, "findings"):
            items = getattr(findings_obj, "findings", []) or []

        for item in items:
            url = None
            if isinstance(item, dict):
                url = item.get("source_url")
            elif hasattr(item, "source_url"):
                url = getattr(item, "source_url", None)

            if url and isinstance(url, str):
                cleaned = url.strip().rstrip("/")
                if cleaned:
                    known_urls.add(cleaned)
                    # Also include exact string as-is
                    known_urls.add(url.strip())

    return known_urls


def is_structural_line(line: str) -> bool:
    """Check if a line is a header, separator, metadata, or structural disclaimer rather than a factual claim."""
    stripped = line.strip()
    if not stripped:
        return True
    # Markdown headers
    if stripped.startswith("#"):
        return True
    # Horizontal rules
    if stripped in ("---", "***", "___"):
        return True
    # Structural metadata disclaimers
    if (
        stripped.startswith("*Generated")
        or stripped.startswith("*No prior briefing")
        or stripped.startswith("*Standard profile")
        or stripped.startswith("*(Generated")
        or stripped.startswith("*(No prior")
    ):
        return True
    # Warning callout blocks from prior grounding checks
    if stripped.startswith("> [!WARNING]") or stripped.startswith("> Unsourced claim:"):
        return True
    return False


class GroundingGuardPlugin(BasePlugin):
    """Deterministic, zero-LLM guardrail plugin verifying composer drafts against research findings."""

    def __init__(self) -> None:
        super().__init__(name="grounding_guard_plugin")

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Validate composer output for citation grounding."""
        agent_name = getattr(callback_context, "agent_name", "")
        if agent_name != "composer":
            return None

        # Extract draft text from LLM response
        content = getattr(llm_response, "content", None)
        if not content or not getattr(content, "parts", None):
            return None

        draft_text = ""
        for part in content.parts:
            text = getattr(part, "text", "")
            if text:
                draft_text += text

        if not draft_text:
            return None

        state = callback_context.state
        known_urls = extract_known_research_urls(state)
        known_normalized = {u.rstrip("/") for u in known_urls}

        current_attempt = int(state.get("grounding_attempts", 0)) + 1
        state["grounding_attempts"] = current_attempt

        unsourced_claims: list[str] = []
        invalid_urls: list[str] = []

        # Analyze line by line
        for line in draft_text.splitlines():
            clean_line = line.strip()
            if is_structural_line(clean_line):
                continue

            # Check markdown links
            markdown_links = MARKDOWN_LINK_PATTERN.findall(clean_line)
            raw_urls = URL_PATTERN.findall(clean_line)

            if not markdown_links and not raw_urls:
                # Content line without any citation link
                unsourced_claims.append(clean_line)
                continue

            # Verify every cited URL is known from research findings
            cited_urls = [link[1].strip() for link in markdown_links] + [u.strip() for u in raw_urls]
            for cited_url in cited_urls:
                if cited_url.rstrip("/") not in known_normalized:
                    invalid_urls.append(cited_url)
                    unsourced_claims.append(f"{clean_line} (Invalid URL: {cited_url})")

        passed = len(unsourced_claims) == 0

        validation_summary = {
            "passed": passed,
            "attempt": current_attempt,
            "unsourced_count": len(unsourced_claims),
            "unsourced_claims": unsourced_claims,
            "invalid_urls": invalid_urls,
        }
        state["grounding_validation"] = validation_summary

        if passed:
            logger.info(
                "GroundingGuardPlugin PASSED for composer (attempt %d). All claims grounded in research findings.",
                current_attempt,
                extra={"event_type": "grounding_check", "status": "PASSED", "attempt": current_attempt},
            )
            state["grounding_retry_needed"] = False
            return None

        # Failure case
        logger.warning(
            "GroundingGuardPlugin FAILED for composer (attempt %d): %d unsourced claims detected",
            current_attempt,
            len(unsourced_claims),
            extra={
                "event_type": "grounding_check",
                "status": "FAILED",
                "attempt": current_attempt,
                "unsourced_claims": unsourced_claims[:5],
            },
        )

        if current_attempt == 1:
            # First failure: Escalate to PRO and prepare corrective instruction for regeneration
            state["composer_model"] = PRO
            state["grounding_retry_needed"] = True
            corrective_msg = (
                "Grounding self-check failed on your previous draft. "
                "The following claims were missing inline Markdown citations to valid research URLs, or cited unknown URLs:\n"
                + "\n".join(f"- {c}" for c in unsourced_claims[:5])
                + "\n\nPlease regenerate the complete briefing. Ensure EVERY factual claim is supported by an inline citation link [anchor](url) strictly using the URLs from the research findings."
            )
            state["grounding_correction"] = corrective_msg
        else:
            # Second failure: Surface the draft with the ungrounded claims explicitly listed
            state["grounding_retry_needed"] = False
            unsourced_warning = (
                "\n\n> [!WARNING] **Grounding Notice: The following claims could not be verified against research sources:**\n"
                + "\n".join(f"> - {claim}" for claim in unsourced_claims)
                + "\n"
            )
            # Append warning to draft
            annotated_draft = draft_text + unsourced_warning
            state["brief_draft"] = annotated_draft
            from google.genai import types
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=annotated_draft)],
                )
            )

        return None
