"""FastAPI REST server for Meeting Prep Copilot.

Implements two-leg execution with non-blocking pauses for Human-In-The-Loop (HITL)
gates, with pending-call recovery directly from session events (no auxiliary database).

Endpoints:
- POST /briefs: Runs Leg 1 until HITL gate pause or completion.
- POST /briefs/{id}/decision: Runs Leg 2 by posting FunctionResponse to resume execution.
- GET /briefs/{id}: Retrieves current session state and brief metadata.
- GET /health: Health check endpoint.

Source: docs/hld.md §10.2, §10.3, §12.2
"""

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, status
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel, Field

from meeting_prep.app import app as adk_app
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
    user_id: Optional[str] = Field(default="executive_user", description="Caller user identifier.")


class DecisionRequest(BaseModel):
    status: str = Field(description="Decision status: 'approved' or 'revise'.")
    comment: Optional[str] = Field(default=None, description="Optional revision instructions.")
    candidate: Optional[dict[str, Any]] = Field(
        default=None, description="Optional selected entity candidate if resolving disambiguation."
    )
    user_id: Optional[str] = Field(default=None, description="User identifier owning the session.")


async def resolve_session(session_id: str, user_id: Optional[str] = None):
    """Retrieve session by ID, searching across registered sessions if user_id is omitted."""
    if user_id:
        try:
            sess = await session_service.get_session(
                app_name=adk_app.name,
                user_id=user_id,
                session_id=session_id,
            )
            if sess:
                return sess
        except Exception:
            pass

    # Search in-memory session registry across all users
    if hasattr(session_service, "sessions"):
        app_sessions = session_service.sessions.get(adk_app.name, {})
        for uid, user_sessions in app_sessions.items():
            if session_id in user_sessions:
                return user_sessions[session_id]

    try:
        return await session_service.get_session(
            app_name=adk_app.name,
            user_id="executive_user",
            session_id=session_id,
        )
    except Exception:
        return None



class BriefResponse(BaseModel):
    status: str = Field(description="'paused' (at gate) or 'completed' (published).")
    brief_id: str = Field(description="Unique brief session identifier.")
    session_id: str = Field(description="ADK session identifier.")
    gate: Optional[str] = Field(default=None, description="Name of the pending gate function if paused.")
    payload: Optional[dict[str, Any]] = Field(default=None, description="Arguments or draft content at gate.")
    doc_url: Optional[str] = Field(default=None, description="Google Doc URL if completed.")
    draft: Optional[str] = Field(default=None, description="Generated brief markdown text.")
    version: Optional[int] = Field(default=1, description="Draft version.")


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
    state = updated_session.state

    if pending_gate:
        call_id, func_name, args = pending_gate
        return BriefResponse(
            status="paused",
            brief_id=session.id,
            session_id=session.id,
            gate=func_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status="completed",
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
    session = await resolve_session(session_id, effective_user_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    effective_user_id = session.user_id or effective_user_id or "executive_user"

    pending_call = get_unanswered_pending_gate(session)
    if not pending_call:
        raise HTTPException(
            status_code=400,
            detail=f"No active pending gate found for session '{session_id}'.",
        )

    call_id, func_name, _ = pending_call

    # Build response payload matching the pending tool contract
    if func_name == "request_disambiguation":
        response_payload = {
            "selected_candidate": decision.candidate or {"name": decision.comment or ""},
            "status": "resolved",
        }
    else:
        # approve_brief
        response_payload = {
            "status": decision.status,
            "comment": decision.comment,
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
    state = updated_session.state

    if pending_gate:
        c_id, f_name, args = pending_gate
        return BriefResponse(
            status="paused",
            brief_id=session.id,
            session_id=session.id,
            gate=f_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status="completed",
        brief_id=session.id,
        session_id=session.id,
        doc_url=state.get("published_doc_url"),
        draft=state.get("brief_draft"),
        version=state.get("draft_version", 1),
    )


@server.get("/briefs/{session_id}", response_model=BriefResponse)
async def get_brief(session_id: str, user_id: Optional[str] = None):
    """Retrieve the current status of a brief session."""
    session = await resolve_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    pending_call = get_unanswered_pending_gate(session)
    state = session.state

    if pending_call:
        _, f_name, args = pending_call
        return BriefResponse(
            status="paused",
            brief_id=session.id,
            session_id=session.id,
            gate=f_name,
            payload=args,
            draft=state.get("brief_draft"),
            version=state.get("draft_version", 1),
        )

    return BriefResponse(
        status="completed",
        brief_id=session.id,
        session_id=session.id,
        doc_url=state.get("published_doc_url"),
        draft=state.get("brief_draft"),
        version=state.get("draft_version", 1),
    )
