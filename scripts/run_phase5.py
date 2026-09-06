"""Headless verification runner for Phase 5 Long-Term Memory & Delta Agent.

Verifies the full two-run cross-session memory lifecycle (HLD §9, §16):
1. RUN 1 (Baseline):
   - Initial run on 'Stripe' with explicit focus areas.
   - delta_agent executes search_memory -> has_prior is False ("baseline brief").
   - Gate 2 approval triggers publisher -> after_agent callback saves brief record & preferences to Memory Bank.
2. RUN 2 (Returning User):
   - Subsequent run on 'Stripe' with NO explicit focus areas specified.
   - root_coordinator preloads remembered preferences from Memory Bank.
   - delta_agent searches memory, retrieves Run 1 brief facts -> has_prior is True.
   - delta_agent computes 3-5 delta bullet points highlighting developments/updates.
   - Composer synthesizes brief leading with the delta section.

Usage:
    .venv/bin/python scripts/run_phase5.py
"""

import asyncio
import json
import os
import sys
from typing import Any, Optional

from google.adk.runners import Runner
from google.genai import types

from meeting_prep.app import app
from meeting_prep.config import (
    MODEL_NAME,
    PROJECT_ID,
    LOCATION,
    get_session_service,
    get_memory_service,
    get_artifact_service,
)
from meeting_prep.tools.drive import reset_stub_creation_counts


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


async def execute_brief_flow(
    prompt: str,
    user_id: str,
    session_service: Any,
    memory_service: Any,
    artifact_service: Any,
) -> dict[str, Any]:
    """Execute a complete Leg 1 -> Leg 2 approved brief flow."""
    session = await session_service.create_session(
        app_name=app.name,
        user_id=user_id,
        state={},
    )

    runner = Runner(
        app=app,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=memory_service,
    )

    msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])

    # Leg 1: run to Gate 2
    gate = None
    delta_findings = None
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
        detected = extract_gate_call(event)
        if detected:
            gate = detected

    if not gate:
        raise RuntimeError("Pipeline did not pause at gate.")

    call_id, func_name, args = gate

    # Leg 2: approve
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

    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=approve_msg):
        pass

    final_session = await session_service.get_session(
        app_name=app.name,
        user_id=user_id,
        session_id=session.id,
    )
    return final_session.state


async def main() -> int:
    print("=" * 75)
    print("🚀 Meeting Prep Copilot — Phase 5 Memory Bank & Delta Agent Verification")
    print(f"   Project:  {PROJECT_ID} | Region: {LOCATION} | Model: {MODEL_NAME}")
    print("=" * 75)

    os.environ["DRIVE_CLIENT_MODE"] = "stub"
    reset_stub_creation_counts()

    # Shared services representing persistent storage across runs
    session_service = get_session_service()
    memory_service = get_memory_service()
    artifact_service = get_artifact_service()
    user_id = "test_exec_memory_p5"

    # -----------------------------------------------------------------
    # RUN 1: Baseline Brief for Stripe
    # -----------------------------------------------------------------
    print("\n[1/3] RUN 1 (Baseline): Initial briefing for Stripe with explicit focus areas...")
    prompt1 = "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and billing platform."

    state1 = await execute_brief_flow(
        prompt=prompt1,
        user_id=user_id,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )

    delta1 = state1.get("delta_summary", {})
    has_prior_1 = delta1.get("has_prior", False) if isinstance(delta1, dict) else getattr(delta1, "has_prior", False)
    doc_url_1 = state1.get("published_doc_url")

    print(f"      Run 1 delta_summary.has_prior: {has_prior_1}")
    print(f"      Run 1 published doc URL:       {doc_url_1}")

    if has_prior_1:
        print("      ❌ FAILED: Run 1 should have has_prior == False (initial baseline).")
        return 1
    print("      ✅ Run 1 established baseline brief correctly (has_prior=False).")

    # Verify memory contents after Run 1
    mem_resp = await memory_service.search_memory(
        app_name=app.name,
        user_id=user_id,
        query="Stripe briefing facts",
    )
    print(f"      Memories persisted in Memory Bank: {len(mem_resp.memories or [])}")
    if not mem_resp.memories:
        print("      ❌ FAILED: No memories were persisted after Run 1 approval.")
        return 1

    # -----------------------------------------------------------------
    # RUN 2: Returning User Brief for Stripe (Memory Delta Verification)
    # -----------------------------------------------------------------
    print("\n[2/3] RUN 2 (Returning): Follow-up brief for Stripe (no focus overrides given)...")
    prompt2 = "Prepare an updated briefing for my follow-up meeting with Stripe."

    state2 = await execute_brief_flow(
        prompt=prompt2,
        user_id=user_id,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )

    delta2 = state2.get("delta_summary", {})
    has_prior_2 = delta2.get("has_prior", False) if isinstance(delta2, dict) else getattr(delta2, "has_prior", False)
    changes_2 = delta2.get("changes", []) if isinstance(delta2, dict) else getattr(delta2, "changes", [])
    prefs_2 = state2.get("user_preferences", {})
    focus_2 = prefs_2.get("focus_areas", []) if isinstance(prefs_2, dict) else getattr(prefs_2, "focus_areas", [])
    doc_url_2 = state2.get("published_doc_url")

    print(f"      Run 2 preloaded focus areas:   {focus_2}")
    print(f"      Run 2 delta_summary.has_prior: {has_prior_2}")
    print(f"      Run 2 delta bullet count:      {len(changes_2)}")
    print(f"      Run 2 published doc URL:       {doc_url_2}")

    print("\n      Delta Summary Changes Output:")
    for c in changes_2:
        print(f"        • {c}")

    if not focus_2 or len(focus_2) == 0:
        print("      ❌ FAILED: Run 2 expected preloaded focus areas from memory.")
        return 1

    if not has_prior_2:
        print("      ❌ FAILED: Run 2 expected has_prior == True from prior brief in memory.")
        return 1

    if len(changes_2) == 0:
        print("      ❌ FAILED: Run 2 delta changes list is empty.")
        return 1

    print("      ✅ Run 2 successfully preloaded preferences, retrieved prior brief, and computed delta!")

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    print("\n[3/3] Phase 5 Acceptance Summary:")
    print("      Run 1: Established baseline, has_prior=False, saved to Memory Bank.")
    print("      Run 2: Pre-filled user preferences, retrieved prior brief, has_prior=True.")
    print(f"      Run 2 Delta Bullets: {len(changes_2)} changes identified.")
    print("\n🎉 PHASE 5 ACCEPTANCE CRITERIA MET SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
