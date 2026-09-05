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


if __name__ == '__main__':
    unittest.main()
