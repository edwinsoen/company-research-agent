"""Unit and integration tests for FastAPI REST server (HLD §10.3, §12.2).

Tests:
1. GET /health returns service status.
2. get_unanswered_pending_gate recovers pending FunctionCall from session events.
3. Decision endpoint rejects unknown session ID with 404.
4. Decision endpoint rejects session with no pending gates with 400.
"""

import unittest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from google.genai import types

from meeting_prep.server import (
    server,
    get_unanswered_pending_gate,
)


class MockEvent:
    def __init__(self, long_running_tool_ids=None, function_calls=None, function_responses=None, partial=False):
        self.long_running_tool_ids = long_running_tool_ids or []
        self.partial = partial
        parts = []
        if function_calls:
            for call_id, name, args in function_calls:
                parts.append(types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args)))
        if function_responses:
            for resp_id, name, resp in function_responses:
                parts.append(types.Part(function_response=types.FunctionResponse(id=resp_id, name=name, response=resp)))
        self.content = types.Content(role="model", parts=parts) if parts else None


class MockSession:
    def __init__(self, events=None, state=None):
        self.id = "mock-session-123"
        self.events = events or []
        self.state = state or {}


class TestServer(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(server)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "meeting_prep_copilot")

    def test_recover_pending_gate_from_events(self):
        # Event 1: Gate paused at approve_brief
        e1 = MockEvent(
            long_running_tool_ids=["call_gate_99"],
            function_calls=[("call_gate_99", "approve_brief", {"draft": "# Brief"})],
        )
        session = MockSession(events=[e1])

        recovered = get_unanswered_pending_gate(session)
        self.assertIsNotNone(recovered)
        call_id, name, args = recovered
        self.assertEqual(call_id, "call_gate_99")
        self.assertEqual(name, "approve_brief")
        self.assertEqual(args["draft"], "# Brief")

    def test_answered_gate_not_returned_as_pending(self):
        # Event 1: Gate paused
        e1 = MockEvent(
            long_running_tool_ids=["call_gate_99"],
            function_calls=[("call_gate_99", "approve_brief", {"draft": "# Brief"})],
        )
        # Event 2: Human response answered call_gate_99
        e2 = MockEvent(
            function_responses=[("call_gate_99", "approve_brief", {"status": "approved"})],
        )
        session = MockSession(events=[e1, e2])

        recovered = get_unanswered_pending_gate(session)
        self.assertIsNone(recovered)

    def test_decision_unknown_session(self):
        # Missing user_id returns 400 Bad Request
        response_no_user = self.client.post(
            "/briefs/non-existent-session-id/decision",
            json={"status": "approved"},
        )
        self.assertEqual(response_no_user.status_code, 400)

        # Provided user_id but unknown session returns 404 Not Found
        response_with_user = self.client.post(
            "/briefs/non-existent-session-id/decision",
            json={"status": "approved", "user_id": "test_user"},
        )
        self.assertEqual(response_with_user.status_code, 404)

    def test_get_brief_user_id_required(self):
        # GET /briefs/{id} without user_id query param returns 400
        response = self.client.get("/briefs/any-session-id")
        self.assertEqual(response.status_code, 400)

        # GET /briefs/{id}?user_id=test_user for unknown session returns 404
        response_not_found = self.client.get("/briefs/any-session-id?user_id=test_user")
        self.assertEqual(response_not_found.status_code, 404)

    def test_derive_brief_status(self):
        from meeting_prep.server import derive_brief_status

        # 1. Pending gate -> paused
        gate = ("call_1", "approve_brief", {})
        self.assertEqual(derive_brief_status({}, gate), "paused")
        self.assertEqual(derive_brief_status({"published_doc_url": "https://docs.google.com/..."}, gate), "paused")

        # 2. Published doc url and no gate -> completed
        self.assertEqual(derive_brief_status({"published_doc_url": "https://docs.google.com/..."}, None), "completed")

        # 3. No gate and no published doc url (e.g. error, or revision without publication) -> failed
        self.assertEqual(derive_brief_status({}, None), "failed")
        self.assertEqual(derive_brief_status({"brief_draft": "# Draft"}, None), "failed")

    def test_decision_requires_status(self):
        # DecisionRequest requires 'status' explicitly (no defaulting to 'approved')
        response = self.client.post(
            "/briefs/any-session-id/decision",
            json={"user_id": "test_user"},
        )
        self.assertEqual(response.status_code, 422)

    def test_transient_delegated_token(self):
        from meeting_prep.auth import (
            set_session_delegated_token,
            get_session_delegated_token,
            clear_session_delegated_token,
        )

        test_sess = "session-test-token-123"
        self.assertIsNone(get_session_delegated_token(test_sess))

        set_session_delegated_token(test_sess, "mock-delegated-bearer-token")
        self.assertEqual(get_session_delegated_token(test_sess), "mock-delegated-bearer-token")

        clear_session_delegated_token(test_sess)
        self.assertIsNone(get_session_delegated_token(test_sess))


if __name__ == "__main__":
    unittest.main()
