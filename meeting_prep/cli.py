"""Interactive CLI client for Meeting Prep Copilot.

Drives the Human-In-The-Loop (HITL) workflow in-process using ADK Runner.
Handles non-blocking pauses for entity disambiguation and draft review,
captures decisions from stdin, and resumes execution via FunctionResponse.

Source: docs/hld.md §10.2, §10.3, §12.2
"""

import argparse
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


def print_banner():
    print("=" * 72)
    print("🏢 Meeting Prep Copilot — Executive Briefing Assistant")
    print(f"   Model: {MODEL_NAME} | Project: {PROJECT_ID} | Region: {LOCATION}")
    print("=" * 72)


def print_event_log(event):
    """Print readable agent and tool activity from an event (supports both dict and Event object)."""
    if isinstance(event, dict):
        agent_name = event.get("author") or event.get("agent_name") or "system"
        content = event.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            fc = part.get("function_call") or part.get("functionCall")
            fr = part.get("function_response") or part.get("functionResponse")
            text = part.get("text")
            if fc:
                print(f"   🔧 [{agent_name}] Tool: {fc.get('name')}({json.dumps(fc.get('args') or {})[:70]}...)")
            elif fr:
                print(f"   ✅ [{agent_name}] Tool Result: {fr.get('name')}")
            elif text and agent_name not in ("root_coordinator", "user"):
                first_line = text.strip().split("\n")[0][:80]
                if first_line:
                    print(f"   💬 [{agent_name}]: {first_line}")
        return

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


def extract_pending_gate(event) -> Optional[tuple[str, str, dict[str, Any]]]:
    """Extract (call_id, function_name, args) from a non-partial long-running event."""
    if isinstance(event, dict):
        if event.get("partial", False):
            return None
        lr_ids = event.get("long_running_tool_ids") or event.get("longRunningToolIds") or []
        if not lr_ids:
            return None
        content = event.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            fc = part.get("function_call") or part.get("functionCall")
            if fc and fc.get("id") in lr_ids:
                return (fc.get("id"), fc.get("name"), fc.get("args") or {})
        return None

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


def _patch_engine_methods(engine: Any) -> None:
    """Binds operations from engine schema when Vertex AI SDK skips them due to unsupported api_modes (like async in ADK 2.8)."""
    import types
    from vertexai.reasoning_engines import _reasoning_engines

    for schema in engine.operation_schemas():
        mode = schema.get("api_mode", "")
        m_name = schema.get("name")
        if not m_name or hasattr(engine, m_name):
            continue
        m_doc = schema.get("description", "")
        if mode == "":
            fn = _reasoning_engines._wrap_query_operation(method_name=m_name, doc=m_doc)
            setattr(engine, m_name, types.MethodType(fn, engine))
        elif mode == "stream":
            fn = _reasoning_engines._wrap_stream_query_operation(method_name=m_name, doc=m_doc)
            setattr(engine, m_name, types.MethodType(fn, engine))


def load_delegated_drive_token() -> Optional[str]:
    """Loads and auto-refreshes local user-delegated Drive OAuth access token if present."""
    token_file = os.getenv("DRIVE_CREDENTIALS_FILE", ".drive_user_token.json")
    if not os.path.isfile(token_file):
        return os.getenv("DRIVE_USER_TOKEN")
    try:
        with open(token_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials(
            token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        if not creds.valid:
            creds.refresh(Request())
            data["access_token"] = creds.token
            with open(token_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        return creds.token
    except Exception as err:
        print(f"   ⚠️ Could not refresh delegated Drive token: {err}")
        return None


async def run_pipeline(
    user_prompt: str,
    user_id: str = "executive_user",
    engine_id: Optional[str] = None,
):
    print_banner()
    if engine_id:
        print(f"   Mode: Remote Deployed Agent Engine ({engine_id})")
    else:
        print("   Mode: Local In-Process Runner")
    print("=" * 72)

    initial_state: dict[str, Any] = {}
    delegated_token = load_delegated_drive_token()
    if delegated_token:
        initial_state["delegated_drive_token"] = delegated_token
        print("   🔑 User-Delegated Drive Token: Attached to session")

    if engine_id:
        import vertexai
        from vertexai.preview import reasoning_engines

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        engine = reasoning_engines.ReasoningEngine(engine_id)
        _patch_engine_methods(engine)
        session = engine.create_session(user_id=user_id, state=initial_state)
        session_id = session.get("id") if isinstance(session, dict) else session.id
        print(f"   Session Created: {session_id}")
    else:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        session = await session_service.create_session(
            app_name=app.name,
            user_id=user_id,
            state=initial_state,
        )
        session_id = session.id
        if delegated_token:
            from meeting_prep.auth import set_session_delegated_token
            set_session_delegated_token(session_id, delegated_token)
        runner = Runner(
            app=app,
            session_service=session_service,
            artifact_service=artifact_service,
        )

    next_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_prompt)],
    )

    async def get_current_state() -> dict[str, Any]:
        if engine_id:
            s = engine.get_session(user_id=user_id, session_id=session_id)
            return s.get("state", {}) if isinstance(s, dict) else getattr(s, "state", {})
        s = await session_service.get_session(app_name=app.name, user_id=user_id, session_id=session_id)
        return s.state

    iteration = 1
    while True:
        pending_gate = None
        print(f"\n[Leg {iteration}] Executing agent pipeline...")

        if engine_id:
            msg_payload = next_message.model_dump(mode="json", exclude_none=True)
            for event in engine.stream_query(
                message=msg_payload,
                user_id=user_id,
                session_id=session_id,
            ):
                print_event_log(event)
                gate = extract_pending_gate(event)
                if gate:
                    pending_gate = gate
        else:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=next_message,
            ):
                print_event_log(event)
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
            state = await get_current_state()
            comp_name = state.get("company_input") or "the specified company"
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
                return await get_current_state()

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
                return await get_current_state()

            if action.startswith("r"):
                comment = ""
                while not comment:
                    try:
                        comment = input("Enter revision feedback: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\n\n⚠️ Input cancelled. Leaving session paused at draft review gate.")
                        return await get_current_state()
                    if not comment:
                        print("Revision feedback cannot be empty. Please specify what needs updating (or press Ctrl-C to abort).")

                decision = {"status": "revise", "comment": comment}
                print(f"\n🔄 Resuming with revision feedback: \"{comment}\"")
            elif action.startswith("a"):
                decision = {"status": "approved", "comment": None}
                print("\n✅ Resuming with APPROVAL. Proceeding to publish...")
            else:
                print(f"\n⚠️ Input '{action}' cancelled or invalid. Leaving session paused at draft review gate.")
                return await get_current_state()

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

    final_state = await get_current_state()
    doc_url = final_state.get("published_doc_url")
    if doc_url:
        print(f"\n📎 Published Google Doc: {doc_url}")
    return final_state


def main():
    import os
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
    parser.add_argument(
        "--engine-id",
        type=str,
        default=os.getenv("AGENT_ENGINE_ID") or os.getenv("REASONING_ENGINE_ID"),
        help="Vertex AI Agent Engine ID to run interactively against deployed runtime (default: $AGENT_ENGINE_ID or local in-process)",
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
        engine_id=args.engine_id,
    ))


if __name__ == "__main__":
    main()
