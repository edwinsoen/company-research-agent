"""FastAPI REST server for Meeting Prep Copilot.

Implements two-leg execution with non-blocking pauses for Human-In-The-Loop (HITL)
gates, with pending-call recovery directly from session events (no auxiliary database).

Note: Per-request user_token passing is a local development and testing stand-in
for end-user delegation. Production deployments rely on the Agent Identity Auth Manager /
ToolContext credentials per HLD §12A.2. Raw credentials are kept in transient in-memory
mapping during execution and are never persisted to session state or backing stores.

Endpoints:
- POST /briefs: Runs Leg 1 until HITL gate pause or completion.
- POST /briefs/{id}/decision: Runs Leg 2 by posting FunctionResponse to resume execution.
- GET /briefs/{id}: Retrieves current session state and brief metadata.
- GET /health: Health check endpoint.

Source: docs/hld.md §10.2, §10.3, §12.2, §12A.2
"""

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, status
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel, Field

from meeting_prep.app import app as adk_app
from meeting_prep.auth import (
    set_session_delegated_token,
    clear_session_delegated_token,
)
from meeting_prep.config import (
    MODEL_NAME,
    PROJECT_ID,
    LOCATION,
    get_session_service,
    get_artifact_service,
)

logger = logging.getLogger(__name__)

server = FastAPI(
    title="Meeting Prep Copilot REST API",
    description="Programmatic REST interface for ADK multi-agent research assistant.",
    version="0.1.0",
)

# Global services (managed Vertex AI or in-memory depending on environment)
session_service = get_session_service()
artifact_service = get_artifact_service()
runner = Runner(
    app=adk_app,
    session_service=session_service,
    artifact_service=artifact_service,
)


class CreateBriefRequest(BaseModel):
    prompt: str = Field(description="User prompt requesting executive research brief.")
    user_id: str = Field(default="executive_user", description="Caller user identifier owning the brief session.")
    user_token: Optional[str] = Field(default=None, description="Optional user-delegated Google Drive OAuth bearer token.")


class DecisionRequest(BaseModel):
    status: str = Field(description="Decision status: 'approved' or 'revise'.")
    comment: Optional[str] = Field(default=None, description="Optional revision instructions.")
    candidate: Optional[dict[str, Any]] = Field(
        default=None, description="Optional selected entity candidate if resolving disambiguation."
    )
    user_id: Optional[str] = Field(default=None, description="User identifier owning the session.")
    user_token: Optional[str] = Field(default=None, description="Optional user-delegated Google Drive OAuth bearer token.")


async def resolve_session(session_id: str, user_id: Optional[str] = None):
    """Retrieve session strictly scoped to user_id without cross-user scanning (HLD §12A.3)."""
    if not user_id:
        return None
    try:
        return await session_service.get_session(
            app_name=adk_app.name,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as err:
        logger.debug("Failed to retrieve session %s for user %s: %s", session_id, user_id, err)
        return None


class BriefResponse(BaseModel):
    status: str = Field(description="'paused' (at gate), 'completed' (published), or 'failed'.")
    brief_id: str = Field(description="Unique brief session identifier.")
    session_id: str = Field(description="ADK session identifier.")
    gate: Optional[str] = Field(default=None, description="Name of the pending gate function if paused.")
    payload: Optional[dict[str, Any]] = Field(default=None, description="Arguments or draft content at gate.")
    doc_url: Optional[str] = Field(default=None, description="Google Doc URL if completed.")
    draft: Optional[str] = Field(default=None, description="Generated brief markdown text.")
    version: Optional[int] = Field(default=1, description="Draft version.")


def derive_brief_status(state: dict[str, Any], pending_gate: Optional[tuple]) -> str:
    """Accurately derive brief status from session state and pending gate (HLD §10.3)."""
    if pending_gate:
        return "paused"
    if state.get("published_doc_url"):
        return "completed"
    return "failed"


def extract_gate_call_from_event(event) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Extract pending gate (id, name, args) from a non-partial long-running event."""
    lr_ids = getattr(event, "long_running_tool_ids", None)
    if not lr_ids or getattr(event, "partial", False):
        return None
    content = getattr(event, "content", None)
    if not content or not content.parts:
        return None
    for part in content.parts:
        fc = getattr(part, "function_call", None)
        if fc and fc.id in lr_ids:
            return (fc.id, fc.name, fc.args or {})
    return None


def get_unanswered_pending_gate(session) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Recover unanswered pending FunctionCall from session events (HLD §10.2)."""
    answered_ids: set[str] = set()
    for event in session.events:
        content = getattr(event, "content", None)
        if content and content.parts:
            for part in content.parts:
                fr = getattr(part, "function_response", None)
                if fr and fr.id:
                    answered_ids.add(fr.id)

    for event in reversed(session.events):
        lr_ids = getattr(event, "long_running_tool_ids", None)
        if not lr_ids or getattr(event, "partial", False):
            continue
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        for part in content.parts:
            fc = getattr(part, "function_call", None)
            if fc and fc.id in lr_ids and fc.id not in answered_ids:
                return (fc.id, fc.name, fc.args or {})
    return None


@server.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "meeting_prep_copilot",
        "model": MODEL_NAME,
        "project": PROJECT_ID,
        "location": LOCATION,
    }


