# Meeting Prep Copilot — Multi-Agent Research Assistant

An enterprise-grade, ADK-first multi-agent briefing assistant designed to prepare executive research briefs before meetings with external companies, built with Google Agent Development Kit (ADK) and Gemini Enterprise Agent Platform (GEAP).

Source of truth and architecture design: [docs/hld.md](docs/hld.md).

---

## Overview & Purpose

Preparing for external meetings—whether with partners, clients, or vendors—requires actionable context about who the company is, what products they build, and what has developed recently. Conducting thorough public research manually is time-consuming, fragmented across disjointed sources, and repetitive when meeting with companies repeatedly over time. Executives and account leads often struggle to quickly isolate high-signal context and identify **what is genuinely new** since their last touchpoint.

**Meeting Prep Copilot** automates this entire lifecycle as an autonomous, multi-agent research team:
- **Grounded Parallel Research**: Concurrently maps the company's profile, extracts 90-day news events, and investigates user-specified strategic focus areas using live Google Search grounding.
- **Continuous Memory & Delta Analysis**: Leverages long-term Memory Bank storage to track prior briefings, automatically highlighting incremental changes, new announcements, and strategic pivots since the baseline touchpoint while preloading standing user preferences.
- **Human-In-The-Loop (HITL) Governance**: Pauses at non-blocking review gates to let the user inspect the synthesized draft, request targeted refinements routed by an LLM classifier, or approve the brief.
- **Automated Publishing**: Directly converts approved briefs into formatted Google Docs and shares them with designated meeting participants under user-delegated authority.

---

## Architecture (Phases 1–7)

```mermaid
flowchart TD
    User([User Prompt]) --> Preload[BriefingPreloadMemoryTool\nPreload Preferences]
    Preload --> Coordinator[root_coordinator\nExtracts Company & Focus]
    Coordinator --> Disambiguator{entity_disambiguator}
    
    Disambiguator -- "Ambiguous (Gate 1)" --> HITL_Gate1[request_disambiguation\nLongRunningFunctionTool]
    HITL_Gate1 -- "User Selection" --> Researchers
    Disambiguator -- "Unique Match" --> Researchers
    
    subgraph ParallelResearchers [Parallel Research Branch]
        R1[profile_researcher\ngoogle_search]
        R2[news_researcher\ngoogle_search]
        R3[focus_researcher\ngoogle_search]
    end
    
    Researchers --> ParallelResearchers
    ParallelResearchers --> Delta[delta_agent\nScoped search_memory & Prior Delta]
    Delta --> Composer[composer\nMarkdown Brief + Delta Section]
    Composer --> HITL_Gate2[approve_brief\nGate 2 Pause]
    
    HITL_Gate2 -- "Revise + Comment" --> Router[refinement_router\nLLM Classifier]
    Router -- "Targeted Rerun" --> ParallelResearchers
    
    HITL_Gate2 -- "Approved" --> Publisher[publisher]
    Publisher --> DriveTools[create_google_doc & share_doc\nIdempotent on brief_id:version]
    DriveTools --> GoogleDoc([Google Doc Published])
    Publisher --> Callback[save_memory_after_publish\nPost-Approval Callback]
    
    subgraph ManagedStorage [Managed Storage & Memory Layers]
        MB[(Vertex AI Memory Bank\ncompany_brief_history 90d TTL\nbriefing_preferences)]
        Sessions[(Agent Engine Sessions\nManaged Session State)]
        GCS[(GCS Artifact Bucket\nDrafts & Raw Findings)]
    end
    
    Preload -. Reads Preferences .-> MB
    Delta -. Scoped Search .-> MB
    Callback -. Direct Ingest & Extraction .-> MB
    Publisher -. Session State .-> Sessions
```

### Key Capabilities

1. **Multi-Agent Research Pipeline (Phase 1)**:
   - **`root_coordinator`**: Ingests free-text prompts, extracts target company, focus topics, and recipient lists. Preloads standing preferences from Memory Bank.
   - **`entity_disambiguator`**: Verifies company domain and business entity against public web grounding.
   - **Parallel Researchers**: Concurrently executes company profile analysis (`profile_researcher`), recent news retrieval (`news_researcher`), and user-specified focus area deep dives (`focus_researcher`).
   - **`delta_agent`**: Computes incremental delta against prior briefings retrieved from Memory Bank (`has_prior`, structured fact comparisons, word boundary isolation, and recency sorting per HLD §9).
   - **`composer`**: Synthesizes findings into an executive markdown briefing with structured sections, prominent delta analysis, and inline source citations.

2. **Human-In-The-Loop (HITL) Workflow (Phase 2)**:
   - **Non-blocking Pauses**: Implemented using ADK's `LongRunningFunctionTool` primitive (`request_disambiguation` for Gate 1, `approve_brief` for Gate 2).
   - **Stateless Two-Legged Invocations**: Pauses hold zero open network connections; state is maintained in the session store. Execution resumes seamlessly upon receiving a matching `FunctionResponse`.
   - **LLM Refinement Router**: If the reviewer requests revisions, `refinement_router` classifies feedback and selectively triggers **only** the affected researchers, preserving unchanged sections and minimizing redundant LLM calls.

