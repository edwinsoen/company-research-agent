"""Unit tests for Phase 5 Long-Term Memory & Delta retrieval (HLD §9.3, §9.4, §9.5).

Tests:
1. search_memory returns has_prior=False when memory is empty.
2. search_memory retrieves prior brief facts and doc_url when memory exists.
3. search_memory enforces company-scoped isolation (facts for Stripe not returned for Acme).
4. preload_memory preloads preferences from memory into state.
5. initialize_briefing_session preserves preloaded preferences when no user overrides provided.
6. save_memory_after_publish skips writing when brief is not approved.
7. save_memory_after_publish writes structured brief record and preferences on approval.
"""

import json
import unittest
from unittest.mock import MagicMock

from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from meeting_prep.callbacks.memory import save_memory_after_publish
from meeting_prep.tools.memory import (
    search_memory,
    preload_memory,
    initialize_briefing_session,
)


class MockToolContext:
    def __init__(self, state=None, memories=None, should_fail=False):
        self.state = state if state is not None else {}
        self.actions = MagicMock()
        self.actions.state_delta = {}
        self._memories = memories or []
        self.should_fail = should_fail

    async def search_memory(self, query: str):
        if self.should_fail:
            raise ValueError("Memory service is not configured.")
        matching = []
        for m in self._memories:
            matching.append(m)
        return SearchMemoryResponse(memories=matching)