@server.post("/briefs", response_model=BriefResponse, status_code=status.HTTP_201_CREATED)
async def create_brief(req: CreateBriefRequest):
    """Start Leg 1 execution of the brief research pipeline.

    Runs until a HITL gate pause (request_disambiguation or approve_brief)
    or completion.
    """
    user_id = req.user_id or "executive_user"
    session = await session_service.create_session(
        app_name=adk_app.name,
        user_id=user_id,
        state={},
    )
    if req.user_token:
        set_session_delegated_token(session.id, req.user_token)

    user_msg = types.Content(
        role="user",
        parts=[types.Part.from_text(text=req.prompt)],
    )

    pending_gate = None
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=user_msg,
    ):
        gate = extract_gate_call_from_event(event)
        if gate:
            pending_gate = gate

    updated_session = await session_service.get_session(
        app_name=adk_app.name,
        user_id=user_id,
        session_id=session.id,
    )
    state = updated_session.state if updated_session else {}
    brief_status = derive_brief_status(state, pending_gate)
    if brief_status in ("completed", "failed"):
        clear_session_delegated_token(session.id)

    if pending_gate:
        call_id, func_name, args = pending_gate
        return BriefResponse(
            status=brief_status,
            brief_id=session.id,
            session_id=session.id,
            gate=func_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status=brief_status,
        brief_id=session.id,
        session_id=session.id,
        doc_url=state.get("published_doc_url"),
        draft=state.get("brief_draft"),
        version=state.get("draft_version", 1),
    )


@server.post("/briefs/{session_id}/decision", response_model=BriefResponse)
async def submit_decision(session_id: str, decision: DecisionRequest, user_id: Optional[str] = None):
    """Submit human decision for a pending gate and execute Leg 2.

    Reconstructs the pending FunctionResponse using the call_id and name recovered
    from the session's event history.
    """
    effective_user_id = decision.user_id or user_id
    if not effective_user_id:
        raise HTTPException(
            status_code=400,
            detail="user_id is required in decision payload or query parameter to resolve session.",
        )

    session = await resolve_session(session_id, effective_user_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found for user '{effective_user_id}'.")

    pending_call = get_unanswered_pending_gate(session)
    if not pending_call:
        raise HTTPException(
            status_code=400,
            detail=f"No active pending gate found for session '{session_id}'.",
        )

    call_id, func_name, _ = pending_call

    # If user provided a delegated token, store in transient in-memory session store (HLD §12A.2)
    if decision.user_token:
        set_session_delegated_token(session.id, decision.user_token)

    # Build response payload matching the pending tool contract
    if func_name == "request_disambiguation":
        if not decision.candidate and not (decision.comment and decision.comment.strip()):
            raise HTTPException(
                status_code=400,
                detail="candidate entity selection (or comment) is required for request_disambiguation gate.",
            )
        response_payload = decision.candidate if decision.candidate is not None else {"name": decision.comment.strip()}
    else:
        # approve_brief
        decision_status = (decision.status or "").lower().strip()
        if decision_status not in {"approved", "revise"}:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid decision status '{decision.status}'. Must be 'approved' or 'revise'.",
            )
        if decision_status == "revise" and not (decision.comment and decision.comment.strip()):
            raise HTTPException(
                status_code=400,
                detail="Revision feedback comment is required when status is 'revise'.",
            )
        response_payload = {
            "status": decision_status,
            "comment": decision.comment.strip() if decision.comment else None,
        }

    resume_msg = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=func_name,
                    response=response_payload,
                )
            )
        ],
    )

    pending_gate = None
    async for event in runner.run_async(
        user_id=effective_user_id,
        session_id=session.id,
        new_message=resume_msg,
    ):
        gate = extract_gate_call_from_event(event)
        if gate:
            pending_gate = gate

    updated_session = await session_service.get_session(
        app_name=adk_app.name,
        user_id=effective_user_id,
        session_id=session.id,
    )
    state = updated_session.state if updated_session else {}
    brief_status = derive_brief_status(state, pending_gate)
    if brief_status in ("completed", "failed"):
        clear_session_delegated_token(session.id)

    if pending_gate:
        c_id, f_name, args = pending_gate
        return BriefResponse(
            status=brief_status,
            brief_id=session.id,
            session_id=session.id,
            gate=f_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status=brief_status,
        brief_id=session.id,
        session_id=session.id,
        doc_url=state.get("published_doc_url"),
        draft=state.get("brief_draft"),
        version=state.get("draft_version", 1),
    )


@server.get("/briefs/{session_id}", response_model=BriefResponse)
async def get_brief(session_id: str, user_id: Optional[str] = None):
    """Retrieve the current status of a brief session."""
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id query parameter is required to retrieve brief.")

    session = await resolve_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found for user '{user_id}'.")

    pending_call = get_unanswered_pending_gate(session)
    state = session.state
    brief_status = derive_brief_status(state, pending_call)

    if pending_call:
        _, f_name, args = pending_call
        return BriefResponse(
            status=brief_status,
            brief_id=session.id,
            session_id=session.id,
            gate=f_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status=brief_status,
        brief_id=session.id,
        session_id=session.id,
        doc_url=state.get("published_doc_url"),
        draft=state.get("brief_draft"),
        version=state.get("draft_version", 1),
    )