3. **Publishing & Idempotency (Phase 3)**:
   - **`create_google_doc`**: Converts markdown briefings to native Google Docs via the Google Drive API (`uploadType=multipart`).
   - **Strict Idempotency**: Document creation is idempotent on `(brief_id, version)`. Repeated approvals return the existing `DocRef` without re-creating files or leaving duplicate docs in Drive.
   - **Error-Isolated Sharing (`share_doc`)**: Shares documents with individual recipients; failures on one recipient do not abort others.
   - **Decoupled Identity Provider**: Eliminates hardcoded credentials inside tools. Outbound Drive calls support offline stub execution, local browser OAuth, and live Drive publishing via user-delegated authority.

4. **Agent Runtime, Agent Identity & REST Server (Phase 4)**:
   - **Vertex AI Agent Engine (Reasoning Engine)**: Full deployment to Google Cloud Vertex AI Agent Engine with managed session persistence (`VertexAiSessionService`) and artifact storage (`GcsArtifactService`).
   - **SPIFFE Agent Identity (`AGENT_IDENTITY`)**: Deployed under a dedicated per-agent SPIFFE identity principal with least-privilege IAM roles (`roles/aiplatform.agentDefaultAccess`, `roles/aiplatform.expressUser`, `roles/storage.objectUser`), rejecting ambient service accounts (HLD §12A.1).
   - **FastAPI REST API (`meeting_prep/server.py`)**: Programmatic REST interface implementing two-leg execution with non-blocking pauses (`POST /briefs`, `POST /briefs/{id}/decision`, `GET /briefs/{id}`, `GET /health`). Recovers pending function calls directly from session events without requiring an auxiliary database.
   - **Remote Interactive CLI**: Interactive CLI supports connecting directly to a remote deployed Agent Engine instance (`--engine-id`) with event streaming and remote HITL approval.
   - **User-Delegated Remote Drive Publishing**: Facilitates end-user Drive authorization in remote Agent Engine executions via transient bearer token delegation (`delegated_drive_token`).

5. **Long-Term Memory Bank & Incremental Delta Retrieval (Phase 5)**:
   - **Vertex AI Memory Bank**: Managed cross-session memory backed by `VertexAiMemoryBankService` (with `LocalMemoryService` in-memory fallback for local development).
   - **Custom Topics & Retention**:
     - `company_brief_history`: 90-day TTL (`7776000s`) capturing structured fact comparisons, headline findings, and published doc links.
     - `briefing_preferences`: Indefinite retention for standing user preferences (e.g. focus areas, formatting preferences, recipient lists).
   - **Post-Approval Write Callback (`save_memory_after_publish`)**: Attached `after_agent` to `publisher`. Ingests memory **strictly on approval**, preventing rejected or unverified drafts from polluting long-term memory. Employs direct `add_memory` ingestion for structured brief history and extraction for user preferences.
   - **Dual Read Strategies**:
     - Turn-start preference preloading on `root_coordinator` via `BriefingPreloadMemoryTool` and `initialize_briefing_session`.
     - Scoped delta retrieval on `delta_agent` via company-scoped `search_memory`, using regex word boundary isolation (`_company_matches`) to avoid cross-company false positives and ISO recency sorting.
   - **Fail-Loud Cloud Placeholder**: `UnconfiguredCloudMemoryService` ensures import safety for `server.py` at boot while raising an explicit `RuntimeError` on read/write if deployed to cloud without a configured `AGENT_ENGINE_ID`.

6. **Observability, Distributed Tracing & Redaction (Phase 6)**:
   - **Structured JSON Logging**: Cloud Logging-compatible JSON formatting on stdout across all environments with automatic 1:1 trace correlation (`logging.googleapis.com/trace` and `logging.googleapis.com/spanId`).
   - **Intent vs. Outcome Operational Logging**: Dual-layer operational logging across agents and tools (`log_intent` / `log_outcome`) recording parameters, execution latency (`duration_ms`), and outcome statuses.
   - **Distributed Tracing (OpenTelemetry)**: Dedicated human wait time spans (`hitl_wait.<gate_name>`) measuring human decision duration separately from agent compute, custom subagent span attributes (`subagent.name`, `subagent.model`, `subagent.latency_ms`), and refinement router classification telemetry.
   - **Terminal UI Provenance Panel**: Formatted ASCII table summarizing pipeline execution latency, model attribution, and completion status per research section.
   - **Comprehensive PII & Secret Redaction Pipeline**: Multi-entity sanitization (`RedactionPipeline`, `RedactionFilter`, `RedactionPlugin`) filtering emails, phone numbers, Bearer tokens, API keys, and IP addresses while preserving cloud resource identifiers and semantic version numbers.

