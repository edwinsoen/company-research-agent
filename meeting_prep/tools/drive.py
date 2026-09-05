"""Google Drive publishing tools with idempotency and graceful degradation.

Supports dual-mode execution:
- 'stub' mode (default for local dev & CI): deterministic mock responses with creation tracking.
- 'drive' mode (deployment & live testing): creates Google Docs via Drive API v3 with
  markdown-to-document conversion and manages permissions with retry/backoff.

Source: docs/hld.md §10.5, §11, §12A
"""

import logging
import os
import re
from typing import Any, Optional

import requests
from google.adk.tools.tool_context import ToolContext
import tenacity

logger = logging.getLogger(__name__)

# Global tracker for stub mode creation events (useful for idempotency assertions in tests)
_STUB_CREATION_COUNT: dict[str, int] = {}


class DriveError(Exception):
    """Base exception for Google Drive API operations."""
    pass


class TransientDriveError(DriveError):
    """Transient error (e.g. 429, 5xx, network timeout) that is safe to retry."""
    pass


class PermanentDriveError(DriveError):
    """Permanent error (e.g. 400, 403, 404) that should not be retried."""
    pass


def get_stub_creation_count(brief_id: str, version: int) -> int:
    """Get the number of times a document was actively created in stub mode."""
    return _STUB_CREATION_COUNT.get(f"{brief_id}:v{version}", 0)


def reset_stub_creation_counts() -> None:
    """Reset stub creation counters."""
    _STUB_CREATION_COUNT.clear()


def redact_email(email: str) -> str:
    """Mask email address for privacy-safe logging and tracing (HLD §11)."""
    if not email or "@" not in email:
        return "[REDACTED]"
    parts = email.split("@", 1)
    username = parts[0]
    domain = parts[1]
    if len(username) <= 2:
        masked_user = username[0] + "*"
    else:
        masked_user = username[0] + "*" * (len(username) - 2) + username[-1]
    return f"{masked_user}@{domain}"


def _get_drive_client_mode() -> str:
    """Return configured Drive client mode: 'stub' or 'drive'."""
    return os.getenv("DRIVE_CLIENT_MODE", "stub").lower().strip()


from meeting_prep.auth import get_drive_session


def _upload_google_doc(title: str, markdown: str, tool_context: Optional[ToolContext] = None) -> dict[str, str]:
    """Upload markdown to Google Drive with mimeType conversion to native Google Doc.

    Non-idempotent create call: does not automatically retry on failure, avoiding duplicate
    document creation in Google Drive on dropped responses or timeouts (HLD §10.5, §11).
    Failures fall through to the graceful degradation branch in create_google_doc.
    """
    import json

    session = get_drive_session(tool_context=tool_context)

    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }

    boundary = "================" + os.urandom(8).hex()
    headers = {"Content-Type": f"multipart/related; boundary={boundary}"}

    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/markdown; charset=UTF-8\r\n\r\n"
        f"{markdown}\r\n"
        f"--{boundary}--\r\n"
    )

    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    try:
        response = session.post(url, data=body.encode("utf-8"), headers=headers, timeout=30)
    except Exception as err:
        logger.error("Drive API upload network error: %s", err)
        raise DriveError(f"Drive API upload network error: {err}") from err

    if not response.ok:
        logger.error("Drive API upload error (%s): %s", response.status_code, response.text)
        raise DriveError(f"Drive API upload failed ({response.status_code}): {response.text}")
    file_data = response.json()
    doc_id = file_data.get("id")
    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    return {"doc_id": doc_id, "doc_url": doc_url}


