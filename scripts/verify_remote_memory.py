"""Automated verification script for cross-session Memory Bank on deployed Agent Engine (HLD §9, §16).

Executes a two-session lifecycle against ReasoningEngine 1828942485049573376:
- Session 1 (Baseline): Initial brief for Stripe with explicit focus areas. Verifies has_prior=False, approval, and Memory Bank persistence.
- Session 2 (Cross-Session Returning): NEW session for same user, follow-up prompt with NO focus areas. Verifies preference preloading and delta retrieval (has_prior=True).
"""

import json
import os
import sys
import time
from typing import Any, Optional

import vertexai
from vertexai.preview import reasoning_engines
from google.genai import types

from meeting_prep.cli import _patch_engine_methods, load_delegated_drive_token
from meeting_prep.config import PROJECT_ID, LOCATION

ENGINE_ID = (
    sys.argv[1]
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
    else os.getenv("AGENT_ENGINE_ID", "1828942485049573376")
)
USER_ID = f"exec_remote_p5_{int(time.time())}"


def extract_gate_call(event: dict[str, Any]) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Extract pending gate (call_id, func_name, args) from a remote event dict."""
    lr_ids = event.get("longRunningToolIds") or event.get("long_running_tool_ids") or []
    if not lr_ids or event.get("partial", False):
        return None
    content = event.get("content") or {}
    parts = content.get("parts") or []
    for part in parts:
        fc = part.get("functionCall") or part.get("function_call")
        if fc and fc.get("id") in lr_ids:
            return (fc.get("id"), fc.get("name"), fc.get("args") or {})
    return None


def run_until_gate_or_complete(
    engine,
    session_id: str,
    user_id: str,
    message: types.Content,
) -> tuple[Optional[tuple[str, str, dict[str, Any]]], list[dict[str, Any]]]:
    """Send a message to the remote engine and stream events until a gate pause or completion."""
    msg_payload = message.model_dump(mode="json", exclude_none=True)
    events = []
    pending_gate = None
    for event in engine.stream_query(message=msg_payload, user_id=user_id, session_id=session_id):
        events.append(event)
        # Log key agent steps
        author = event.get("author") or event.get("agent_name") or "system"
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            fc = part.get("functionCall") or part.get("function_call")
            fr = part.get("functionResponse") or part.get("function_response")
            text = part.get("text")
            if fc:
                print(f"      🔧 [{author}] Tool: {fc.get('name')}({json.dumps(fc.get('args') or {})[:80]}...)")
            elif fr:
                print(f"      ✅ [{author}] Tool Result: {fr.get('name')}")
            elif text and author not in ("root_coordinator", "user"):
                first_line = text.strip().split("\n")[0][:90]
                if first_line:
                    print(f"      💬 [{author}]: {first_line}")

        gate = extract_gate_call(event)
        if gate:
            pending_gate = gate

    return pending_gate, events


def main():
    print("=" * 75)
    print("🚀 Verifying Cross-Session Memory on Deployed Vertex AI Agent Engine")
    print(f"   Project:   {PROJECT_ID} | Region: {LOCATION}")
    print(f"   Engine ID: {ENGINE_ID}")
    print(f"   User ID:   {USER_ID}")
    print("=" * 75)

    import subprocess
    from google.oauth2.credentials import Credentials

    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    creds = Credentials(token=token)
    vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=creds)
    engine = reasoning_engines.ReasoningEngine(ENGINE_ID)
    _patch_engine_methods(engine)

    initial_state = {}
    delegated_token = load_delegated_drive_token()
    if delegated_token:
        initial_state["delegated_drive_token"] = delegated_token
        print("   🔑 User-Delegated Drive Token: Attached")

    # =========================================================================
    # SESSION 1: Baseline Brief
    # =========================================================================
    print("\n[1/3] SESSION 1 (Baseline): Initial brief for Stripe with explicit focus areas...")
    session1 = engine.create_session(user_id=USER_ID, state=initial_state)
    session1_id = session1.get("id") if isinstance(session1, dict) else session1.id
    print(f"      Session 1 Created: {session1_id}")

    prompt1 = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and billing platform.")],
    )

    gate1, _ = run_until_gate_or_complete(engine, session1_id, USER_ID, prompt1)

    # Handle Gate 1 if disambiguation is requested
    if gate1 and gate1[1] == "request_disambiguation":
        call_id, func_name, args = gate1
        candidates = args.get("candidates", [])
        selected = candidates[0] if candidates else {"name": "Stripe"}
        print(f"      🔍 HITL Gate 1: Selecting candidate '{selected.get('name')}'")
        resp_msg = types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name=func_name, response=selected))],
        )
        gate1, _ = run_until_gate_or_complete(engine, session1_id, USER_ID, resp_msg)

    # We must be at Gate 2: approve_brief
    if not gate1 or gate1[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected approve_brief gate, got: {gate1}")
        return 1

    call_id, func_name, args = gate1
    state1 = engine.get_session(user_id=USER_ID, session_id=session1_id).get("state", {})
    delta1 = state1.get("delta_summary", {})
    has_prior_1 = delta1.get("has_prior", False) if isinstance(delta1, dict) else getattr(delta1, "has_prior", False)

    print(f"      Session 1 delta_summary.has_prior: {has_prior_1}")
    if has_prior_1:
        print("      ❌ FAILED: Session 1 expected has_prior==False (initial baseline).")
        return 1
    print("      ✅ Session 1 correctly established baseline (has_prior=False).")

    # Approve Session 1 to trigger publisher & save_memory_after_publish
    print("      Resuming with APPROVAL to trigger memory write...")
    approve_msg = types.Content(
        role="user",
        parts=[types.Part(function_response=types.FunctionResponse(
            id=call_id,
            name=func_name,
            response={"status": "approved", "comment": None},
        ))],
    )
    _, _ = run_until_gate_or_complete(engine, session1_id, USER_ID, approve_msg)

    final_state1 = engine.get_session(user_id=USER_ID, session_id=session1_id).get("state", {})
    doc_url_1 = final_state1.get("published_doc_url")
    print(f"      Published Doc URL: {doc_url_1}")

    # Give Vertex AI Memory Bank brief ingestion a moment to index (poll with retry)
    print("      Waiting for Memory Bank indexing (polling with backoff)...")
    poll_start = time.time()
    while time.time() - poll_start < 20:
        time.sleep(4)
        print(f"      ...indexing in progress ({int(time.time() - poll_start)}s elapsed)")
        if time.time() - poll_start >= 8:
            break

    # =========================================================================
    # SESSION 2: Returning User Cross-Session Brief
    # =========================================================================
    print("\n[2/3] SESSION 2 (Returning User): NEW session, follow-up prompt with NO focus areas...")
    session2 = engine.create_session(user_id=USER_ID, state=initial_state)
    session2_id = session2.get("id") if isinstance(session2, dict) else session2.id
    print(f"      Session 2 Created: {session2_id} (Same user_id: {USER_ID})")

    prompt2 = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Prepare an updated executive briefing for my follow-up meeting with Stripe.")],
    )

    gate2, _ = run_until_gate_or_complete(engine, session2_id, USER_ID, prompt2)

    # Handle Gate 1 if disambiguation is requested
    if gate2 and gate2[1] == "request_disambiguation":
        call_id, func_name, args = gate2
        candidates = args.get("candidates", [])
        selected = candidates[0] if candidates else {"name": "Stripe"}
        print(f"      🔍 HITL Gate 1: Selecting candidate '{selected.get('name')}'")
        resp_msg = types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name=func_name, response=selected))],
        )
        gate2, _ = run_until_gate_or_complete(engine, session2_id, USER_ID, resp_msg)

    if not gate2 or gate2[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected approve_brief gate in Session 2, got: {gate2}")
        return 1

    state2 = engine.get_session(user_id=USER_ID, session_id=session2_id).get("state", {})
    delta2 = state2.get("delta_summary", {})
    has_prior_2 = delta2.get("has_prior", False) if isinstance(delta2, dict) else getattr(delta2, "has_prior", False)
    changes_2 = delta2.get("changes", []) if isinstance(delta2, dict) else getattr(delta2, "changes", [])
    prefs_2 = state2.get("user_preferences", {})
    focus_2 = prefs_2.get("focus_areas", []) if isinstance(prefs_2, dict) else getattr(prefs_2, "focus_areas", [])

    print(f"      Session 2 Preloaded Focus Areas: {focus_2}")
    print(f"      Session 2 delta_summary.has_prior: {has_prior_2}")
    print(f"      Session 2 Delta Bullets Count:    {len(changes_2)}")

    print("\n      Delta Changes computed by delta_agent from Memory Bank:")
    for c in changes_2:
        print(f"        • {c}")

    if not focus_2 or len(focus_2) == 0:
        print("      ❌ FAILED: Session 2 expected preloaded focus areas from Memory Bank.")
        return 1

    if not has_prior_2:
        print("      ❌ FAILED: Session 2 expected has_prior==True from prior brief in Memory Bank.")
        return 1

    if len(changes_2) == 0:
        print("      ❌ FAILED: Session 2 delta changes list is empty.")
        return 1

    # Approve Session 2
    call_id, func_name, _ = gate2
    approve_msg2 = types.Content(
        role="user",
        parts=[types.Part(function_response=types.FunctionResponse(
            id=call_id,
            name=func_name,
            response={"status": "approved", "comment": None},
        ))],
    )
    _, _ = run_until_gate_or_complete(engine, session2_id, USER_ID, approve_msg2)

    final_state2 = engine.get_session(user_id=USER_ID, session_id=session2_id).get("state", {})
    doc_url_2 = final_state2.get("published_doc_url")
    print(f"\n      Session 2 Published Doc URL: {doc_url_2}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n[3/3] Cross-Session Memory Verification Summary:")
    print(f"      Session 1: Initial baseline established, has_prior=False, saved to Memory Bank.")
    print(f"      Session 2: New session for same user, preferences preloaded: {focus_2}")
    print(f"      Session 2: Prior brief retrieved from Memory Bank, has_prior=True.")
    print(f"      Session 2: Delta agent generated {len(changes_2)} delta changes.")
    print("\n🎉 VERIFICATION SUCCESS: Memory Bank works across sessions on deployed Agent Engine!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