7. **Orchestration, Model Routing & Security Guardrails (Phase 7)**:
   - **Strategic Multi-Tier Model Routing**: Optimizes task shape, speed, and inference cost by routing Flash-Lite (`gemini-3.5-flash-lite`) to deterministic agents (`entity_disambiguator`, `approval_gate`, `publisher`), Flash (`gemini-3.7-flash`) to high-volume parallel researchers and router, and Pro (`gemini-3.1-pro-preview`) to complex synthesis and delta reasoning (`delta_agent`, `composer`).
   - **Dynamic Pro Tier Escalation**: When initial Flash synthesis lacks grounded source citations, `GroundingGuardPlugin` triggers an in-flight Pro retry with corrective directives, tracking token ceilings and emitting telemetry spans.
   - **Decoupled Runtime Guardrail Plugins (`meeting_prep/plugins/`)**: Global ADK `BasePlugin` implementations decoupled from agent nodes:
     - `BudgetPlugin`: Prioritized first in the plugin chain to track token counts and model invocations, terminating gracefully with `BudgetExceededError`.
     - `InjectionGuardPlugin`: Intercepts researcher model outputs (`after_model_callback`) and Gemini Google Search grounding metadata (`LlmResponse.grounding_metadata`) to detect prompt injection vectors, neutralizing payloads to `[REDACTED_POTENTIAL_PROMPT_INJECTION]`.
     - `RedactionPlugin`: Global PII/secret scrubbing returning `None` when unmodified to preserve subsequent plugin execution chains.
     - `GroundingGuardPlugin`: Zero-LLM claim-to-citation validator with structural line detection (ignoring tables, blockquotes, code fences, labels) and citation domain matching against researched sources.
     - `PublishPolicyPlugin`: Hard security gate on Drive tools verifying human review approval in session state, checking recipient email allowlists, and populating the idempotency cache.
   - **Resilient Multi-Agent Loop & State Lifecycle**:
     - Isolates human review loop exits to `approval_gate` rather than inner agent escalations, preventing outer loop interruptions.
     - Handles ADK `State` lifecycle on Vertex AI Reasoning Engine using sentinel writes (`state[key] = None`) to prevent cross-run state pollution without raising errors on non-dictionary storage.
   - **Proactive User-Delegated Publishing**: Automatically refreshes OAuth access tokens in the CLI before passing them to remote Agent Engine sessions, preventing mid-flight token expiration during live Google Doc creation.

---

## Local Development Setup

### 1. Prerequisites & Installation

Requires Python 3.11+.

```bash
# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Cloud Authentication

Authenticate with Google Cloud to enable Vertex AI Gemini model inference:

```bash
gcloud auth application-default login
```

The active GCP project is dynamically detected from your environment (`GOOGLE_CLOUD_PROJECT` or `gcloud config get-value project`).

---

## Running the Copilot

### 1. Interactive CLI Client

The interactive CLI ([meeting_prep/cli.py](meeting_prep/cli.py)) drives the full Human-In-The-Loop review cycle:

```bash
# Run locally in-process:
.venv/bin/python -m meeting_prep.cli "Prepare an executive briefing for my upcoming meeting with Stripe. Focus on AI agent payments."

# Or launch interactively:
.venv/bin/python -m meeting_prep.cli

# Or run interactively against a deployed Vertex AI Agent Engine:
.venv/bin/python -m meeting_prep.cli --engine-id <AGENT_ENGINE_ID> "Brief me for meeting with Anthropic."
```

**Interactive Walkthrough**:
1. Preloads standing preferences from Memory Bank if available.
2. The agent gathers intelligence and displays the formatted brief draft in the terminal.
3. Prompts for your decision:
   ```text
   Decision: [A]pprove & Publish, or [R]evise with feedback? [a/r]:
   ```
4. Type `r` to supply feedback (e.g. *"Focus more on their Stablecoin partnerships"*). The refinement router will classify your feedback, rerun the relevant researcher, and render an updated draft.
5. Type `a` to approve. The publisher generates the Google Doc, outputs the link, and triggers the `save_memory_after_publish` callback to record the brief in Memory Bank.

### 2. Programmatic REST API Server

Meeting Prep Copilot provides a FastAPI REST interface ([meeting_prep/server.py](meeting_prep/server.py)) supporting two-leg non-blocking HITL execution (HLD §10.3, §12.2):

```bash
# Start the REST API server:
.venv/bin/uvicorn meeting_prep.server:server --host 0.0.0.0 --port 8000
```

#### API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check endpoint returning service status and environment. |
| `POST` | `/briefs` | **Leg 1**: Ingests prompt, runs research pipeline, pauses at HITL gate (`approve_brief` or `request_disambiguation`), returns session ID and draft. |
| `GET` | `/briefs/{id}?user_id={uid}` | Retrieves session status, pending gate name, and current draft. |
| `POST` | `/briefs/{id}/decision` | **Leg 2**: Submits user decision (`status: approved` or `status: revise`), resumes execution, and returns published doc URL. |

#### Example REST Workflow (curl)

```bash
# 1. Start Leg 1 (runs until Gate 2 pause):
curl -X POST http://localhost:8000/briefs \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Brief me on Datadog for upcoming partnership call.", "user_id": "exec_1"}'