def create_google_doc(
    title: str,
    markdown: str,
    brief_id: str,
    version: int,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Create a new Google Doc from brief markdown content with strict idempotency.

    Idempotent on (brief_id, version). A repeated approval returns the existing doc URL
    and creates nothing (HLD §10.5).

    Args:
        title: Document title.
        markdown: Markdown brief draft.
        brief_id: Unique brief identifier for idempotency (e.g. canonical company name).
        version: Version number of the draft.
        tool_context: ADK tool context providing session state.

    Returns:
        dict: DocRef with doc_id, doc_url, title, version, and cached status.
    """
    canonical_brief_id = brief_id
    canonical_version = version

    # Derive canonical key deterministically from session state to avoid LLM formatting drift
    if tool_context and hasattr(tool_context, "state") and tool_context.state:
        state = tool_context.state
        resolved = state.get("resolved_entity")
        if resolved:
            if isinstance(resolved, dict) and resolved.get("name"):
                canonical_brief_id = resolved["name"]
            elif hasattr(resolved, "name") and resolved.name:
                canonical_brief_id = resolved.name
        elif state.get("company_input"):
            canonical_brief_id = state.get("company_input")

        if state.get("draft_version") is not None:
            try:
                canonical_version = int(state.get("draft_version"))
            except (ValueError, TypeError):
                pass

    cache_key = f"{canonical_brief_id}:v{canonical_version}"

    # 1. Idempotency Check in Session State
    if tool_context and hasattr(tool_context, "state"):
        published_docs = tool_context.state.get("published_docs") or {}
        if cache_key in published_docs:
            cached_doc = dict(published_docs[cache_key])
            cached_doc["cached"] = True
            logger.info(
                "Idempotency hit: Document already created for %s. Returning existing URL: %s",
                cache_key,
                cached_doc.get("doc_url"),
            )
            if hasattr(tool_context, "actions") and tool_context.actions:
                tool_context.actions.state_delta["published_doc_url"] = cached_doc.get("doc_url")
            return cached_doc

    mode = _get_drive_client_mode()
    logger.info("Executing create_google_doc in '%s' mode for %s", mode, cache_key)

    doc_id = ""
    doc_url = ""

    if mode == "drive":
        try:
            result = _upload_google_doc(title=title, markdown=markdown, tool_context=tool_context)
            doc_id = result["doc_id"]
            doc_url = result["doc_url"]
        except Exception as err:
            logger.error("Failed to create Google Doc via Drive API: %s", err, exc_info=True)
            # Graceful degradation: return structured error rather than unhandled exception
            return {
                "error": f"Drive API error: {str(err)}",
                "status": "failed",
                "title": title,
                "version": canonical_version,
                "brief_id": canonical_brief_id,
            }
    else:
        # Stub mode: deterministic ID and URL
        sanitized_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", canonical_brief_id.lower())
        doc_id = f"mock-doc-{sanitized_id}-v{canonical_version}"
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        _STUB_CREATION_COUNT[cache_key] = _STUB_CREATION_COUNT.get(cache_key, 0) + 1

    doc_ref = {
        "doc_id": doc_id,
        "doc_url": doc_url,
        "title": title,
        "version": canonical_version,
        "cached": False,
    }

    # 2. Update Session State with created document record
    if tool_context and hasattr(tool_context, "actions") and tool_context.actions:
        existing_docs = dict(tool_context.state.get("published_docs") or {})
        existing_docs[cache_key] = doc_ref
        tool_context.actions.state_delta["published_docs"] = existing_docs
        tool_context.actions.state_delta["published_doc_url"] = doc_url

    return doc_ref


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    retry=tenacity.retry_if_exception_type(TransientDriveError),
    reraise=True,
)
def _share_drive_file(doc_id: str, email: str, tool_context: Optional[ToolContext] = None) -> None:
    """Share Google Drive file with recipient email address with bounded retries on transient errors."""
    session = get_drive_session(tool_context=tool_context)

    url = f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions"
    payload = {
        "role": "reader",
        "type": "user",
        "emailAddress": email,
    }
    try:
        response = session.post(url, json=payload, timeout=15)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as err:
        logger.warning("Drive API share network timeout/error: %s", err)
        raise TransientDriveError(f"Drive API share network error: {err}") from err
    except Exception as err:
        logger.error("Drive API share unexpected request error: %s", err)
        raise PermanentDriveError(f"Drive API share error: {err}") from err

    if response.ok:
        return

    status = response.status_code
    if status == 429 or 500 <= status < 600:
        logger.warning("Drive API share transient error (%s): %s", status, response.text)
        raise TransientDriveError(f"Drive API share transient error ({status}): {response.text}")
    else:
        logger.error("Drive API share permanent error (%s): %s", status, response.text)
        raise PermanentDriveError(f"Drive API share permanent error ({status}): {response.text}")


def share_doc(
    doc_id: str,
    emails: list[str],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Share a Google Doc with recipients, with error isolation per recipient (HLD §11).

    Args:
        doc_id: Document identifier.
        emails: List of recipient email addresses.
        tool_context: ADK tool context.

    Returns:
        dict: Sharing results per recipient and failure summary.
    """
    mode = _get_drive_client_mode()
    shared_results: dict[str, str] = {}
    failed_results: dict[str, str] = {}

    for raw_email in emails:
        email = raw_email.strip()
        if not email:
            continue
        redacted = redact_email(email)
        logger.info("Sharing doc %s with recipient %s", doc_id, redacted)

        if mode == "drive":
            try:
                _share_drive_file(doc_id=doc_id, email=email, tool_context=tool_context)
                shared_results[email] = "success"
            except Exception as err:
                logger.warning("Failed to share doc %s with %s: %s", doc_id, redacted, err)
                failed_results[email] = str(err)
        else:
            # Stub mode validation
            if "@" in email and "." in email.split("@")[-1]:
                shared_results[email] = "success"
            else:
                failed_results[email] = "invalid_email_format"

    return {
        "doc_id": doc_id,
        "shared": shared_results,
        "failed": failed_results,
        "success_count": len(shared_results),
        "failure_count": len(failed_results),
    }

