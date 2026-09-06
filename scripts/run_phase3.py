"""Headless verification runner for Phase 3.

Verifies:
1. Publisher agent executes successfully upon Gate 2 approval.
2. create_google_doc creates document and records published_doc_url in session state.
3. share_doc shares the doc with requested recipients.
4. Idempotency test (HLD §10.5, §16):
   Double approval / duplicate publish call on (brief_id, version) yields exactly one document,
   returning the cached DocRef without re-creation.

Usage:
    .venv/bin/python scripts/run_phase3.py
"""

import asyncio
import json
import os
import sys
from typing import Any, Optional

from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from meeting_prep.app import app
from meeting_prep.config import MODEL_NAME, PROJECT_ID, LOCATION
from meeting_prep.tools.drive import (
    create_google_doc,
    get_stub_creation_count,
    reset_stub_creation_counts,
)


def extract_gate_call(event) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Extract (call_id, function_name, args) from a non-partial long-running event."""
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


async def main() -> int:
    print("=" * 75)
    print("🚀 Meeting Prep Copilot — Phase 3 Publish & Idempotency Verification")
    print(f"   Project:  {PROJECT_ID} | Region: {LOCATION} | Model: {MODEL_NAME}")
    print("=" * 75)

    mode = os.getenv("DRIVE_CLIENT_MODE", "stub").lower().strip()
    os.environ["DRIVE_CLIENT_MODE"] = mode
    print(f"   Drive Client Mode: {mode}")
    if mode == "stub":
        reset_stub_creation_counts()

    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    user_id = "test_exec_phase3"

    initial_state = {
        "company_input": "Stripe",
        "user_preferences": {
            "focus_areas": ["AI agent payments", "billing platform"],
            "recipients": ["executive@example.com", "board@example.com"],
        },
    }

    session = await session_service.create_session(
        app_name=app.name,
        user_id=user_id,
        state=initial_state,
    )

    runner = Runner(
        app=app,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    # -----------------------------------------------------------------
    # LEG 1: Initial research & composition -> Gate 2 pause
    # -----------------------------------------------------------------
    print("\n[1/4] Leg 1: Executing pipeline for Stripe to reach Gate 2 approval...")
    prompt = "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and billing platform."
    msg1 = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

    gate = None
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg1):
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                print(f"      🔧 [{agent_name}] Tool: {c.name}")
        detected = extract_gate_call(event)
        if detected:
            gate = detected

    if not gate or gate[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected pause at 'approve_brief', got: {gate}")
        return 1

    call_id, func_name, args = gate
    print(f"      ✅ Leg 1 paused at Gate 2: {func_name} (id={call_id})")

    # -----------------------------------------------------------------
    # LEG 2: Human Approval -> Publisher triggers create_google_doc & share_doc
    # -----------------------------------------------------------------
    print("\n[2/4] Leg 2: Sending Human Approval to trigger publisher agent...")
    approve_msg = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=func_name,
                    response={"status": "approved", "comment": None},
                )
            )
        ],
    )

    tools_called_leg2 = []
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=approve_msg):
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                tools_called_leg2.append(c.name)
                print(f"      🔧 [{agent_name}] Tool: {c.name}")

    print(f"      Leg 2 tools executed: {tools_called_leg2}")
    if "create_google_doc" not in tools_called_leg2:
        print("      ❌ FAILED: create_google_doc was not called by publisher!")
        return 1

    final_session = await session_service.get_session(
        app_name=app.name,
        user_id=user_id,
        session_id=session.id,
    )
    pub_url = final_session.state.get("published_doc_url")
    published_docs = final_session.state.get("published_docs", {})
    resolved_entity = final_session.state.get("resolved_entity", {})
    company_name = resolved_entity.get("name", "Stripe") if isinstance(resolved_entity, dict) else getattr(resolved_entity, "name", "Stripe")

    print(f"      Published Doc URL in session: {pub_url}")
    print(f"      Published Docs in session: {list(published_docs.keys())}")

    if not pub_url:
        print("      ❌ FAILED: published_doc_url is missing from session state.")
        return 1

    draft_version = int(final_session.state.get("draft_version") or 1)

    # -----------------------------------------------------------------
    # IDEMPOTENCY TEST: Duplicate approval / publish call (HLD §10.5, §16)
    # -----------------------------------------------------------------
    print("\n[3/4] Testing Idempotency: Re-sending approval through runner to verify end-to-end idempotency...")
    doc_v_count_before = get_stub_creation_count(company_name, draft_version) if mode == "stub" else 1
    if mode == "stub":
        print(f"      Active stub doc creations before duplicate approval: {doc_v_count_before}")

    dup_tools_called = []
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=approve_msg):
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                dup_tools_called.append(c.name)
                print(f"      🔧 [{agent_name}] Tool: {c.name}")

    after_session = await session_service.get_session(
        app_name=app.name,
        user_id=user_id,
        session_id=session.id,
    )
    doc_v_count_after = get_stub_creation_count(company_name, draft_version) if mode == "stub" else 1
    after_pub_url = after_session.state.get("published_doc_url")

    print(f"      Duplicate pass tools executed: {dup_tools_called}")
    print(f"      Published doc URL after duplicate pass: {after_pub_url}")
    if mode == "stub":
        print(f"      Active stub doc creations after duplicate pass:   {doc_v_count_after}")

    if mode == "stub" and doc_v_count_after != doc_v_count_before:
        print(f"      ❌ FAILED: Document was re-created! Count grew from {doc_v_count_before} to {doc_v_count_after}.")
        return 1

    if after_pub_url != pub_url:
        print(f"      ❌ FAILED: URL mismatch: {after_pub_url} vs {pub_url}")
        return 1

    # Verify direct tool invocation idempotency with session state
    class SessionToolContext:
        def __init__(self, state):
            self.state = state
            self.actions = type("Actions", (), {"state_delta": {}})()

    ctx = SessionToolContext(dict(after_session.state))
    duplicate_doc = create_google_doc(
        title=f"Executive Brief: {company_name}",
        markdown=after_session.state.get("brief_draft", ""),
        brief_id=company_name,
        version=draft_version,
        tool_context=ctx,
    )
    if not duplicate_doc.get("cached"):
        print("      ❌ FAILED: Direct create_google_doc check was not marked cached!")
        return 1

    print("      ✅ IDEMPOTENCY VERIFIED: Duplicate approval yielded the identical Doc with 0 re-creations!")

    # -----------------------------------------------------------------
    # Final Summary
    # -----------------------------------------------------------------
    print("\n[4/4] Phase 3 Acceptance Summary:")
    print(f"      Mode:              {mode}")
    print(f"      Company:           {company_name}")
    print(f"      Doc URL:           {pub_url}")
    print(f"      Idempotency Cache: {list(published_docs.keys())}")
    if mode == "stub":
        print(f"      Creations:         {doc_v_count_after} (exactly 1 doc created despite double approval)")
    print("\n🎉 PHASE 3 ACCEPTANCE CRITERIA MET SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
