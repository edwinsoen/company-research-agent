"""Tests for Strategic Model Routing and dynamic escalation.

Source: docs/orchestration-and-logic-enhancements.md §1
"""

import os
from unittest.mock import MagicMock, patch
import pytest

from meeting_prep.models import (
    FLASH_LITE,
    FLASH,
    PRO,
    MODEL_ROUTING,
    get_agent_model,
)
from meeting_prep.agents.disambiguator import create_entity_disambiguator
from meeting_prep.agents.researchers import (
    create_profile_researcher,
    create_news_researcher,
    create_focus_researcher,
)
from meeting_prep.agents.delta import create_delta_agent
from meeting_prep.agents.composer import create_composer
from meeting_prep.agents.approval import create_approval_gate, create_refinement_router
from meeting_prep.agents.publisher import create_publisher
from meeting_prep.callbacks.telemetry import after_agent_telemetry


def test_model_routing_table_completeness():
    """Assert all core pipeline agents have designated model tiers."""
    expected_agents = {
        "entity_disambiguator": FLASH_LITE,
        "profile_researcher": FLASH,
        "news_researcher": FLASH,
        "focus_researcher": FLASH,
        "delta_agent": PRO,
        "composer": FLASH,
        "approval_gate": FLASH_LITE,
        "refinement_router": FLASH,
        "publisher": FLASH_LITE,
    }

    for agent_name, expected_model in expected_agents.items():
        assert agent_name in MODEL_ROUTING, f"Missing {agent_name} in MODEL_ROUTING"
        assert MODEL_ROUTING[agent_name] == expected_model, (
            f"Expected {expected_model} for {agent_name}, got {MODEL_ROUTING[agent_name]}"
        )


def test_agent_factory_model_binding():
    """Verify agent constructors assign models according to MODEL_ROUTING."""
    disambiguator = create_entity_disambiguator()
    assert disambiguator.model == FLASH_LITE

    profile = create_profile_researcher()
    assert profile.model == FLASH

    news = create_news_researcher()
    assert news.model == FLASH

    focus = create_focus_researcher()
    assert focus.model == FLASH

    delta = create_delta_agent()
    assert delta.model == PRO

    composer = create_composer()
    assert composer.model == FLASH

    gate = create_approval_gate()
    assert gate.model == FLASH_LITE

    router = create_refinement_router()
    assert router.model == FLASH

    publisher = create_publisher()
    assert publisher.model == FLASH_LITE


def test_get_agent_model_dynamic_escalation():
    """Verify get_agent_model resolves baseline and escalated models."""
    # Standard agent
    assert get_agent_model("profile_researcher") == FLASH

    # Composer baseline
    assert get_agent_model("composer") == FLASH
    assert get_agent_model("composer", {}) == FLASH

    # Composer escalated to PRO
    state = {"composer_model": PRO}
    assert get_agent_model("composer", state) == PRO


def test_telemetry_records_resolved_model_span_attribute():
    """Verify after_agent_telemetry records the subagent.model attribute matching resolved routing."""
    mock_span = MagicMock()
    mock_span.is_recording.return_value = True

    with patch("meeting_prep.callbacks.telemetry.trace.get_current_span", return_value=mock_span):
        # 1. Test profile_researcher (FLASH)
        ctx1 = MagicMock()
        ctx1.agent_name = "profile_researcher"
        ctx1.state = {}
        after_agent_telemetry(ctx1)
        mock_span.set_attribute.assert_any_call("subagent.model", FLASH)

        # 2. Test delta_agent (PRO)
        ctx2 = MagicMock()
        ctx2.agent_name = "delta_agent"
        ctx2.state = {}
        after_agent_telemetry(ctx2)
        mock_span.set_attribute.assert_any_call("subagent.model", PRO)

        # 3. Test composer escalated to PRO
        ctx3 = MagicMock()
        ctx3.agent_name = "composer"
        ctx3.state = {"composer_model": PRO}
        after_agent_telemetry(ctx3)
        mock_span.set_attribute.assert_any_call("subagent.model", PRO)
