"""Phase 4 REST API & Two-Leg HITL Verification Runner.

Verifies the REST API contract and HITL pause/resume flow (HLD §10.3, §12.2):
1. GET /health returns service status.
2. POST /briefs starts Leg 1 execution and pauses at Gate 2 (approve_brief) with no active connection held.
3. GET /briefs/{id} retrieves current paused session status and draft payload with strict user scoping.
4. POST /briefs/{id}/decision recovers pending call from session events, resumes Leg 2,
   and completes publishing.
5. Response returns status == 'completed' with valid published doc_url.

Execution modes:
- Hermetic / In-Process (default, for CI & local tests): Uses FastAPI TestClient in stub Drive mode.
- Live Endpoint: Pass --live <URL> or set LIVE_ENDPOINT_URL=<URL> to verify against a deployed server.

Usage:
    .venv/bin/python scripts/run_phase4.py
    .venv/bin/python scripts/run_phase4.py --live http://localhost:8000
"""

import argparse
import asyncio
import json
import os
import sys

import requests
from fastapi.testclient import TestClient

from meeting_prep.config import MODEL_NAME, PROJECT_ID, LOCATION
from meeting_prep.server import server
from meeting_prep.tools.drive import reset_stub_creation_counts


class LiveRestClient:
    """Simple wrapper over requests.Session presenting the same interface as TestClient."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, path: str, **kwargs):
        return self.session.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path: str, **kwargs):
        return self.session.post(f"{self.base_url}{path}", **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 REST Verification Runner")
    parser.add_argument(
        "--live",
        nargs="?",
        const=os.getenv("LIVE_ENDPOINT_URL", "http://localhost:8000"),
        default=os.getenv("LIVE_ENDPOINT_URL"),
        help="Target live deployed base URL (e.g. http://localhost:8000 or Cloud Run endpoint).",
    )
    args = parser.parse_args()

    live_url = args.live
    mode_label = f"Live Endpoint ({live_url})" if live_url else "Hermetic In-Process (TestClient)"

    print("=" * 75)
    print("🚀 Meeting Prep Copilot — Phase 4 REST API & Runtime Verification")
    print(f"   Mode:     {mode_label}")
    print(f"   Project:  {PROJECT_ID} | Region: {LOCATION} | Model: {MODEL_NAME}")
    print("=" * 75)

    if live_url:
        client = LiveRestClient(live_url)
    else:
        os.environ["DRIVE_CLIENT_MODE"] = "stub"
        reset_stub_creation_counts()
        client = TestClient(server)

    # 1. Health check
    print("\n[1/4] Testing GET /health...")
    health_resp = client.get("/health")
    if health_resp.status_code != 200:
        print(f"      ❌ Health check failed with status {health_resp.status_code}")
        return 1
    print(f"      ✅ Health check OK: {health_resp.json()}")

    # 2. POST /briefs (Leg 1)
    print("\n[2/4] Testing POST /briefs (Leg 1 execution -> Gate 2 pause)...")
    payload = {
        "prompt": "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments and billing platform.",
        "user_id": "test_exec_rest",
    }
    resp1 = client.post("/briefs", json=payload)
    if resp1.status_code != 201:
        print(f"      ❌ POST /briefs failed with status {resp1.status_code}: {resp1.text}")
        return 1

    data1 = resp1.json()
    session_id = data1.get("session_id")
    gate = data1.get("gate")
    status = data1.get("status")

    print(f"      Response Status: {status}")
    print(f"      Session ID:      {session_id}")
    print(f"      Gate:            {gate}")
    print(f"      Draft Length:    {len(data1.get('draft') or '')} chars")

    if status != "paused" or gate != "approve_brief":
        print(f"      ❌ FAILED: Expected paused at 'approve_brief', got status={status}, gate={gate}")
        return 1
    print("      ✅ Leg 1 paused cleanly at Gate 2 (approve_brief)")

    # 3. GET /briefs/{id}
    print(f"\n[3/4] Testing GET /briefs/{session_id} to verify session status query...")
    get_resp = client.get(f"/briefs/{session_id}?user_id=test_exec_rest")
    if get_resp.status_code != 200:
        print(f"      ❌ GET /briefs/{session_id} failed: {get_resp.status_code}")
        return 1
    get_data = get_resp.json()
    if get_data.get("status") != "paused":
        print(f"      ❌ Expected status 'paused', got {get_data.get('status')}")
        return 1
    print(f"      ✅ Verified session state query for {session_id}")

    # 4. POST /briefs/{id}/decision (Leg 2)
    print(f"\n[4/4] Testing POST /briefs/{session_id}/decision (Leg 2 approval & publishing)...")
    decision_payload = {
        "status": "approved",
        "comment": None,
        "user_id": "test_exec_rest",
    }
    resp2 = client.post(f"/briefs/{session_id}/decision", json=decision_payload)
    if resp2.status_code != 200:
        print(f"      ❌ POST /briefs/.../decision failed with {resp2.status_code}: {resp2.text}")
        return 1

    data2 = resp2.json()
    final_status = data2.get("status")
    doc_url = data2.get("doc_url")

    print(f"      Final Status:      {final_status}")
    print(f"      Published Doc URL: {doc_url}")

    if final_status != "completed" or not doc_url or not doc_url.startswith("https://"):
        print(f"      ❌ FAILED: Expected completed status with valid doc_url, got: {data2}")
        return 1

    print("\n🎉 PHASE 4 ACCEPTANCE CRITERIA MET SUCCESSFULLY!")
    print(f"   REST two-leg HITL flow verified: {session_id} -> {doc_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