# Response returns: {"status": "paused", "session_id": "...", "gate": "approve_brief", "draft": "..."}

# 2. Submit approval to resume Leg 2 and publish:
curl -X POST http://localhost:8000/briefs/<SESSION_ID>/decision \
  -H "Content-Type: application/json" \
  -d '{"status": "approved", "user_id": "exec_1"}'

# Response returns: {"status": "completed", "doc_url": "https://docs.google.com/document/d/..."}
```

### 3. Automated Verification Suites

Run the standalone verification suites for each phase:

```bash
# Phase 1: Multi-agent execution and session state contracts
.venv/bin/python scripts/run_phase1.py

# Phase 2: HITL pause/resume, targeted router reruns, and disambiguation
.venv/bin/python scripts/run_phase2.py

# Phase 3: Document publishing and double-approval idempotency
.venv/bin/python scripts/run_phase3.py

# Phase 4 (In-Process): REST API two-leg HITL contracts and session recovery
.venv/bin/python scripts/run_phase4.py

# Phase 4 (Remote Runtime): Verify deployed Agent Engine endpoint / api_server
.venv/bin/python scripts/verify_deployed_runtime.py --endpoint-url http://localhost:8000

# Phase 5 (Local Lifecycle): 2-run lifecycle (baseline creation, delta retrieval, and preference preloading)
.venv/bin/python scripts/run_phase5.py

# Phase 5 (Remote Memory Bank): Cross-session retrieval and active polling against deployed Agent Engine
.venv/bin/python scripts/verify_remote_memory.py [AGENT_ENGINE_ID]

# Phase 6 (Observability & Tracing): Structured JSON logging, Cloud Trace correlation, and PII redaction
.venv/bin/python scripts/run_phase6.py
```

### 4. Running Unit Tests

Execute the full unit test suite (71 unit tests covering strategic model routing, runtime guardrail plugins, Drive tools, Memory Bank, REST server, distributed tracing, and PII redaction):

```bash
# Run with pytest (recommended):
.venv/bin/pytest -v tests/

# Or run with unittest:
.venv/bin/python -m unittest discover -s tests
```

---

## Infrastructure & Agent Engine Deployment (Phase 4)

### 1. Terraform Infrastructure (`infra/`)

Terraform configurations ([infra/main.tf](infra/main.tf)) manage cloud resources:
- **GCS Artifact Bucket**: Dedicated bucket (`${project_id}-meeting_prep-artifacts`) for storing draft markdown versions and raw search findings.
- **Agent Identity IAM Roles**: Provisioned per-agent SPIFFE principal receives `roles/aiplatform.agentContextEditor`, `roles/aiplatform.agentDefaultAccess`, `roles/aiplatform.expressUser`, and `roles/storage.objectUser` on the artifact bucket.

```bash
cd infra
terraform init
terraform apply -var="project_id=$(gcloud config get-value project)"
```

### 2. Deploying to Vertex AI Agent Engine

Use the deployment script ([scripts/deploy_agent_engine.sh](scripts/deploy_agent_engine.sh)) to package and deploy the copilot:

```bash
# Deploy new or update existing Agent Engine instance
./scripts/deploy_agent_engine.sh
```

The script sets `DEPLOYMENT_ENV=cloud`, configures Cloud Trace export (`--otel_to_cloud`), specifies the artifact bucket, and packages the application using `adk deploy agent_engine`.

---

## Long-Term Memory Bank Architecture (Phase 5)

Meeting Prep Copilot implements a three-tier memory architecture (HLD §9):

1. **Layer 1: Session State (`VertexAiSessionService`)**: Holds intra-session context across HITL pause/resume legs and refinement iterations.
2. **Layer 2: Artifacts (`GcsArtifactService`)**: Persists versioned briefing drafts (`brief_draft_v{n}.md`) and raw search responses for context compaction and diff audits.
3. **Layer 3: Long-Term Memory (`VertexAiMemoryBankService`)**: Managed cross-session memory in Agent Engine Memory Bank.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Vertex AI Memory Bank                             │
├────────────────────────────────────┬────────────────────────────────────┤
│ Topic: company_brief_history       │ Topic: briefing_preferences        │
│ TTL: 90 days (7776000s)            │ TTL: Indefinite                    │
│ Content: Structured fact records   │ Content: User focus areas, format  │
│ (headline facts, doc link, date)   │ preferences, recipient lists       │
│ Write: Direct add_memory on approve│ Write: add_session_to_memory       │
│ Read: Company-scoped search_memory │ Read: BriefingPreloadMemoryTool    │
└────────────────────────────────────┴────────────────────────────────────┘
```

### Key Memory Implementation Details

