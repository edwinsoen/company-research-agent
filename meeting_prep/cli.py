"""Interactive CLI client for Meeting Prep Copilot.

Drives the Human-In-The-Loop (HITL) workflow in-process using ADK Runner.
Handles non-blocking pauses for entity disambiguation and draft review,
captures decisions from stdin, and resumes execution via FunctionResponse.

Source: docs/hld.md §10.2, §10.3, §12.2
"""

import argparse
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


def print_banner():
    print("=" * 72)
    print("🏢 Meeting Prep Copilot — Executive Briefing Assistant")
    print(f"   Model: {MODEL_NAME} | Project: {PROJECT_ID} | Region: {LOCATION}")
    print("=" * 72)


def extract_pending_gate(event) -> Optional[tuple[str, str, dict[str, Any]]]:
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


async def run_pipeline(
    user_prompt: str,
    user_id: str = "executive_user",
):
    print_banner()
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()

    # Initial state begins empty; root_coordinator_step extracts
    # company_input, focus_areas, and recipients from the free text prompt.
    session = await session_service.create_session(
        app_name=app.name,
        user_id=user_id,
        state={},
    )

    runner = Runner(
        app=app,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    next_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_prompt)],
    )

    iteration = 1
    while True:
        pending_gate = None
        print(f"\n[Leg {iteration}] Executing agent pipeline...")

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=next_message,
        ):
            agent_name = getattr(event, "author", None) or getattr(event, "agent_name", None) or "system"
            content = getattr(event, "content", None)

            if content and hasattr(content, "parts"):
                for part in content.parts:
                    fc = getattr(part, "function_call", None)
                    fr = getattr(part, "function_response", None)
                    text = getattr(part, "text", None)

                    if fc:
                        print(f"   🔧 [{agent_name}] Tool: {fc.name}({json.dumps(fc.args or {})[:70]}...)")
                    elif fr:
                        print(f"   ✅ [{agent_name}] Tool Result: {fr.name}")
                    elif text and agent_name not in ("root_coordinator", "user"):
                        first_line = text.strip().split("\n")[0][:80]
                        if first_line:
                            print(f"   💬 [{agent_name}]: {first_line}")

            gate = extract_pending_gate(event)
            if gate:
                pending_gate = gate

        if not pending_gate:
            print("\n🎉 Pipeline execution completed!")
            break

        call_id, func_name, func_args = pending_gate
        iteration += 1

        # Handle Gate 1: Entity Disambiguation
        if func_name == "request_disambiguation":
            candidates = func_args.get("candidates", [])
            curr_sess = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
            comp_name = curr_sess.state.get("company_input") or "the specified company"
            print("\n" + "-" * 72)
            print("🔍 [HITL Gate 1: Entity Disambiguation Required]")
            print(f"   The company '{comp_name}' matched multiple candidates:")
            for idx, cand in enumerate(candidates, 1):
                name = cand.get("name", "Unknown")
                domain = cand.get("domain", "")
                desc = cand.get("description", "")
                print(f"   [{idx}] {name} ({domain}) — {desc}")

            choice = 1
            try:
                raw_input = input(f"\nSelect candidate [1-{len(candidates)}] (default 1): ").strip()
                if raw_input.isdigit() and 1 <= int(raw_input) <= len(candidates):
                    choice = int(raw_input)
            except (EOFError, KeyboardInterrupt):
                print("\n\n⚠️ Input cancelled. Leaving session paused at disambiguation gate.")
                curr = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
                return curr.state

            selected = candidates[choice - 1] if candidates else {}
            print(f"   Selected: {selected.get('name')}")
            print("-" * 72)

            next_message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call_id,
                            name=func_name,
                            response=selected,
                        )
                    )
                ],
            )

        # Handle Gate 2: Approve / Revise Draft
        elif func_name == "approve_brief":
            draft = func_args.get("draft", "")
            print("\n" + "=" * 72)
            print("📄 [HITL Gate 2: Executive Brief Draft Review]")
            print("=" * 72)
            print(draft)
            print("=" * 72)

            action = ""
            try:
                action = input("\nDecision: [A]pprove & Publish, or [R]evise with feedback? [a/r]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n\n⚠️ Input cancelled. Leaving session paused at draft review gate.")
                curr = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
                return curr.state

            if action.startswith("r"):
                comment = ""
                while not comment:
                    try:
                        comment = input("Enter revision feedback: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\n\n⚠️ Input cancelled. Leaving session paused at draft review gate.")
                        curr = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
                        return curr.state
                    if not comment:
                        print("Revision feedback cannot be empty. Please specify what needs updating (or press Ctrl-C to abort).")

                decision = {"status": "revise", "comment": comment}
                print(f"\n🔄 Resuming with revision feedback: \"{comment}\"")
            elif action.startswith("a"):
                decision = {"status": "approved", "comment": None}
                print("\n✅ Resuming with APPROVAL. Proceeding to publish...")
            else:
                print(f"\n⚠️ Input '{action}' cancelled or invalid. Leaving session paused at draft review gate.")
                curr = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
                return curr.state

            next_message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call_id,
                            name=func_name,
                            response=decision,
                        )
                    )
                ],
            )
        else:
            print(f"Unknown gate: {func_name}")
            break

    final_session = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session.id)
    doc_url = final_session.state.get("published_doc_url")
    if doc_url:
        print(f"\n📎 Published Google Doc: {doc_url}")
    return final_session.state


def main():
    parser = argparse.ArgumentParser(
        description="Meeting Prep Copilot Interactive CLI — accepts free text meeting briefing requests."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Free-text briefing request (e.g. 'Prepare a briefing for my meeting with Figma on their design AI suite')",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default="executive_user",
        help="User ID for session tracking (default: executive_user)",
    )

    args = parser.parse_args()
    user_prompt = " ".join(args.prompt).strip()

    if not user_prompt:
        print_banner()
        try:
            user_prompt = input("\nEnter your briefing request (e.g. 'Brief me for my meeting with Datadog'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return

    if not user_prompt:
        print("Error: No briefing request provided.")
        return

    asyncio.run(run_pipeline(
        user_prompt=user_prompt,
        user_id=args.user_id,
    ))


if __name__ == "__main__":
    main()
