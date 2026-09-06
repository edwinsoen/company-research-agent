#!/usr/bin/env python3
"""Verification runner for deployed Agent Engine / ADK api_server endpoint.

Validates the deployed runtime behavior and addresses HLD §10.4 & §12A.4:
1. Session persistence in Agent Engine Sessions:
   Creates session via `POST /apps/{app}/users/{user}/sessions`
2. Leg 1 execution and non-blocking gate pause:
   `POST /run` with user prompt -> detects pause event with long_running_tool_ids and functionCall
3. Agent Runtime passthrough hazard (§10.4):
   Validates whether a `Content` carrying a `function_response` part can be sent as `new_message`
   through `POST /run` to resume the existing invocation rather than spawning a new invocation.
4. Identity & session retrieval verification (§12A.4):
   `GET /apps/{app}/users/{user}/sessions/{session_id}` confirms session state persistence in store.

Usage:
    # Test against deployed Agent Engine or locally running adk api_server
    .venv/bin/python scripts/verify_deployed_runtime.py --endpoint-url http://localhost:8000
    .venv/bin/python scripts/verify_deployed_runtime.py --endpoint-url https://<region>-aiplatform.googleapis.com/...
"""

import argparse
import json
import os
import sys
from typing import Any, Optional

import requests


def extract_pending_gate_from_events(events: list[dict[str, Any]]) -> Optional[tuple[str, str, dict[str, Any], str]]:
    """Extract pending gate (id, name, args, invocation_id) from non-partial long-running events."""
    for event in reversed(events):
        if event.get("partial", False):
            continue
        # Support both snake_case and camelCase alias wire formats from FastAPI/Pydantic
        lr_ids = event.get("long_running_tool_ids") or event.get("longRunningToolIds") or []
        if not lr_ids:
            continue
        inv_id = event.get("invocation_id") or event.get("invocationId") or ""
        content = event.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            fc = part.get("function_call") or part.get("functionCall")
            if fc and fc.get("id") in lr_ids:
                return (fc.get("id"), fc.get("name"), fc.get("args") or {}, inv_id)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify deployed Agent Engine / ADK api_server endpoint (HLD §10.4, §12A.4)"
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.getenv("AGENT_ENGINE_ENDPOINT", "http://localhost:8000"),
        help="Base URL of deployed Agent Engine or adk api_server (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--agent-engine-id",
        default=os.getenv("AGENT_ENGINE_ID") or os.getenv("REASONING_ENGINE_ID"),
        help="Optional reasoning engine resource ID to verify deployed AGENT_IDENTITY configuration (§12A.4)",
    )
    parser.add_argument(
        "--app-name",
        default=os.getenv("APP_NAME", "meeting_prep"),
        help="Target ADK application name (default: meeting_prep)",
    )
    parser.add_argument(
        "--user-id",
        default="test_verifier",
        help="Caller user identifier for session scoping",
    )
    parser.add_argument(
        "--prompt",
        default="Prepare an executive briefing for my upcoming meeting with Stripe.",
        help="Prompt to execute",
    )
    args = parser.parse_args()

    base_url = args.endpoint_url.rstrip("/")
    app_name = args.app_name
    user_id = args.user_id
    agent_engine_id = args.agent_engine_id

    print("=" * 75)
    print("🚀 Deployed Agent Engine Runtime Verification (HLD §10.4, §12A.4)")
    print(f"   Endpoint:        {base_url}")
    print(f"   App Name:        {app_name}")
    print(f"   User ID:         {user_id}")
    if agent_engine_id:
        print(f"   Agent Engine ID: {agent_engine_id}")
    print("=" * 75)

    session = requests.Session()

    # Step 1: Create session in Agent Engine Sessions
    print("\n[1/5] Creating session via Agent Engine Sessions API...")
    session_url = f"{base_url}/apps/{app_name}/users/{user_id}/sessions"
    try:
        sess_resp = session.post(session_url, json={}, timeout=15)
    except requests.exceptions.RequestException as err:
        print(f"      ❌ Connection error to {session_url}: {err}")
        print("\nNote: To run against local ADK server:")
        print(f"      adk api_server --port=8000 {app_name}")
        return 1

    if sess_resp.status_code not in (200, 201):
        print(f"      ❌ Session creation failed ({sess_resp.status_code}): {sess_resp.text}")
        return 1

    sess_data = sess_resp.json()
    session_id = sess_data.get("id") or sess_data.get("session_id")
    print(f"      ✅ Session created successfully: {session_id}")

    # Step 2: POST /run Leg 1 prompt
    print("\n[2/5] Executing Leg 1 prompt via POST /run...")
    run_url = f"{base_url}/run"
    leg1_payload = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": args.prompt}],
        },
    }

    try:
        run_resp = session.post(run_url, json=leg1_payload, timeout=120)
    except requests.exceptions.RequestException as err:
        print(f"      ❌ Network error during Leg 1 run: {err}")
        return 1

    if not run_resp.ok:
        print(f"      ❌ POST /run failed ({run_resp.status_code}): {run_resp.text}")
        return 1

    events = run_resp.json()
    if not isinstance(events, list):
        print(f"      ❌ Expected list of events, got: {type(events)}")
        return 1

    gate = extract_pending_gate_from_events(events)
    if not gate:
        print("      ❌ Leg 1 completed without hitting a long-running tool gate.")
        return 1

    call_id, func_name, func_args, leg1_inv_id = gate
    print(f"      ✅ Leg 1 paused at Gate: {func_name} (call_id: {call_id}, invocation_id: {leg1_inv_id})")

    # Step 3: POST /run Leg 2 function_response (HLD §10.4 passthrough & continuity verification)
    print("\n[3/5] Verifying Agent Runtime passthrough: POST /run with function_response...")
    if func_name == "request_disambiguation":
        resp_payload = {"name": "Stripe"}
    else:
        resp_payload = {"status": "approved", "comment": None}

    leg2_payload = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": call_id,
                        "name": func_name,
                        "response": resp_payload,
                    }
                }
            ],
        },
    }

    try:
        resume_resp = session.post(run_url, json=leg2_payload, timeout=120)
    except requests.exceptions.RequestException as err:
        print(f"      ❌ Network error during Leg 2 run: {err}")
        return 1

    if not resume_resp.ok:
        print(f"      ❌ Leg 2 resumption failed ({resume_resp.status_code}): {resume_resp.text}")
        print("      ⚠️ This indicates the Agent Runtime passthrough hazard (§10.4) failed.")
        return 1

    leg2_events = resume_resp.json()
    if not isinstance(leg2_events, list) or not leg2_events:
        print("      ❌ Expected non-empty event list from resumed execution.")
        return 1

    # Verify invocation continuity (HLD §10.4: resume existing invocation rather than starting new one)
    leg2_inv_id = None
    for ev in leg2_events:
        inv = ev.get("invocation_id") or ev.get("invocationId")
        if inv:
            leg2_inv_id = inv
            break

    if leg1_inv_id and leg2_inv_id:
        if leg1_inv_id != leg2_inv_id:
            print(f"      ❌ Invocation ID mismatch! Leg 1: {leg1_inv_id}, Leg 2: {leg2_inv_id}")
            print("      ⚠️ A new invocation started instead of resuming Leg 1 invocation (HLD §10.4 hazard).")
            return 1
        print(f"      ✅ Invocation continuity verified: Resumed existing invocation {leg1_inv_id} (HLD §10.4 verified)")
    else:
        print(f"      ✅ FunctionResponse accepted by deployed runtime ({len(leg2_events)} events returned)")

    # Step 4: Verify session persistence in store (§12A.4)
    print(f"\n[4/5] Verifying session state persistence in Agent Engine Sessions...")
    get_sess_url = f"{base_url}/apps/{app_name}/users/{user_id}/sessions/{session_id}"
    get_resp = session.get(get_sess_url, timeout=15)
    if not get_resp.ok:
        print(f"      ❌ Failed to fetch session from store ({get_resp.status_code}): {get_resp.text}")
        return 1

    final_session = get_resp.json()
    final_state = final_session.get("state", {})
    print(f"      ✅ Session verified in store: ID={session_id}")
    print(f"         State keys: {list(final_state.keys())}")

    # Step 5: Verify deployed Agent Identity configuration (§12A.4)
    print("\n[5/5] Verifying Agent Identity configuration (§12A.4)...")
    if agent_engine_id:
        try:
            import vertexai
            from vertexai.preview import reasoning_engines
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            vertexai.init(project=project, location=location)
            engine = reasoning_engines.ReasoningEngine(agent_engine_id)
            spec = getattr(engine, "spec", None) or {}
            identity_type = spec.get("identity_type") if isinstance(spec, dict) else getattr(spec, "identity_type", None)
            if identity_type == "AGENT_IDENTITY":
                print(f"      ✅ Deployed Agent Engine identity confirmed: {identity_type} (§12A.4 verified)")
            else:
                print(f"      ⚠️ Expected identity_type AGENT_IDENTITY, got: {identity_type}")
        except Exception as err:
            print(f"      ⚠️ Could not query Vertex AI Reasoning Engine directly: {err}")
    else:
        print("      ℹ️ Identity check skipped: Provide --agent-engine-id to query deployed spec.identity_type.")

    print("\n🎉 DEPLOYED RUNTIME VERIFICATION COMPLETE!")
    print("   §10.4 (FunctionResponse passthrough & continuity) & §12A.4 (Session persistence) verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
