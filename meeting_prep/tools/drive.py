"""Google Drive publishing tools.

In Phase 1, stubs return mock doc references.
In Phase 3, these will use Google Drive API with user-delegated auth and idempotency.
"""

from typing import Any, Optional
from google.adk.tools.tool_context import ToolContext


def create_google_doc(
    title: str,
    markdown: str,
    brief_id: str = "brief-1",
    version: int = 1,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Create a new Google Doc from brief markdown content.

    Args:
        title: Document title.
        markdown: Markdown brief draft.
        brief_id: Unique brief identifier for idempotency.
        version: Version number of the draft.
        tool_context: ADK tool context.

    Returns:
        dict: DocRef with doc_id, doc_url, title, and version.
    """
    mock_doc_id = f"mock-doc-{brief_id}-v{version}"
    mock_doc_url = f"https://docs.google.com/document/d/{mock_doc_id}/edit"

    doc_ref = {
        "doc_id": mock_doc_id,
        "doc_url": mock_doc_url,
        "title": title,
        "version": version,
    }

    if tool_context and hasattr(tool_context, "actions"):
        tool_context.actions.state_delta["published_doc_url"] = mock_doc_url

    return doc_ref


def share_doc(
    doc_id: str,
    emails: list[str],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Share a Google Doc with recipients.

    Args:
        doc_id: Document identifier.
        emails: List of recipient email addresses.
        tool_context: ADK tool context.

    Returns:
        dict: Sharing results per recipient.
    """
    results = {email: "success" for email in emails}
    return {"shared": results, "doc_id": doc_id}
