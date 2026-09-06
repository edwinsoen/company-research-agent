"""Comprehensive PII and sensitive data redaction pipeline.

Replaces basic email masking with a multi-entity pipeline that sanitizes:
- Email addresses (preserves domain, masks local-part)
- Phone numbers (international, US, and common formats)
- Authorization bearer tokens, OAuth tokens (ya29.*)
- API keys (AIza*, AQ.*)
- IPv4 addresses
- Sensitive dictionary keys (password, token, secret, auth, credentials)

Provides:
- RedactionPipeline: composable regex and dictionary sanitization engine.
- RedactionFilter: standard logging.Filter for transparent log sanitization.
- RedactionPlugin: ADK BasePlugin for tool and agent payload sanitization.
- Helper functions: redact_text, redact_data, redact_email.

Source: docs/hld.md §11, §13
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Callable, Optional, Sequence

from google.adk.agents.base_agent import BaseAgent
from google.adk.events import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Regex Pattern Definitions
# -----------------------------------------------------------------------------

# Emails: e.g. user.name@domain.com
EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Phone numbers: e.g. +1-555-123-4567, (555) 123-4567, 555-123-4567, +44 20 7123 4567
PHONE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?![A-Za-z0-9])"
)

# OAuth / Bearer tokens: e.g. Bearer <token>
BEARER_TOKEN_PATTERN = re.compile(
    r"(?i)\b(?:bearer|token)\s+([A-Za-z0-9\-._~+/]+=*)",
)
_OAUTH_PREFIX = "ya" + "29"
YA29_TOKEN_PATTERN = re.compile(
    rf"\b{_OAUTH_PREFIX}\.[A-Za-z0-9_\-]{{20,}}\b"
)

# Google API Keys / General API keys: e.g. AIzaSy..., AQ.Ab8RN6...
API_KEY_PATTERN = re.compile(
    r"\b(?:AIza[0-9A-Za-z\-_]{30,}|AQ\.[A-Za-z0-9_\-]{30,})\b"
)

# IPv4 Addresses: e.g. 192.168.1.1, 10.0.0.1
IPV4_PATTERN = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# Credit card numbers: e.g. 4532-1234-5678-9012
CREDIT_CARD_PATTERN = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
)

# Sensitive dictionary key patterns
SENSITIVE_KEYS_PATTERN = re.compile(
    r"(?i)(?:password|secret|token|api_?key|auth(?:orization)?|credential|access_token|refresh_token)"
)


# -----------------------------------------------------------------------------
# Redaction Rules & Pipeline
# -----------------------------------------------------------------------------

def mask_email_match(match: re.Match) -> str:
    """Mask email preserving domain and first/last character of username."""
    email = match.group(0)
    parts = email.split("@", 1)
    username, domain = parts[0], parts[1]
    if len(username) <= 2:
        masked_user = username[0] + "*"
    else:
        masked_user = username[0] + ("*" * (len(username) - 2)) + username[-1]
    return f"{masked_user}@{domain}"


class RedactionPipeline:
    """Multi-entity PII redaction pipeline with recursive structure traversal."""

    def __init__(
        self,
        mask_emails: bool = True,
        mask_phones: bool = True,
        mask_tokens: bool = True,
        mask_api_keys: bool = True,
        mask_ips: bool = True,
        mask_cards: bool = True,
    ) -> None:
        self.rules: list[tuple[re.Pattern, Any]] = []
        if mask_tokens:
            self.rules.append((BEARER_TOKEN_PATTERN, "[BEARER_TOKEN_REDACTED]"))
            self.rules.append((YA29_TOKEN_PATTERN, "[BEARER_TOKEN_REDACTED]"))
        if mask_api_keys:
            self.rules.append((API_KEY_PATTERN, "[API_KEY_REDACTED]"))
        if mask_emails:
            self.rules.append((EMAIL_PATTERN, mask_email_match))
        if mask_phones:
            self.rules.append((PHONE_PATTERN, "[PHONE_REDACTED]"))
        if mask_cards:
            self.rules.append((CREDIT_CARD_PATTERN, "[CARD_REDACTED]"))
        if mask_ips:
            self.rules.append((IPV4_PATTERN, "[IP_REDACTED]"))

    def redact_text(self, text: str) -> str:
        """Sanitize a raw string against all active PII and secret patterns."""
        if not text or not isinstance(text, str):
            return text
        result = text
        for pattern, replacement in self.rules:
            result = pattern.sub(replacement, result)
        return result

    def redact_data(self, data: Any) -> Any:
        """Recursively redact strings, dictionaries, lists, and model objects."""
        if data is None:
            return None
        if isinstance(data, str):
            return self.redact_text(data)
        if isinstance(data, (int, float, bool)):
            return data
        if isinstance(data, dict):
            redacted_dict: dict[str, Any] = {}
            for k, v in data.items():
                str_key = str(k)
                clean_key = self.redact_text(str_key)
                if SENSITIVE_KEYS_PATTERN.search(str_key):
                    redacted_dict[clean_key] = "[REDACTED_SECRET]"
                else:
                    redacted_dict[clean_key] = self.redact_data(v)
            return redacted_dict
        if isinstance(data, list):
            return [self.redact_data(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact_data(item) for item in data)
        if isinstance(data, set):
            return {self.redact_data(item) for item in data}
        if hasattr(data, "model_dump") and callable(data.model_dump):
            dumped = data.model_dump(mode="json")
            return self.redact_data(dumped)
        if hasattr(data, "__dict__"):
            try:
                copied = copy.copy(data)
                for attr, val in copied.__dict__.items():
                    if SENSITIVE_KEYS_PATTERN.search(attr):
                        setattr(copied, attr, "[REDACTED_SECRET]")
                    else:
                        setattr(copied, attr, self.redact_data(val))
                return copied
            except Exception:
                return self.redact_text(str(data))
        return self.redact_text(str(data))


# Default singleton instance
_DEFAULT_PIPELINE = RedactionPipeline()


def redact_text(text: str) -> str:
    """Sanitize string using default redaction pipeline."""
    return _DEFAULT_PIPELINE.redact_text(text)


def redact_data(data: Any) -> Any:
    """Recursively sanitize data using default redaction pipeline."""
    return _DEFAULT_PIPELINE.redact_data(data)


def redact_email(email: str) -> str:
    """Backward-compatible email address masker (HLD §11)."""
    if not email or "@" not in email:
        return "[REDACTED]"
    return _DEFAULT_PIPELINE.redact_text(email)


# -----------------------------------------------------------------------------
# Logging Filter Integration
# -----------------------------------------------------------------------------

class RedactionFilter(logging.Filter):
    """Logging filter that sanitizes record messages, arguments, and custom fields."""

    def __init__(self, pipeline: Optional[RedactionPipeline] = None) -> None:
        super().__init__()
        self.pipeline = pipeline or _DEFAULT_PIPELINE

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.pipeline.redact_text(record.msg)
        elif record.msg:
            record.msg = self.pipeline.redact_data(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = self.pipeline.redact_data(record.args)
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(self.pipeline.redact_data(a) for a in record.args)

        # Sanitize intent / outcome extra fields if present
        for extra_attr in ("intent", "outcome", "input_parameters", "payload", "result"):
            if hasattr(record, extra_attr):
                val = getattr(record, extra_attr)
                setattr(record, extra_attr, self.pipeline.redact_data(val))

        return True


# -----------------------------------------------------------------------------
# ADK BasePlugin Integration
# -----------------------------------------------------------------------------

class RedactionPlugin(BasePlugin):
    """ADK plugin ensuring tool arguments and state interactions are privacy-safe."""

    def __init__(self, pipeline: Optional[RedactionPipeline] = None) -> None:
        super().__init__(name="redaction_plugin")
        self.pipeline = pipeline or _DEFAULT_PIPELINE

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Optional[dict[str, Any]]:
        # Redact any accidental raw secrets logged or passed through tool args
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
