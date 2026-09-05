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
To execute the full 7-agent pipeline headlessly and verify state contracts against Stripe:
```bash
.venv/bin/python scripts/run_phase1.py
```

### Run Local ADK Web UI
To visualize the agent topology and inspect parallel researcher spans in Trace View:
```bash
.venv/bin/adk web --port 8080 .
```
Then navigate to http://localhost:8080/ to interact with the agent.

## License
Distributed under the [MIT License](LICENSE).
