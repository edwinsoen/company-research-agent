"""GroundingGuardPlugin: Deterministic self-evaluation policy for citation fidelity.

Source: docs/orchestration-and-logic-enhancements.md §1.3 & §2.2
Enforces:
1. Extract every claim line from the draft.
2. Assert each carries a source URL.
3. Assert each URL appears in the research_* findings in session state.
4. On failure, reject draft and regenerate with corrective instruction on PRO tier.
5. On repeated failure, surface draft with unsourced claims listed.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any, Optional, Set
from urllib.parse import urlparse

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types
from opentelemetry import trace
from meeting_prep.models import PRO

logger = logging.getLogger(__name__)

# Pattern to extract markdown links [anchor text](url)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
URL_PATTERN = re.compile(r"https?://[^\s\)\>\]]+")


def extract_known_research_urls(state: dict[str, Any]) -> tuple[Set[str], Set[str]]:
    """Collect all valid source URLs and domain netlocs present in research findings."""
    known_urls: Set[str] = set()
    known_domains: Set[str] = set()

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
                url = item.get("source_url") or item.get("url")
            elif hasattr(item, "source_url"):
                url = getattr(item, "source_url", None)

            if url and isinstance(url, str):
                cleaned = url.strip().rstrip("/")
                if cleaned:
                    known_urls.add(cleaned)
                    known_urls.add(url.strip())
                    try:
                        netloc = urlparse(cleaned).netloc.lower()
                        if netloc:
                            known_domains.add(netloc)
                            if netloc.startswith("www."):
                                known_domains.add(netloc[4:])
                    except Exception:
                        pass

    return known_urls, known_domains


def is_valid_citation(url_str: str, known_normalized: Set[str], known_domains: Set[str]) -> bool:
    """Validate if a URL matches known research sources either by exact URL or domain."""
    clean_u = url_str.strip().rstrip("/")
    if clean_u in known_normalized or url_str.strip() in known_normalized:
        return True
    try:
        netloc = urlparse(clean_u).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if netloc in known_domains:
            return True
    except Exception:
        pass
    return False


def is_structural_line(line: str) -> bool:
    """Check if a line is structural Markdown, heading, table, quote, or non-factual text."""
    stripped = line.strip()
    if not stripped:
        return True

    # Markdown headers (# Header)
    if stripped.startswith("#"):
        return True

    # Horizontal rules (---, ***, ___)
    if stripped in ("---", "***", "___") or (len(stripped) >= 3 and set(stripped) <= {"-", "*", "_"}):
        return True

    # Table rows (| Col 1 | Col 2 |) or table separators (|---|---|)
    if stripped.startswith("|"):
        return True

    # Blockquotes (> text)
    if stripped.startswith(">"):
        return True

    # Code blocks or fences
    if stripped.startswith("```") or stripped.startswith("`"):
        return True

    # Entire line in italics (*...* or _..._)
    if (stripped.startswith("*") and stripped.endswith("*") and len(stripped) > 1) or (
        stripped.startswith("_") and stripped.endswith("_") and len(stripped) > 1
    ):
        return True

    # Warning callout blocks from prior grounding checks
    if stripped.startswith("> [!WARNING]") or stripped.startswith("> Unsourced claim:"):
        return True

    # Category or section intro lines ending with a colon (e.g. "**Overview & Governance:**" or "Key Highlights:")
    clean_no_md = re.sub(r"[\*\_\#\-\+\d\.\s]", "", stripped)
    if not clean_no_md:
        return True
    if stripped.rstrip("*_ ").endswith(":"):
        return True

    # Short boilerplate / disclaimers (e.g. fewer than 4 alphanumeric words)
    words = re.findall(r"\b[A-Za-z0-9]+\b", stripped)
    if len(words) < 4:
        return True

    return False


class GroundingGuardPlugin(BasePlugin):
    """Deterministic, zero-LLM guardrail plugin verifying composer drafts against research findings."""

    def __init__(self) -> None:
        super().__init__(name="grounding_guard_plugin")
        self._last_requests: dict[str, LlmRequest] = {}

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        """Capture incoming LlmRequest for composer to enable Pro regeneration if needed."""
        agent_name = getattr(callback_context, "agent_name", "")
        if agent_name == "composer":
            key = getattr(callback_context, "invocation_id", "") or "default"
            if len(self._last_requests) > 50:
                oldest_keys = list(self._last_requests.keys())[:25]
                for k in oldest_keys:
                    self._last_requests.pop(k, None)
            self._last_requests[key] = llm_request
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Validate composer output for citation grounding with fail-safe Pro escalation."""
        agent_name = getattr(callback_context, "agent_name", "")
        if agent_name != "composer":
            return None

        key = getattr(callback_context, "invocation_id", "") or "default"
        # Always evict cached request to avoid memory leaks
        last_req = self._last_requests.pop(key, None)

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
        known_urls, known_domains = extract_known_research_urls(state)
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

            # Check markdown links and raw URLs
            markdown_links = MARKDOWN_LINK_PATTERN.findall(clean_line)
            raw_urls = URL_PATTERN.findall(clean_line)

            if not markdown_links and not raw_urls:
                # Content line without any citation link
                unsourced_claims.append(clean_line)
                continue

            # Verify cited URLs against known research findings
            cited_urls = [link[1].strip() for link in markdown_links] + [u.strip() for u in raw_urls]
            valid_claim = False
            for cited_url in cited_urls:
                if is_valid_citation(cited_url, known_normalized, known_domains):
                    valid_claim = True
                else:
                    invalid_urls.append(cited_url)

            if not valid_claim:
                unsourced_claims.append(f"{clean_line} (Invalid URLs: {', '.join(cited_urls)})")

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
            state["brief_draft"] = draft_text
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

        def _build_warning_draft(base_draft: str, claims: list[str]) -> LlmResponse:
            """Fail-safe: annotate draft with ungrounded claims warning."""
            warning = (
                "\n\n> [!WARNING] **Grounding Notice: The following claims could not be verified against research sources:**\n"
                + "\n".join(f"> - {c}" for c in claims)
                + "\n"
            )
            annotated = base_draft + warning
            state["brief_draft"] = annotated
            state["grounding_retry_needed"] = False
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=annotated)],
                )
            )

        if current_attempt == 1:
            # First failure: Escalate to PRO and prepare corrective instruction
            state["composer_model"] = PRO
            state["grounding_retry_needed"] = True
            corrective_msg = (
                "Grounding self-check failed on your previous draft. "
                "The following claims were missing inline Markdown citations to valid research URLs, or cited unknown URLs:\n"
                + "\n".join(f"- {c}" for c in unsourced_claims[:5])
                + "\n\nPlease regenerate the complete briefing. Ensure EVERY factual claim is supported by an inline citation link [anchor](url) strictly using the URLs from the research findings."
            )
            state["grounding_correction"] = corrective_msg

            # Check budget ceilings before invoking Pro
            current_calls = int(state.get("budget_model_calls", 0))
            current_tokens = int(state.get("budget_total_tokens", 0))
            max_calls = int(os.getenv("BUDGET_MAX_MODEL_CALLS", "25"))
            max_tokens = int(os.getenv("BUDGET_MAX_TOKENS", "150000"))

            if current_calls >= max_calls or current_tokens >= max_tokens:
                logger.warning("GroundingGuardPlugin: Budget ceiling reached; skipping Pro regeneration.")
                return _build_warning_draft(draft_text, unsourced_claims)

            if not last_req:
                logger.warning("GroundingGuardPlugin: No cached LlmRequest for composer invocation; falling back to annotated draft.")
                return _build_warning_draft(draft_text, unsourced_claims)

            try:
                from google.adk.models.registry import LLMRegistry
                pro_llm = LLMRegistry.new_llm(PRO)
                retry_req = copy.deepcopy(last_req)
                retry_req.model = PRO
                retry_req.contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=corrective_msg)])
                )

                # Emit OpenTelemetry call_llm span
                tracer = trace.get_tracer("meeting_prep.plugins.grounding")
                pro_response = None
                with tracer.start_as_current_span("call_llm") as span:
                    span.set_attribute("gen_ai.system", "google")
                    span.set_attribute("gen_ai.request.model", PRO)
                    span.set_attribute("subagent.model", PRO)
                    span.set_attribute("subagent.name", "composer_pro_escalation")

                    async for chunk in pro_llm.generate_content_async(retry_req, stream=False):
                        pro_response = chunk

                # Account for Pro call in budget counters
                state["budget_model_calls"] = current_calls + 1
                if pro_response and hasattr(pro_response, "usage_metadata") and pro_response.usage_metadata:
                    u = pro_response.usage_metadata
                    p_tok = getattr(u, "prompt_token_count", 0) or 0
                    c_tok = getattr(u, "candidates_token_count", 0) or 0
                    t_tok = getattr(u, "total_token_count", 0) or (p_tok + c_tok)
                    state["budget_input_tokens"] = int(state.get("budget_input_tokens", 0)) + p_tok
                    state["budget_output_tokens"] = int(state.get("budget_output_tokens", 0)) + c_tok
                    state["budget_total_tokens"] = int(state.get("budget_total_tokens", 0)) + t_tok

                if pro_response:
                    regen_text = ""
                    if pro_response.content and pro_response.content.parts:
                        for p in pro_response.content.parts:
                            if getattr(p, "text", ""):
                                regen_text += p.text

                    # Validate attempt 2 claims
                    regen_unsourced: list[str] = []
                    for line in regen_text.splitlines():
                        clean_line = line.strip()
                        if is_structural_line(clean_line):
                            continue
                        m_links = MARKDOWN_LINK_PATTERN.findall(clean_line)
                        r_urls = URL_PATTERN.findall(clean_line)
                        if not m_links and not r_urls:
                            regen_unsourced.append(clean_line)
                        else:
                            c_urls = [l[1].strip() for l in m_links] + [x.strip() for x in r_urls]
                            valid_claim = any(
                                is_valid_citation(u, known_normalized, known_domains) for u in c_urls
                            )
                            if not valid_claim:
                                regen_unsourced.append(f"{clean_line} (Invalid URLs: {', '.join(c_urls)})")

                    state["grounding_attempts"] = 2
                    state["grounding_retry_needed"] = False

                    if regen_unsourced:
                        return _build_warning_draft(regen_text, regen_unsourced)

                    state["brief_draft"] = regen_text
                    return pro_response
            except Exception as err:
                logger.warning("Dynamic Pro regeneration in GroundingGuardPlugin failed: %s", err)
                return _build_warning_draft(draft_text, unsourced_claims)

            # In case pro_response was empty
            return _build_warning_draft(draft_text, unsourced_claims)
        else:
            # Second failure: Surface the draft with the ungrounded claims explicitly listed
            return _build_warning_draft(draft_text, unsourced_claims)