- **Post-Approval Ingestion**: `save_memory_after_publish` callback in [meeting_prep/callbacks/memory.py](meeting_prep/callbacks/memory.py) ensures only approved briefs enter long-term storage.
- **Custom Memory Topic Schema**: Metadata payloads use `topics=[{"custom_memory_topic_label": "company_brief_history"}]` adhering strictly to Vertex AI's `AgentEngineMemoryConfig` schema.
- **Word-Boundary Company Isolation**: `_company_matches` in [meeting_prep/tools/memory.py](meeting_prep/tools/memory.py) uses regex word boundaries (`\b`) to prevent substring collisions (e.g. `Box` will not match `Boxed`, while `Meta` correctly matches `Meta Platforms`).
- **Recency Sorting**: Undated records fall back to `""` so ISO dates (`"2026-..."`) strictly win descending recency sort.
- **Delta Presentation**: When `has_prior` is true, the brief opens with `### What Changed Since Prior Briefing (YYYY-MM-DD)` highlighting new developments; when false, an explicit baseline marker is rendered.

---

## Google Drive Publishing Modes (Phase 3 & 4)

Configured via the `DRIVE_CLIENT_MODE` environment variable:

### Mode A: Offline Stub (Default)

Fast, offline, and deterministic. Simulates Drive API file creation and permissions without making network calls or requiring Drive OAuth grants:

```bash
DRIVE_CLIENT_MODE=stub .venv/bin/python scripts/run_phase3.py
```

### Mode B: Live Google Drive

Publishes real Google Docs directly to Google Drive with strict idempotency on `(brief_id, version)`.

**Credential Options**:
1. **Interactive browser OAuth** (recommended for local dev):
   ```bash
   .venv/bin/python scripts/auth_drive_user.py
   ```
   *(Signs in via browser and saves credentials to `.drive_user_token.json` or path in `DRIVE_CREDENTIALS_FILE`)*.

2. **Direct delegated token**:
   ```bash
   export DRIVE_USER_TOKEN="<oauth-access-token>"
   ```

3. **Remote Agent Engine Delegation**:
   When invoking the remote Agent Engine or REST API, pass the user OAuth access token in the request (`user_token`) or session state (`delegated_drive_token`). The agent creates documents as the user without holding static keys in the container.

---

## Observability & Inspection (Phase 6)

Source of truth: [docs/hld.md](docs/hld.md) §11, §13, §16.

Meeting Prep Copilot provides end-to-end production observability across all environments (local CLI, FastAPI REST server, and deployed Vertex AI Agent Engine):
- **Structured JSON Logging**: Cloud Logging-compatible JSON formatting on `stdout` across all runtimes, with 1:1 trace correlation.
- **Intent vs. Outcome Operational Logging**: Dual-layer operational event pairing tracking parameters, duration (`duration_ms`), and execution status (`outcome_status`).
- **Distributed Tracing (OpenTelemetry)**: Explicit spans for human review wait times (`hitl_wait.<gate_name>`), subagent execution metrics (`subagent.name`, `subagent.model`, `subagent.latency_ms`), and refinement classification routing.
- **Terminal UI Provenance Panel**: Formatted ASCII table tracking per-section latency and model attribution.
- **Multi-Entity PII & Secret Redaction Pipeline**: Defense-in-depth sanitization of tokens, credentials, API keys, phone numbers, and emails across logs and tool data context.

---

### 1. Structured JSON Logging & 1:1 Cloud Trace Correlation

All application runtimes (`meeting_prep/app.py`, `meeting_prep/server.py`, `meeting_prep/agent.py`, `meeting_prep/cli.py`) initialize structured logging at module load via `configure_logging()`. Logs are emitted as single-line JSON records formatted by `JsonTraceFormatter`:

```json
{
  "timestamp": "2026-09-06T10:28:45.847452+00:00",
  "severity": "INFO",
  "message": "OUTCOME: [publisher] (SUCCESS) Agent 'publisher' completed execution in 11165.19ms",
  "logger": "meeting_prep.callbacks.telemetry",
  "component": "publisher",
  "event_type": "outcome",
  "outcome": "Agent 'publisher' completed execution in 11165.19ms",
  "outcome_status": "SUCCESS",
  "duration_ms": 11165.19,
  "company": "Stripe",
  "logging.googleapis.com/trace": "projects/<PROJECT_ID>/traces/<TRACE_ID_HEX>",
  "logging.googleapis.com/spanId": "<SPAN_ID_HEX>",
  "logging.googleapis.com/trace_sampled": true
}
```

- **Trace Correlation**: Automatically extracts active trace and span IDs from the OpenTelemetry context (`trace.get_current_span()`). In Google Cloud Logging, this enables direct correlation between logs and trace waterfalls.
- **Fail-Safe Sanitization**: Every log record passes through `RedactionFilter` before being serialized.

---

### 2. Dual-Layer Intent vs. Outcome Logging

Operational actions across agents and tools are logged in explicit pairs:
1. **`INTENT`**: Emitted before starting an operation, capturing context and input parameters:
   - Tool calls (`search_memory`, `create_google_doc`, `share_doc`, `request_disambiguation`, `approve_brief`).
   - Agent steps (`profile_researcher`, `news_researcher`, `focus_researcher`, `delta_agent`, `composer`, `publisher`).
   - Memory Bank writes (`save_memory_after_publish`).
