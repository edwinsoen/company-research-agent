"""Comprehensive PII and sensitive data redaction pipeline.

This module re-exports components from meeting_prep.plugins.redaction for backward compatibility.
Source: docs/hld.md §11, §13
"""

from meeting_prep.plugins.redaction import (
    EMAIL_PATTERN,
    PHONE_PATTERN,
    BEARER_TOKEN_PATTERN,
    YA29_TOKEN_PATTERN,
    API_KEY_PATTERN,
    IPV4_PATTERN,
    CREDIT_CARD_PATTERN,
    SENSITIVE_KEYS_PATTERN,
    mask_email_match,
    RedactionPipeline,
    RedactionFilter,
    RedactionPlugin,
    redact_text,
    redact_data,
    redact_email,
)

__all__ = [
    "EMAIL_PATTERN",
    "PHONE_PATTERN",
    "BEARER_TOKEN_PATTERN",
    "YA29_TOKEN_PATTERN",
    "API_KEY_PATTERN",
    "IPV4_PATTERN",
    "CREDIT_CARD_PATTERN",
    "SENSITIVE_KEYS_PATTERN",
    "mask_email_match",
    "RedactionPipeline",
    "RedactionFilter",
    "RedactionPlugin",
    "redact_text",
    "redact_data",
    "redact_email",
]
