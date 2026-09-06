"""Agent entrypoint for ADK discovery (web, cli, deploy).

Exports root_agent and app.
Source: docs/hld.md §6 & §12.1
"""

from meeting_prep.callbacks.telemetry import configure_logging

configure_logging()

from meeting_prep.app import app, root_agent, agent