2. **`OUTCOME`**: Emitted upon completion, capturing duration (`duration_ms`), status (`SUCCESS` / `FAILED`), and operational results:
   - Tool results (e.g. `doc_id`, `success_count`, `failure_count`).
   - Agent execution latencies and brief sections updated.

---

### 3. Distributed Tracing Hierarchy & Custom Spans

Built on OpenTelemetry Python SDK. In cloud environments (`DEPLOYMENT_ENV=cloud` or `ENABLE_CLOUD_TRACE=true`), `configure_telemetry()` registers an SDK `TracerProvider` with `CloudTraceSpanExporter` using `BatchSpanProcessor`.

```
Reasoning Engine Execution (Root Span)
├── subagent.profile_researcher [subagent.name, subagent.model, subagent.latency_ms]
├── subagent.news_researcher    [subagent.name, subagent.model, subagent.latency_ms]
├── subagent.focus_researcher   [subagent.name, subagent.model, subagent.latency_ms]
├── subagent.delta_agent        [subagent.name, subagent.model, subagent.latency_ms]
├── subagent.composer           [subagent.name, subagent.model, subagent.latency_ms]
├── hitl_wait.approve_brief     [hitl.gate_name, hitl.wait_duration_s, hitl.decision_status]
├── refinement_router           [refinement.target, refinement.confidence, refinement.directive]
└── subagent.publisher          [subagent.name, subagent.model, subagent.latency_ms]
```

- **Dedicated HITL Wait Spans**: `record_hitl_wait_span` records a span around human review gates (`hitl_wait.approve_brief`, `hitl_wait.request_disambiguation`). This measures human idle/review time separately from autonomous agent runtime.
- **Subagent Custom Attributes**: Every researcher and synthesis subagent span carries `subagent.name`, `subagent.model`, and execution latency (`subagent.latency_ms`, `subagent.latency_s`).
- **Refinement Router Telemetry**: Injects `refinement.target`, `refinement.confidence`, `refinement.directive`, and `refinement.iteration` attributes.
- **Concurrency Isolation**: Agent start times are scoped by unique context and invocation IDs (`_get_context_start_key`), guaranteeing isolated latency calculations across overlapping concurrent requests.

---

### 4. Terminal UI Provenance Panel

Upon brief completion, the CLI renders an ASCII provenance summary from session state metadata:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ SECTION                  │ PRODUCED BY          │ LATENCY  │ STATUS    │
├──────────────────────────────────────────────────────────────────────┤
│ Company Profile          │ profile_researcher   │ 5.67s    │ COMPLETED │
│ Focus Areas              │ focus_researcher     │ 13.23s   │ COMPLETED │
│ Recent Developments      │ news_researcher      │ 13.90s   │ COMPLETED │
│ Executive Delta          │ delta_agent          │ 2.51s    │ COMPLETED │
│ Brief Synthesis          │ composer             │ 9.67s    │ COMPLETED │
│ Google Doc Publishing    │ publisher            │ 11.17s   │ COMPLETED │
├──────────────────────────────────────────────────────────────────────┤
│ TOTAL PIPELINE LATENCY                          │ 56.15s   │ DONE      │
└──────────────────────────────────────────────────────────────────────┘
```

---

### 5. Multi-Entity PII & Secret Redaction Pipeline

Protects sensitive credentials and user data across both logs and agent contexts:
- **Sanitized Entities**:
  - **Bearer & OAuth Tokens**: `Bearer [BEARER_TOKEN_REDACTED]`, `ya29.*`
  - **API Keys**: Google API keys (`AIza*`) and general keys (`AQ.*`)
  - **Email Addresses**: Preserves domain while masking username (`u*******e@domain.com`)
  - **Phone Numbers**: International and local formats with standard separators or area code parentheses (`[PHONE_REDACTED]`)
  - **IPv4 Addresses**: Real IPs masked (`[IP_REDACTED]`) while 4-part semantic versions (`version 1.10.0.1`) are preserved
  - **Sensitive Keys**: Dictionary keys like `password`, `token`, `secret`, `auth`, `api_key`
- **Zero False-Positive Precision**: Unhyphenated integer runs (such as GCP project numbers in resource paths `projects/<PROJECT_ID>/locations/...` and Reasoning Engine IDs) and common words (such as `"the token is stale"`) are preserved intact.
- **ADK `RedactionPlugin`**: Attached to the ADK `App` to sanitize tool result dictionaries in `after_tool_callback` before outputs enter LLM prompt context.

---

### 6. Where to View Traces and Logs in Google Cloud

> **Note**: Replace `<PROJECT_ID>` with your active Google Cloud project ID (e.g. `$(gcloud config get-value project)`).

#### A. Google Cloud Trace Console

View distributed trace waterfalls, subagent parallel branches, and HITL wait durations:

- **Console URL**:
  ```text
  https://console.cloud.google.com/traces/list?project=<PROJECT_ID>
  ```
- **Navigation**: In the Google Cloud Console, navigate to **Observability / Operations > Trace > Trace list**.
- **Inspecting Spans**:
  - Filter by root span or subagent span names (e.g. `hitl_wait.approve_brief`, `meeting_prep.*`).
  - Click on any trace in the waterfall to view span latency breakdowns and custom attributes (`subagent.latency_ms`, `hitl.wait_duration_s`, `refinement.target`).
  - **Correlated Logs**: Selecting a span automatically displays its correlated structured JSON log entries in the bottom panel via the embedded trace identifier.

#### B. Google Cloud Logging (Logs Explorer)

View structured operational logs, intent/outcome pairs, and diagnostic events:

- **Console URL**:
  ```text
  https://console.cloud.google.com/logs/query?project=<PROJECT_ID>
  ```
- **Navigation**: In the Google Cloud Console, navigate to **Observability / Operations > Logging > Logs Explorer**.
- **Useful Filter Queries**:
  - **All Intent and Outcome Events**:
    ```text
    resource.type="aiplatform.googleapis.com/ReasoningEngine"
    jsonPayload.event_type=("intent" OR "outcome")
    ```
  - **Logs for a Specific Target Company**:
    ```text
    resource.type="aiplatform.googleapis.com/ReasoningEngine"
    jsonPayload.company="Stripe"
    ```
  - **Correlate Logs for a Specific Trace ID**:
    ```text
    logging.googleapis.com/trace="projects/<PROJECT_ID>/traces/<TRACE_ID_HEX>"
    ```

#### C. Local Inspection & Verification

- **Interactive Local Visualization (`adk web`)**:
  ```bash
  .venv/bin/adk web --port 8080 meeting_prep
  ```
  Navigate to `http://localhost:8080/` to inspect agent node graphs, events, and tool invocations.

