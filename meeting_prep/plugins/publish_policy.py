"""PublishPolicyPlugin: Hard policy gate on document publishing tools.

Enforces:
1. approval_decision.status == "approved" in session state.
2. Every recipient is within ALLOWED_RECIPIENT_DOMAINS.
3. Idempotency key (brief_id, draft_version) tracking to prevent duplicate document creations.

Source: docs/orchestration-and-logic-enhancements.md §2.2
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Allowed recipient domains configuration (comma-separated list, e.g. "corp.com,google.com")
_ENV_ALLOWED_DOMAINS = os.getenv("ALLOWED_RECIPIENT_DOMAINS", "*").strip()


class PublishPolicyPlugin(BasePlugin):
    """ADK BasePlugin that runtime-enforces document publication and sharing policies."""

    def __init__(
        self,
        allowed_domains: Optional[list[str]] = None,
    ) -> None:
        super().__init__(name="publish_policy_plugin")
        if allowed_domains is not None:
            self.allowed_domains = [d.strip().lower() for d in allowed_domains if d.strip()]
        elif _ENV_ALLOWED_DOMAINS and _ENV_ALLOWED_DOMAINS != "*":
            self.allowed_domains = [d.strip().lower() for d in _ENV_ALLOWED_DOMAINS.split(",") if d.strip()]
        else:
            self.allowed_domains = ["*"]

    def _is_domain_allowed(self, email: str) -> bool:
        if "*" in self.allowed_domains:
            return True
        if "@" not in email:
            return False
        domain = email.split("@", 1)[1].strip().lower()
        return domain in self.allowed_domains

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict[str, Any]]:
        """Intercept create_google_doc and share_doc to enforce approval, domain allowlist, and idempotency."""
        tool_name = getattr(tool, "name", "")
        if tool_name not in ("create_google_doc", "share_doc"):
            return None

        state = tool_context.state

        # 1. Assert human approval status
        approval_decision = state.get("approval_decision") or {}
        if isinstance(approval_decision, dict):
            status = approval_decision.get("status")
        else:
            status = getattr(approval_decision, "status", None)

        if status != "approved":
            logger.warning(
                "PublishPolicyPlugin DENIED tool '%s': approval_decision.status is '%s' (not 'approved')",
                tool_name,
                status,
                extra={"event_type": "guardrail_violation", "tool": tool_name, "status": status},
            )
            return {
                "status": "denied",
                "error": f"Publish policy violation: tool '{tool_name}' requires approval_decision.status == 'approved' in session state. Current status: '{status}'.",
            }

        # 2. Assert recipient domain allowlist
        if tool_name == "share_doc":
            recipients = tool_args.get("emails") or tool_args.get("recipients") or []
            if isinstance(recipients, str):
                recipients = [recipients]
            for recipient in recipients:
                if not self._is_domain_allowed(recipient):
                    logger.warning(
                        "PublishPolicyPlugin DENIED tool '%s': recipient '%s' not in allowed domains %s",
                        tool_name,
                        recipient,
                        self.allowed_domains,
                        extra={"event_type": "guardrail_violation", "tool": tool_name, "recipient": recipient},
                    )
                    return {
                        "status": "denied",
                        "error": f"Publish policy violation: recipient '{recipient}' domain is not in ALLOWED_RECIPIENT_DOMAINS ({self.allowed_domains}).",
                    }

        # Also check user_preferences.recipients if create_google_doc is invoked
        if tool_name == "create_google_doc":
            user_prefs = state.get("user_preferences") or {}
            recipients = user_prefs.get("recipients") if isinstance(user_prefs, dict) else getattr(user_prefs, "recipients", [])
            if recipients:
                for recipient in recipients:
                    if not self._is_domain_allowed(recipient):
                        logger.warning(
                            "PublishPolicyPlugin DENIED tool '%s': configured recipient '%s' not allowed",
                            tool_name,
                            recipient,
                            extra={"event_type": "guardrail_violation", "recipient": recipient},
                        )
                        return {
                            "status": "denied",
                            "error": f"Publish policy violation: configured recipient '{recipient}' domain is not in ALLOWED_RECIPIENT_DOMAINS.",
                        }

        # 3. Idempotency key (brief_id, draft_version)
        brief_id = str(tool_args.get("brief_id") or state.get("brief_id") or (state.get("resolved_entity") or {}).get("name") or "default_brief")
        version = int(tool_args.get("version") or state.get("draft_version") or 1)
        idempotency_key = f"{brief_id}:v{version}"

        published_cache = state.get("_published_idempotency_cache")
        if not isinstance(published_cache, dict):
            published_cache = {}
            state["_published_idempotency_cache"] = published_cache

        if tool_name == "create_google_doc" and idempotency_key in published_cache:
            cached_result = published_cache[idempotency_key]
            logger.info(
                "PublishPolicyPlugin IDEMPOTENT HIT: tool '%s' key '%s' previously executed",
                tool_name,
                idempotency_key,
                extra={"event_type": "idempotency_hit", "key": idempotency_key},
            )
            return {
                "status": "idempotent_cached",
                "doc_id": cached_result.get("doc_id"),
                "doc_url": cached_result.get("doc_url"),
                "message": f"Document for {idempotency_key} already published. Returning existing doc.",
            }

        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Record successful create_google_doc execution in idempotency cache."""
        tool_name = getattr(tool, "name", "")
        if (
            tool_name == "create_google_doc"
            and isinstance(result, dict)
            and (result.get("doc_url") or result.get("status") == "success")
            and not result.get("error")
        ):
            state = tool_context.state
            brief_id = str(tool_args.get("brief_id") or state.get("brief_id") or (state.get("resolved_entity") or {}).get("name") or "default_brief")
            version = int(tool_args.get("version") or state.get("draft_version") or 1)
            idempotency_key = f"{brief_id}:v{version}"

            published_cache = dict(state.get("_published_idempotency_cache") or {})
            published_cache[idempotency_key] = {
                "doc_id": result.get("doc_id"),
                "doc_url": result.get("doc_url"),
            }
            state["_published_idempotency_cache"] = published_cache
            if hasattr(tool_context, "actions") and tool_context.actions:
                tool_context.actions.state_delta["_published_idempotency_cache"] = published_cache

        return None
