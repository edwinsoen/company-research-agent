"""Application entrypoint for ADK Web and CLI.

Exposes root_agent, agent, and app for 'adk web' discovery.
Source: docs/hld.md §6 & §12.1
"""

from google.adk.apps import App, ResumabilityConfig
from meeting_prep.agents.root import create_root_coordinator

root_agent = create_root_coordinator()
agent = root_agent
app = App(
    name="meeting_prep",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)

