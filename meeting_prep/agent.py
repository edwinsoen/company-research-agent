"""Agent entrypoint for ADK discovery (web, cli, deploy).

Exports root_agent and app.
Source: docs/hld.md §6 & §12.1
"""

from google.adk.apps import App
from meeting_prep.agents.root import create_root_coordinator

root_agent = create_root_coordinator()
agent = root_agent
app = App(name="meeting_prep", root_agent=root_agent)
