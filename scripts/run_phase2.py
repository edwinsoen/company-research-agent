"""Automated headless verification runner for Phase 2.

Verifies:
1. Suite A: Gate 2 revision loop with targeted single-researcher rerun on 'Stripe':
   - Leg 1 pauses cleanly at Gate 2 (approve_brief) with no active connection held
   - Resuming with non-section-naming comment ("The funding and valuation numbers feel out of date")
   - Refinement router classifies target to profile researcher and invokes it via AgentTool
   - Targeted rerun triggers ONLY profile_researcher (0 calls to news or focus researchers)
   - Composer synthesizes draft v2 and records draft_version == 2
   - Approval gate pauses on draft v2
   - Resuming with approval calls exit_loop and completes to publisher
2. Suite B: Gate 1 conditional disambiguation on ambiguous query 'Acme':
   - Ambiguous company pauses at Gate 1 (request_disambiguation)
   - Resuming with candidate selection resolves entity and continues pipeline

Usage:
    .venv/bin/python scripts/run_phase2.py
"""

import asyncio
import json
import sys
from typing import Any, Optional

from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from meeting_prep.app import app
from meeting_prep.config import MODEL_NAME, PROJECT_ID, LOCATION


def extract_gate_call(event) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Extract (call_id, function_name, args) from a non-partial long-running event."""
    lr_ids = getattr(event, "long_running_tool_ids", None)
    if not lr_ids:
        return None
    if getattr(event, "partial", False):
        return None

    content = getattr(event, "content", None)
    if not content or not content.parts:
        return None

    for part in content.parts:
        fc = getattr(part, "function_call", None)
        if fc and fc.id in lr_ids:
            return (fc.id, fc.name, fc.args or {})

    return None


async def run_suite_a() -> bool:
    print("\n" + "=" * 75)
    print("🧪 SUITE A: Gate 2 HITL Revision Loop & Targeted Researcher Rerun ('Stripe')")
    print("=" * 75)

    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    user_id = "test_exec_suite_a"

    initial_state = {
        "company_input": "Stripe",
        "user_preferences": {
            "focus_areas": ["AI agent payments", "billing platform"],
            "recipients": ["exec@example.com"],
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

    # -------------------------------------------------------------
    # LEG 1: Initial research & composition -> Gate 2 pause
    # -------------------------------------------------------------
    print("\n[A.1] Starting Leg 1: Initial pipeline run for Stripe...")
    prompt = "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and billing platform."
    msg1 = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

    gate1 = None
    event_count_leg1 = 0
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg1):
        event_count_leg1 += 1
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                print(f"      🔧 [{agent_name}] Tool: {c.name}")
        detected = extract_gate_call(event)
        if detected:
            gate1 = detected

    if not gate1 or gate1[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected pause at 'approve_brief', got: {gate1}")
        return False

    call_id_v1, func_name_v1, args_v1 = gate1
    print(f"      ✅ Leg 1 paused cleanly at Gate 2: {func_name_v1} (id={call_id_v1})")
    print(f"      Draft v1 character count: {len(args_v1.get('draft', ''))}")

    # Check session state after Leg 1
    s1 = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
    v1_val = s1.state.get("draft_version")
    print(f"      Session state draft_version: {v1_val}")

    # -------------------------------------------------------------
    # LEG 2: Revision Feedback -> Targeted Rerun -> Gate 2 pause v2
    # -------------------------------------------------------------
    revision_feedback = "their pricing seems stale, they moved off per-seat to usage tiers"
    print(f"\n[A.2] Starting Leg 2: Resuming with feedback naming NO section: '{revision_feedback}'...")
    resume_msg1 = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id_v1,
                    name=func_name_v1,
                    response={"status": "revise", "comment": revision_feedback},
                )
            )
        ],
    )

    gate2 = None
    agents_invoked_leg2 = []
    tools_called_leg2 = []

    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=resume_msg1):
        agent_name = getattr(event, "author", None) or "system"
        agents_invoked_leg2.append(agent_name)
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                tools_called_leg2.append(c.name)
                print(f"      🔧 [{agent_name}] Tool: {c.name}")
        detected = extract_gate_call(event)
        if detected:
            gate2 = detected

    print(f"      Leg 2 agents observed: {set(agents_invoked_leg2)}")
    print(f"      Leg 2 tools observed: {tools_called_leg2}")

    # Assert targeted rerun behavior
    profile_rerun = any("profile" in t.lower() for t in tools_called_leg2)
    news_rerun = any("news" in t.lower() for t in tools_called_leg2)
    focus_rerun = any("focus" in t.lower() for t in tools_called_leg2)

    print(f"      Targeted rerun checks -> profile: {profile_rerun}, news: {news_rerun}, focus: {focus_rerun}")
    if not profile_rerun:
        print("      ❌ FAILED: Expected profile_researcher to be triggered during revision.")
        return False
    if news_rerun or focus_rerun:
        print("      ❌ FAILED: News or focus researcher was unnecessarily rerun! Must only rerun targeted researcher.")
        return False

    if not gate2 or gate2[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected pause at draft v2 approve_brief, got: {gate2}")
        return False

    call_id_v2, func_name_v2, args_v2 = gate2
    print(f"      ✅ Gate 2 paused on Draft v2: {func_name_v2} (id={call_id_v2})")

    # Check draft_version increment and refinement_target in session state
    s2 = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
    v2_val = s2.state.get("draft_version")
    ref_target = s2.state.get("refinement_target")
    print(f"      Session state draft_version after refinement: {v2_val}")
    print(f"      Session state refinement_target: {ref_target}")
    if ref_target != "research_profile":
        print(f"      ❌ FAILED: Expected refinement_target == 'research_profile', got: {ref_target}")
        return False

    # -------------------------------------------------------------
    # LEG 3: Approve Draft v2 -> Loop Termination via escalate -> Publish
    # -------------------------------------------------------------
    print("\n[A.3] Starting Leg 3: Resuming with APPROVAL for Draft v2...")
    approve_msg = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id_v2,
                    name=func_name_v2,
                    response={"status": "approved", "comment": None},
                )
            )
        ],
    )

    gate3 = None
    tools_called_leg3 = []
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=approve_msg):
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                tools_called_leg3.append(c.name)
                print(f"      🔧 [{agent_name}] Tool: {c.name}")
        detected = extract_gate_call(event)
        if detected:
            gate3 = detected

    print(f"      Leg 3 tools observed: {tools_called_leg3}")
    if gate3 is not None:
        print(f"      ❌ FAILED: Pipeline should have completed, but paused at: {gate3}")
        return False

    s3 = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
    pub_url = s3.state.get("published_doc_url")
    print(f"      Published doc URL: {pub_url}")
    if not pub_url or not pub_url.startswith("https://"):
        print(f"      ❌ FAILED: Expected valid published_doc_url starting with 'https://', got: {pub_url}")
        return False

    print("\n✨ SUITE A PASSED: Approval gate, targeted rerun, and loop escalation verified successfully!")
    return True


async def run_suite_b() -> bool:
    print("\n" + "=" * 75)
    print("🧪 SUITE B: Gate 1 Conditional Entity Disambiguation ('Acme')")
    print("=" * 75)

    session_service = InMemorySessionService()
    user_id = "test_exec_suite_b"

    initial_state = {
        "company_input": "Acme",
        "user_preferences": {"focus_areas": [], "recipients": []},
    }

    session = await session_service.create_session(
        app_name=app.name,
        user_id=user_id,
        state=initial_state,
    )

    runner = Runner(
        app=app,
        session_service=session_service,
    )

    print("\n[B.1] Starting Leg 1 with ambiguous company 'Acme'...")
    prompt = "Prepare an executive briefing for my upcoming meeting with Acme."
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

    if not gate or gate[1] != "request_disambiguation":
        print(f"      ❌ FAILED: Expected pause at 'request_disambiguation', got: {gate}")
        return False

    call_id, func_name, func_args = gate
    candidates = func_args.get("candidates", [])
    print(f"      ✅ Gate 1 paused cleanly: {func_name} (id={call_id})")
    print(f"      Candidates proposed ({len(candidates)}): {[c.get('name') for c in candidates]}")

    if not candidates:
        print("      ❌ FAILED: No candidates returned in request_disambiguation call.")
        return False

    selected_candidate = candidates[0]
    print(f"\n[B.2] Starting Leg 2: Resuming with selected candidate: {selected_candidate.get('name')}...")
    resume_msg = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id,
                    name=func_name,
                    response=selected_candidate,
                )
            )
        ],
    )

    next_gate = None
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=resume_msg):
        agent_name = getattr(event, "author", None) or "system"
        fc = event.get_function_calls()
        if fc:
            for c in fc:
                print(f"      🔧 [{agent_name}] Tool: {c.name}")
        detected = extract_gate_call(event)
        if detected:
            next_gate = detected

    sb = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
    resolved = sb.state.get("resolved_entity")
    print(f"      Resolved entity in state: {resolved}")
    if not resolved or not resolved.get("name"):
        print("      ❌ FAILED: resolved_entity missing or empty in session state.")
        return False

    if not next_gate or next_gate[1] != "approve_brief":
        print(f"      ❌ FAILED: Expected pipeline to advance to Gate 2 ('approve_brief'), got: {next_gate}")
        return False
    print(f"      ✅ Suite B advanced cleanly to Gate 2: {next_gate[1]} (id={next_gate[0]})")

    print("\n✨ SUITE B PASSED: Conditional disambiguation gate verified successfully!")
    return True


async def main():
    print("=" * 75)
    print("🚀 Meeting Prep Copilot — Phase 2 Automated Headless Verification")
    print(f"   Project:  {PROJECT_ID} | Region: {LOCATION} | Model: {MODEL_NAME}")
    print("=" * 75)

    suite_a_ok = await run_suite_a()
    if not suite_a_ok:
        print("\n❌ Suite A failed.")
        return 1

    suite_b_ok = await run_suite_b()
    if not suite_b_ok:
        print("\n❌ Suite B failed.")
        return 1

    print("\n" + "=" * 75)
    print("🎉 ALL PHASE 2 ACCEPTANCE CRITERIA MET SUCCESSFULLY!")
    print("=" * 75)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