- **Headless Acceptance Runner**:
  ```bash
  .venv/bin/python scripts/run_phase6.py
  ```
  Validates all 5 Phase 6 observability criteria in a single headless run (records 70+ structured JSON logs, verifies intent/outcome pairing, confirms 0 PII leaks, validates 50+ OpenTelemetry spans with custom subagent attributes, and renders the UI Provenance Panel).

---

## Phase 7: Orchestration & Logic Enhancements

Phase 7 introduces strategic multi-tier model routing, decoupled runtime security guardrails implemented as ADK Plugins, resilient loop orchestration, and automated user-delegated token management:

### 1. Strategic Multi-Tier Model Routing (`meeting_prep/models.py`)

Rather than scattering model literals across agent definitions, model assignments are centralized in a declarative routing table mapped to task shape and inference characteristics:

```python
# meeting_prep/models.py
MODEL_ROUTING = {
    "entity_disambiguator": FLASH_LITE,  # gemini-3.5-flash-lite
    "profile_researcher":   FLASH,       # gemini-3.7-flash
    "news_researcher":      FLASH,       # gemini-3.7-flash
    "focus_researcher":     FLASH,       # gemini-3.7-flash
    "delta_agent":          PRO,         # gemini-3.1-pro-preview
    "composer":             PRO,         # gemini-3.1-pro-preview
    "approval_gate":        FLASH_LITE,  # gemini-3.5-flash-lite
    "refinement_router":    FLASH,       # gemini-3.7-flash
    "publisher":            FLASH_LITE,  # gemini-3.5-flash-lite
}
```

- **Task Shape & Cost Rationale**:
  | Tier | Models | Agents | Rationale |
  |---|---|---|---|
  | **Flash-Lite** | `gemini-3.5-flash-lite` | `entity_disambiguator`, `approval_gate`, `publisher` | Fast, structured, deterministic tasks with fixed schemas and zero multi-document synthesis requirements. |
  | **Flash** | `gemini-3.7-flash` | `profile_researcher`, `news_researcher`, `focus_researcher`, `refinement_router` | High-volume parallel research and intent classification. Parallelizing across 3 researchers on Pro would dominate cost; Flash handles grounded extraction into fixed schemas with high concurrency and low latency. |
  | **Pro** | `gemini-3.1-pro-preview` | `delta_agent`, `composer` | Genuine synthesis across multi-source findings, delta reasoning against historical briefs, and strict citation preservation. |

- **Dynamic Pro Tier Escalation**: The composer initiates synthesis on Flash for speed. If `GroundingGuardPlugin` detects unsourced claims or citation gaps, it triggers a dynamic in-flight retry escalating to Pro with corrective directives, verifying against budget ceilings and emitting OpenTelemetry `call_llm` spans.
- **Routing Observability**: Each agent emits its active model tier as an OpenTelemetry span attribute (`subagent.model`), enabling live cost and latency tracking per tier.

### 2. Decoupled Runtime Guardrail Plugins (`meeting_prep/plugins/`)

Implemented as ADK `BasePlugin` extensions registered globally on `App(plugins=[...])`. Decoupling policies from agent prompt wording ensures security and governance rules are enforced as runtime invariants across all deployment modes:

