"""Application entrypoint for ADK Web and CLI.

Exposes root_agent, agent, and app for 'adk web' discovery and Agent Runtime.
Registers guardrail plugins globally on the App/Runner.
Source: docs/hld.md §6 & §12.1, docs/orchestration-and-logic-enhancements.md §2 & §3
"""

from google.adk.apps import App, ResumabilityConfig
from meeting_prep.agents.root import create_root_coordinator
from meeting_prep.callbacks.telemetry import configure_logging
from meeting_prep.plugins import (
    BudgetPlugin,
    InjectionGuardPlugin,
    PublishPolicyPlugin,
    GroundingGuardPlugin,
    RedactionPlugin,
)

configure_logging()

root_agent = create_root_coordinator()
agent = root_agent
app = App(
    name="meeting_prep",
    root_agent=root_agent,
    plugins=[
        RedactionPlugin(),
        InjectionGuardPlugin(),
        PublishPolicyPlugin(),
        GroundingGuardPlugin(),
        BudgetPlugin(),
    ],
    resumability_config=ResumabilityConfig(is_resumable=True),
)