class TestMemoryTools(unittest.IsolatedAsyncioTestCase):

    async def test_search_memory_empty(self):
        ctx = MockToolContext(memories=[])
        res = await search_memory(query="Stripe", company="Stripe", tool_context=ctx)
        self.assertFalse(res["has_prior"])
        self.assertEqual(res["prior_facts"], [])

    async def test_search_memory_with_prior_brief_stripped_metadata(self):
        # Real Vertex AI Memory Bank returns empty custom_metadata (Finding 2)
        brief_data = {
            "company": "Stripe",
            "date": "2026-08-01",
            "facts": ["Stripe processes over $1T in payments", "Launched agentic payment APIs"],
            "doc_url": "https://docs.google.com/document/d/mock-stripe-v1/edit",
        }
        mem = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(brief_data))]),
            custom_metadata={},  # Memory Bank strips custom_metadata on retrieve
        )
        ctx = MockToolContext(memories=[mem])

        res = await search_memory(query="Stripe", company="Stripe", tool_context=ctx)
        self.assertTrue(res["has_prior"])
        self.assertEqual(len(res["prior_facts"]), 2)
        self.assertEqual(res["prior_date"], "2026-08-01")
        self.assertEqual(res["doc_url"], brief_data["doc_url"])

    async def test_search_memory_scoped_isolation(self):
        brief_data = {
            "company": "Stripe",
            "date": "2026-08-01",
            "facts": ["Stripe payments platform"],
        }
        mem = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(brief_data))]),
            custom_metadata={},  # Empty custom_metadata as in cloud
        )
        ctx = MockToolContext(memories=[mem])

        # Querying for Acme must NOT return Stripe facts even if metadata is empty
        res = await search_memory(query="Acme", company="Acme", tool_context=ctx)
        self.assertFalse(res["has_prior"])
        self.assertEqual(res["prior_facts"], [])

    async def test_search_memory_rejects_narrative_blobs(self):
        # Narrative extraction memory mentioning "facts" and company must NOT be treated as brief record (Finding 2)
        narrative = "The user asked for facts regarding Stripe and mentioned billing infrastructure."
        mem = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=narrative)]),
            custom_metadata={},
        )
        ctx = MockToolContext(memories=[mem])

        res = await search_memory(query="Stripe", company="Stripe", tool_context=ctx)
        self.assertFalse(res["has_prior"])
        self.assertEqual(res["prior_facts"], [])

    async def test_search_memory_sorts_most_recent_first(self):
        # Memory Bank returns similarity-ordered entries; verify newest by date is selected (Finding 6)
        older_brief = {
            "company": "Stripe",
            "date": "2026-06-01",
            "facts": ["Stripe old fact 2026-06"],
            "doc_url": "https://docs.google.com/document/d/v1",
        }
        newer_brief = {
            "company": "Stripe",
            "date": "2026-08-15",
            "facts": ["Stripe newer fact 2026-08"],
            "doc_url": "https://docs.google.com/document/d/v2",
        }
        mem_older = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(older_brief))]),
            custom_metadata={},
            timestamp="2026-06-01T12:00:00Z",
        )
        mem_newer = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(newer_brief))]),
            custom_metadata={},
            timestamp="2026-08-15T12:00:00Z",
        )
        # Pass newer first or older first; sorting must always select newer
        ctx = MockToolContext(memories=[mem_newer, mem_older])
        res = await search_memory(query="Stripe", company="Stripe", tool_context=ctx)
        self.assertTrue(res["has_prior"])
        self.assertEqual(res["prior_date"], "2026-08-15")
        self.assertEqual(res["prior_facts"], ["Stripe newer fact 2026-08"])

        # Reverse order in memory response
        ctx_rev = MockToolContext(memories=[mem_older, mem_newer])
        res_rev = await search_memory(query="Stripe", company="Stripe", tool_context=ctx_rev)
        self.assertEqual(res_rev["prior_date"], "2026-08-15")

    async def test_search_memory_raises_on_service_failure(self):
        # Failing memory service must raise RuntimeError, not silently pretend baseline (Finding 7)
        ctx = MockToolContext(should_fail=True)
        with self.assertRaises(RuntimeError):
            await search_memory(query="Stripe", company="Stripe", tool_context=ctx)

    async def test_preload_memory_with_stripped_metadata(self):
        # Real Vertex AI Memory Bank returns empty custom_metadata
        prefs_data = {
            "focus_areas": ["Enterprise AI", "Q3 Revenue"],
            "recipients": ["vp@example.com"],
        }
        mem = MemoryEntry(
            content=types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(prefs_data))]),
            custom_metadata={},  # Stripped metadata
        )
        ctx = MockToolContext(memories=[mem])

        preloaded = await preload_memory(tool_context=ctx)
        self.assertEqual(preloaded["focus_areas"], ["Enterprise AI", "Q3 Revenue"])
        self.assertEqual(preloaded["recipients"], ["vp@example.com"])
        self.assertEqual(ctx.actions.state_delta["user_preferences"], preloaded)

    async def test_initialize_briefing_session_merges_preloaded(self):
        ctx = MockToolContext(state={
            "user_preferences": {"focus_areas": ["Preloaded Topic"], "recipients": ["preloaded@example.com"]}
        })

        # User provides company only, no focus or recipients overrides
        res = initialize_briefing_session(company_input="Stripe", tool_context=ctx)
        self.assertEqual(res["focus_areas"], ["Preloaded Topic"])
        self.assertEqual(res["recipients"], ["preloaded@example.com"])

        # User provides explicit overrides
        res2 = initialize_briefing_session(
            company_input="Stripe",
            focus_areas=["New Focus"],
            recipients=["new@example.com"],
            tool_context=ctx,
        )
        self.assertEqual(res2["focus_areas"], ["New Focus"])
        self.assertEqual(res2["recipients"], ["new@example.com"])

    async def test_save_memory_after_publish_on_approval(self):
        class MockCallbackContext:
            def __init__(self, state):
                self.state = state
                self.saved_memories = []
                self.session_added = False

            async def add_memory(self, memories, custom_metadata=None):
                self.saved_memories.extend(memories)

            async def add_session_to_memory(self):
                self.session_added = True

        state = {
            "approval_decision": {"status": "approved"},
            "resolved_entity": {"name": "Stripe"},
            "research_profile": {"findings": [{"claim": "Fact 1"}, {"claim": "Fact 2"}]},
            "published_doc_url": "https://docs.google.com/document/d/doc-1/edit",
            "user_preferences": {"focus_areas": ["AI payments"], "recipients": ["exec@example.com"]},
        }

        ctx = MockCallbackContext(state)
        await save_memory_after_publish(ctx)

        self.assertTrue(ctx.session_added)
        self.assertGreaterEqual(len(ctx.saved_memories), 2)
        topics = [m.custom_metadata.get("topic") for m in ctx.saved_memories]
        self.assertIn("company_brief_history", topics)
        self.assertIn("briefing_preferences", topics)
        # Verify topics key is populated for memories.create config (Finding 1)
        for m in ctx.saved_memories:
            self.assertIn("topics", m.custom_metadata)

    async def test_save_memory_after_publish_skipped_when_not_approved(self):
        class MockCallbackContext:
            def __init__(self, state):
                self.state = state
                self.saved_memories = []
                self.session_added = False

            async def add_memory(self, memories, custom_metadata=None):
                self.saved_memories.extend(memories)

            async def add_session_to_memory(self):
                self.session_added = True

        state = {
            "approval_decision": {"status": "revise"},
            "resolved_entity": {"name": "Stripe"},
        }

        ctx = MockCallbackContext(state)
        await save_memory_after_publish(ctx)

        self.assertFalse(ctx.session_added)
        self.assertEqual(len(ctx.saved_memories), 0)


if __name__ == "__main__":
    unittest.main()
