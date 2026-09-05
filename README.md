# Company Research Agent

Demo of ADK-first, GEAP-managed multi-agent research assistant.

This is a work in progress.

## Architecture
See [docs/hld.md](docs/hld.md) for full architecture and interface specifications
that will land when the implementation is completed.

## Local Development (Phase 1)

### Setup
1. Create virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Authenticate to Google Cloud (for Vertex AI Gemini inference):
   ```bash
   gcloud auth application-default login
   ```

### Run Automated Headless Verification
To execute Phase 1 and Phase 2 automated test suites:
```bash
# Phase 1: State contracts regression
.venv/bin/python scripts/run_phase1.py

# Phase 2: HITL Gates, targeted refinement loop, and conditional disambiguation
.venv/bin/python scripts/run_phase2.py
```

### Interactive CLI (Phase 2)
The CLI accepts free-text natural language prompts and guides you through the Human-In-The-Loop review gates:
```bash
# Provide a prompt directly:
.venv/bin/python meeting_prep/cli.py "Prepare an executive briefing for my upcoming meeting with Datadog. Focus on AI observability."

# Or launch interactively:
.venv/bin/python meeting_prep/cli.py
```

### Run Local ADK Web UI
To visualize the agent topology and inspect parallel researcher spans in Trace View:
```bash
.venv/bin/adk web --port 8080 .
```
Then navigate to http://localhost:8080/ to interact with the agent.

## License
Distributed under the [MIT License](LICENSE).
