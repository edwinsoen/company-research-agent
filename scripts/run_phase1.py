"""Headless verification runner for Phase 1.

Executes the full 7-agent Meeting Prep Copilot against target company 'Stripe',
asserts that all session state keys are populated according to schema,
and prints the final brief draft.

Usage:
    .venv/bin/python scripts/run_phase1.py
"""

import asyncio
import json
import sys
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from meeting_prep.agents.root import create_root_coordinator
from meeting_prep.config import MODEL_NAME, PROJECT_ID, LOCATION


async def main():
    print("=" * 70)
    print("🚀 Meeting Prep Copilot — Phase 1 Local Graph Verification")
    print(f"   Project:  {PROJECT_ID}")
    print(f"   Location: {LOCATION}")
    print(f"   Model:    {MODEL_NAME}")
    print("=" * 70)

    # 1. Initialize root agent and in-memory session service
    print("\n[1/4] Initializing agent graph and in-memory session service...")
    root_agent = create_root_coordinator()
    session_service = InMemorySessionService()

    app_name = "meeting_prep"
    user_id = "test_executive"

    # Pre-seed initial state for target company 'Stripe'
    initial_state = {
        "company_input": "Stripe",
        "user_preferences": {
            "focus_areas": ["AI agent payments", "billing platform"],
            "recipients": ["executive@example.com"],
        },
    }

    session = await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        state=initial_state,
    )
    print(f"      Session created: {session.id} for user: {user_id}")
    print("      Target Company:  Stripe")
    print("      Focus Areas:     ['AI agent payments', 'billing platform']")

    # 2. Build runner
    runner = Runner(
        app_name=app_name,
        agent=root_agent,
        session_service=session_service,
    )

    # 3. Execute graph
    print("\n[2/4] Executing agent pipeline (root -> disambiguator -> parallel researchers -> delta -> loop -> publisher)...")
    user_prompt = "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and their billing platform."
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_prompt)],
    )

    event_count = 0
    subagents_seen = set()

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=new_message,
    ):
        event_count += 1
        agent_name = getattr(event, "author", None) or getattr(event, "agent_name", None) or "system"
        subagents_seen.add(agent_name)

        # Print tool calls and key events
        content = getattr(event, "content", None)
        if content and hasattr(content, "parts"):
            for part in content.parts:
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    print(f"      🔧 [{agent_name}] Tool Call: {fc.name}({json.dumps(fc.args or {})[:80]}...)")
                elif getattr(part, "function_response", None):
                    fr = part.function_response
                    print(f"      ✅ [{agent_name}] Tool Result: {fr.name}")
                elif getattr(part, "text", None) and agent_name != "root_coordinator":
                    first_line = part.text.strip().split("\n")[0][:70]
                    if first_line:
                        print(f"      💬 [{agent_name}]: {first_line}")

    print(f"\n[3/4] Pipeline completed. Total events: {event_count}")
    print(f"      Agents observed: {', '.join(sorted(subagents_seen))}")

    # 4. Validate session state
    print("\n[4/4] Validating session state contracts...")
    final_session = await session_service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session.id,
    )
    state = final_session.state

    required_keys = [
        "company_input",
        "user_preferences",
        "resolved_entity",
        "research_profile",
        "research_news",
        "research_focus",
        "delta_summary",
        "brief_draft",
        "published_doc_url",
    ]

    all_keys_present = True
    for key in required_keys:
        if key in state and state[key]:
            print(f"      ✅ State key '{key}': present")
        else:
            print(f"      ❌ State key '{key}': missing or empty")
            all_keys_present = False

    print("\n" + "=" * 70)
    print("📄 GENERATED EXECUTIVE BRIEF (brief_draft):")
    print("=" * 70)
    brief_draft = state.get("brief_draft", "NO DRAFT GENERATED")
    if isinstance(brief_draft, dict):
        brief_draft = json.dumps(brief_draft, indent=2)
    print(brief_draft)
    print("=" * 70)

    print(f"\nPublished Doc URL: {state.get('published_doc_url')}")

    if all_keys_present:
        print("\n🎉 PHASE 1 ACCEPTANCE CRITERIA MET SUCCESSFULLY!")
        return 0
    else:
        print("\n⚠️ WARNING: Some required session state keys were missing.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
