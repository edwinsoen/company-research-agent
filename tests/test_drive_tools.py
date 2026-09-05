"""Unit tests for Google Drive publishing tools and idempotency.

Tests:
1. create_google_doc in stub mode returns valid DocRef.
2. create_google_doc idempotency: duplicate call returns cached DocRef without re-creating.
3. create_google_doc with different versions creates separate document references.
4. share_doc in stub mode handles multiple emails and masks email in tracing.
5. share_doc handles invalid email format gracefully.
6. redact_email utility masks usernames correctly.
"""

import os
import unittest
from unittest.mock import MagicMock

from meeting_prep.tools.drive import (
    create_google_doc,
    share_doc,
    redact_email,
    get_stub_creation_count,
    reset_stub_creation_counts,
)


class MockToolContext:
    """Mock ADK ToolContext for unit testing."""

    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.actions = MagicMock()
        self.actions.state_delta = {}


class TestDriveTools(unittest.TestCase):

    def setUp(self):
        os.environ['DRIVE_CLIENT_MODE'] = 'stub'
        reset_stub_creation_counts()

    def test_redact_email(self):
        self.assertEqual(redact_email('edwin@example.com'), 'e***n@example.com')
        self.assertEqual(redact_email('al@example.com'), 'a*@example.com')
        self.assertEqual(redact_email(''), '[REDACTED]')
        self.assertEqual(redact_email('not-an-email'), '[REDACTED]')

    def test_create_google_doc_stub(self):
        ctx = MockToolContext()
        doc = create_google_doc(
            title='Executive Brief: Stripe',
            markdown="# Stripe Brief - Details here.",
            brief_id='Stripe',
            version=1,
            tool_context=ctx,
        )

        self.assertEqual(doc['title'], 'Executive Brief: Stripe')
        self.assertEqual(doc['version'], 1)
        self.assertIn('mock-doc-stripe-v1', doc['doc_id'])
        self.assertTrue(doc['doc_url'].startswith('https://docs.google.com/document/d/'))
        self.assertFalse(doc['cached'])

        # State delta should be updated
        self.assertEqual(ctx.actions.state_delta['published_doc_url'], doc['doc_url'])
        self.assertIn('Stripe:v1', ctx.actions.state_delta['published_docs'])
        self.assertEqual(get_stub_creation_count('Stripe', 1), 1)

    def test_create_google_doc_idempotency(self):
        ctx = MockToolContext()

        # Call 1: initial creation
        doc1 = create_google_doc(
            title='Executive Brief: Stripe',
            markdown='# Stripe Brief',
            brief_id='Stripe',
            version=1,
            tool_context=ctx,
        )
        self.assertFalse(doc1['cached'])
        self.assertEqual(get_stub_creation_count('Stripe', 1), 1)

        # Simulate state persistence in session state
        ctx.state['published_docs'] = ctx.actions.state_delta['published_docs']

        # Call 2: repeated call with same brief_id and version
        doc2 = create_google_doc(
            title='Executive Brief: Stripe (Retry)',
            markdown='# Stripe Brief Different Content',
            brief_id='Stripe',
            version=1,
            tool_context=ctx,
        )

        self.assertTrue(doc2['cached'])
        self.assertEqual(doc2['doc_id'], doc1['doc_id'])
        self.assertEqual(doc2['doc_url'], doc1['doc_url'])
        # Assert creation was NOT performed again
        self.assertEqual(get_stub_creation_count('Stripe', 1), 1)

    def test_create_google_doc_different_versions(self):
        ctx = MockToolContext()

        doc_v1 = create_google_doc(
            title='Executive Brief: Stripe v1',
            markdown='# Stripe Brief v1',
            brief_id='Stripe',
            version=1,
            tool_context=ctx,
        )
        ctx.state['published_docs'] = ctx.actions.state_delta['published_docs']

        doc_v2 = create_google_doc(
            title='Executive Brief: Stripe v2',
            markdown='# Stripe Brief v2',
            brief_id='Stripe',
            version=2,
            tool_context=ctx,
        )
        ctx.state['published_docs'] = ctx.actions.state_delta['published_docs']

        self.assertNotEqual(doc_v1['doc_id'], doc_v2['doc_id'])
        self.assertNotEqual(doc_v1['doc_url'], doc_v2['doc_url'])
        self.assertEqual(doc_v1['version'], 1)
        self.assertEqual(doc_v2['version'], 2)
        self.assertEqual(get_stub_creation_count('Stripe', 1), 1)
        self.assertEqual(get_stub_creation_count('Stripe', 2), 1)

    def test_share_doc_stub(self):
        ctx = MockToolContext()
        result = share_doc(
            doc_id='mock-doc-123',
            emails=['exec@example.com', 'team@example.com'],
            tool_context=ctx,
        )

        self.assertEqual(result['doc_id'], 'mock-doc-123')
        self.assertEqual(result['success_count'], 2)
        self.assertEqual(result['failure_count'], 0)
        self.assertEqual(result['shared']['exec@example.com'], 'success')
        self.assertEqual(result['shared']['team@example.com'], 'success')

    def test_share_doc_partial_failure(self):
        ctx = MockToolContext()
        result = share_doc(
            doc_id='mock-doc-123',
            emails=['valid@example.com', 'invalid-email'],
            tool_context=ctx,
        )

        self.assertEqual(result['success_count'], 1)
        self.assertEqual(result['failure_count'], 1)
        self.assertEqual(result['shared']['valid@example.com'], 'success')
        self.assertIn('invalid-email', result['failed'])

    def test_spiffe_session_resolution_from_token(self):
        from meeting_prep.auth import get_drive_session

        os.environ["SPIFFE_TOKEN"] = "test-spiffe-jwt-svid"
        try:
            session = get_drive_session()
            self.assertEqual(session.headers.get("Authorization"), "Bearer test-spiffe-jwt-svid")
        finally:
            os.environ.pop("SPIFFE_TOKEN", None)

    def test_spiffe_session_resolution_from_delegated_context(self):
        from meeting_prep.auth import get_drive_session

        ctx = MockToolContext(state={"delegated_drive_token": "delegated-user-token-123"})
        session = get_drive_session(tool_context=ctx)
        self.assertEqual(session.headers.get("Authorization"), "Bearer delegated-user-token-123")

    def test_auth_raises_runtime_error_without_credentials(self):
        from meeting_prep.auth import get_drive_session

        # Ensure no credential environment variables are set
        env_keys = ["DRIVE_USER_TOKEN", "SPIFFE_TOKEN", "SPIFFE_SVID_PATH", "GOOGLE_APPLICATION_CREDENTIALS", "DRIVE_CREDENTIALS_FILE"]
        old_vals = {k: os.environ.pop(k, None) for k in env_keys}
        os.environ["DRIVE_CREDENTIALS_FILE"] = "/nonexistent/token/path.json"
        try:
            with self.assertRaises(RuntimeError) as cm:
                get_drive_session()
            self.assertIn("No valid delegated user credentials", str(cm.exception))
        finally:
            for k, v in old_vals.items():
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)

    def test_create_google_doc_canonical_key_derived_from_state(self):
        ctx = MockToolContext(state={
            "resolved_entity": {"name": "Stripe, Inc."},
            "draft_version": 2,
        })

        # Call 1: model passes brief_id="Stripe" and version=1
        doc1 = create_google_doc(
            title="Executive Brief: Stripe",
            markdown="# Brief content",
            brief_id="Stripe",
            version=1,
            tool_context=ctx,
        )
        self.assertFalse(doc1["cached"])
        self.assertIn("Stripe, Inc.:v2", ctx.actions.state_delta["published_docs"])

        # Simulate state update
        ctx.state["published_docs"] = ctx.actions.state_delta["published_docs"]

        # Call 2: model passes variation brief_id="stripe" and version=1
        doc2 = create_google_doc(
            title="Executive Brief: Stripe",
            markdown="# Different content",
            brief_id="stripe",
            version=1,
            tool_context=ctx,
        )
        self.assertTrue(doc2["cached"])
        self.assertEqual(doc2["doc_id"], doc1["doc_id"])

    def test_drive_mode_create_and_idempotency_zero_posts(self):
        """Test real drive mode with mocked Drive session: verifies multipart POST and zero POSTs on second pass."""
        from unittest.mock import patch

        os.environ["DRIVE_CLIENT_MODE"] = "drive"
        ctx = MockToolContext()

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"id": "drive-doc-abc-123"}
        mock_session.post.return_value = mock_response

        with patch("meeting_prep.tools.drive.get_drive_session", return_value=mock_session):
            # Pass 1: should issue 1 multipart HTTP POST
            doc1 = create_google_doc(
                title="Executive Brief: Stripe",
                markdown="# Markdown content for Stripe",
                brief_id="Stripe",
                version=1,
                tool_context=ctx,
            )

            self.assertEqual(doc1["doc_id"], "drive-doc-abc-123")
            self.assertEqual(doc1["doc_url"], "https://docs.google.com/document/d/drive-doc-abc-123/edit")
            self.assertFalse(doc1["cached"])
            self.assertEqual(mock_session.post.call_count, 1)

            # Inspect multipart POST arguments
            call_args, call_kwargs = mock_session.post.call_args
            self.assertIn("uploadType=multipart", call_args[0])
            self.assertIn(b"multipart/related", call_kwargs["headers"]["Content-Type"].encode("utf-8"))
            self.assertIn(b"application/vnd.google-apps.document", call_kwargs["data"])
            self.assertIn(b"# Markdown content for Stripe", call_kwargs["data"])

            # Simulate state persistence
            ctx.state["published_docs"] = ctx.actions.state_delta["published_docs"]

            # Pass 2: duplicate call should issue ZERO additional HTTP POSTs
            doc2 = create_google_doc(
                title="Executive Brief: Stripe",
                markdown="# Markdown content for Stripe",
                brief_id="Stripe",
                version=1,
                tool_context=ctx,
            )

            self.assertTrue(doc2["cached"])
            self.assertEqual(doc2["doc_id"], "drive-doc-abc-123")
            self.assertEqual(doc2["doc_url"], doc1["doc_url"])
            self.assertEqual(mock_session.post.call_count, 1, "Duplicate call must issue 0 HTTP POSTs!")

    def test_drive_mode_create_error_graceful_degradation(self):
        """Test drive mode error: non-2xx returns structured error dict rather than raising."""
        from unittest.mock import patch

        os.environ["DRIVE_CLIENT_MODE"] = "drive"
        ctx = MockToolContext()

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_session.post.return_value = mock_response

        with patch("meeting_prep.tools.drive.get_drive_session", return_value=mock_session):
            result = create_google_doc(
                title="Executive Brief: Stripe",
                markdown="# Markdown",
                brief_id="Stripe",
                version=1,
                tool_context=ctx,
            )

            self.assertEqual(result["status"], "failed")
            self.assertIn("503", result["error"])
            self.assertEqual(result["brief_id"], "Stripe")
            self.assertEqual(result["version"], 1)

    def test_drive_mode_share_doc_error_isolation(self):
        """Test share_doc in drive mode isolates failures per recipient without aborting."""
        from unittest.mock import patch

        os.environ["DRIVE_CLIENT_MODE"] = "drive"
        ctx = MockToolContext()

        def side_effect(url, json=None, timeout=None):
            email = (json or {}).get("emailAddress", "")
            resp = MagicMock()
            if "invalid" in email:
                resp.ok = False
                resp.status_code = 400
                resp.text = "Invalid recipient address"
            else:
                resp.ok = True
                resp.status_code = 200
                resp.json.return_value = {"id": "perm-1"}
            return resp

        mock_session = MagicMock()
        mock_session.post.side_effect = side_effect

        with patch("meeting_prep.tools.drive.get_drive_session", return_value=mock_session):
            result = share_doc(
                doc_id="drive-doc-123",
                emails=["invalid@bad.com", "valid@good.com"],
                tool_context=ctx,
            )

            self.assertEqual(result["success_count"], 1)
            self.assertEqual(result["failure_count"], 1)
            self.assertEqual(result["shared"]["valid@good.com"], "success")
            self.assertIn("invalid@bad.com", result["failed"])

    def test_share_retry_behavior_transient_vs_permanent(self):
        """Test _share_drive_file retries transient errors and fails immediately on permanent 4xx."""
        from unittest.mock import patch
        from meeting_prep.tools.drive import _share_drive_file, PermanentDriveError

        mock_session = MagicMock()

        # 1. Transient 429 retries and succeeds
        resp_429 = MagicMock(ok=False, status_code=429, text="Rate limit exceeded")
        resp_200 = MagicMock(ok=True, status_code=200)
        mock_session.post.side_effect = [resp_429, resp_200]

        with patch("meeting_prep.tools.drive.get_drive_session", return_value=mock_session):
            _share_drive_file(doc_id="doc-1", email="user@example.com")
            self.assertEqual(mock_session.post.call_count, 2, "Transient 429 should have retried!")

        # 2. Permanent 400 fails immediately on attempt 1 without retry
        mock_session.reset_mock()
        resp_400 = MagicMock(ok=False, status_code=400, text="Bad Request: invalid email")
        mock_session.post.side_effect = [resp_400]

        with patch("meeting_prep.tools.drive.get_drive_session", return_value=mock_session):
            with self.assertRaises(PermanentDriveError):
                _share_drive_file(doc_id="doc-1", email="bad@example.com")
            self.assertEqual(mock_session.post.call_count, 1, "Permanent 400 must NOT be retried!")


if __name__ == '__main__':
    unittest.main()