```python
app = App(
    name="meeting_prep",
    root_agent=root_coordinator,
    plugins=[
        budget_plugin,          # 1. First: Enforces token & call ceilings before LLMs fire
        injection_guard_plugin, # 2. Scans researcher outputs & search grounding metadata
        redaction_plugin,       # 3. Sanitizes PII; returns None on untouched data
        publish_policy_plugin,  # 4. Enforces human approval, domain allowlist, & idempotency
        grounding_guard_plugin, # 5. Validates citations and drives Pro escalation
    ],
    ...
)
```

| Plugin | Lifecycle Hook | Target / Behavior |
|---|---|---|
| **`BudgetPlugin`** | `before_model`, `after_model` | Priority placement (first). Tracks cumulative model calls and tokens (`prompt_tokens`, `candidates_tokens`, `total_tokens`). Raises `BudgetExceededError` before invocation if `BUDGET_MAX_MODEL_CALLS` or `BUDGET_MAX_TOKENS` is exceeded. |
| **`InjectionGuardPlugin`** | `after_model`, `after_tool` | Scans researcher model completions and Gemini Google Search grounding metadata (`LlmResponse.grounding_metadata`) for prompt injection / instruction override patterns (e.g., `Ignore all previous instructions`). Neutralizes offending snippets to `[REDACTED_POTENTIAL_PROMPT_INJECTION]` and logs security audit events. |
| **`RedactionPlugin`** | `after_tool` | Sanitizes credentials, OAuth tokens, API keys, and PII across tool outputs. Returns `None` when content is untouched so ADK's `PluginManager` does not short-circuit downstream plugins in the callback chain. |
| **`GroundingGuardPlugin`** | `after_model` on `composer` | Zero-LLM claim-to-source validator. Uses structural line detection (ignoring markdown tables, blockquotes, code blocks, bullet labels, and sub-4-word headings) and domain-level URL matching against `research_*` outputs. Governs the 2-stage retry flow: escalates to Pro on attempt 1, appends unsourced claim warnings on attempt 2, and falls back to `_build_warning_draft` if budget or cache is exhausted. Evicts cached requests with a bounded 50-entry cap. |
| **`PublishPolicyPlugin`** | `before_tool` on `create_google_doc`, `share_doc` | Hard gate enforcing that `approval_decision.status == "approved"` in session state, validates recipient emails against `ALLOWED_RECIPIENT_DOMAINS`, and tracks `(brief_id, draft_version)` in an idempotency cache to prevent duplicate documents. |

### 3. Resilient Orchestration & Control Flow

- **Elimination of Inner Escalation Bugs**: Previous patterns that set `callback_context.actions.escalate = True` in inner agents prematurely broke enclosing `LoopAgent` / `refinement_loop` structures before reaching human review. Loop termination is strictly isolated to human review decisions (`approval_gate`).
- **ADK `State` Lifecycle Compatibility**: On Vertex AI Reasoning Engine, session state is an ADK `State` instance rather than a mutable Python dictionary, lacking `.pop()` and `__delitem__`. State resets (e.g. `grounding_attempt`, `grounding_correction`, `published_doc`) use sentinel writes (`state[key] = None`) to clear run-specific state cleanly without raising `AttributeError` or leaking counters across loop iterations.
- **Human-In-The-Loop Review Loop**: The outer refinement loop presents the synthesized brief draft to the reviewer, pauses via `approve_brief`, and branches cleanly:
  - **Revisions Requested**: `refinement_router` re-runs only the requested researcher, the composer updates the draft, and the gate pauses again.
  - **Approved**: Execution exits the loop and transitions to `publisher` for document creation.

### 4. Proactive User-Delegated Publishing & CLI Token Management

- **Proactive Token Refresh**: When delegating user OAuth credentials (`DRIVE_CREDENTIALS_FILE` / `.drive_user_token.json`) to remote Reasoning Engine instances, `load_delegated_drive_token()` proactively invokes `creds.refresh(Request())` whenever a `refresh_token` exists. This prevents expired 1-hour access tokens from being forwarded to long-running remote sessions.
- **Graceful Refresh Fallback**: In the event of a refresh error (e.g. transient network glitch), the CLI logs a warning via `logger` and gracefully falls back to the existing cached access token rather than failing with `NameError` or aborting execution.
- **Live Cloud Verification**: Verified live on deployed Vertex AI Reasoning Engine (`projects/edwinsoen-l200/locations/us-central1/reasoningEngines/1828942485049573376`), generating formatted Google Docs with live grounding and provenance reporting.

### 5. Verification Suite & Test Coverage

The full test suite consists of **71 unit tests** with 100% pass rate:

```bash
# Run model routing and guardrail plugin tests:
.venv/bin/pytest -v tests/test_model_routing.py tests/test_guardrail_plugins.py

# Run Drive tools and delegated token refresh fallback tests:
.venv/bin/pytest -v tests/test_drive_tools.py

# Run the complete test suite:
.venv/bin/pytest -v tests/
```

---

## License

Distributed under the [MIT License](LICENSE).
