# Meeting Prep Copilot — Detailed Design

---

## 1. Purpose and audience

Build a multi-agent research assistant that produces an approved, shareable company brief. The system is an onboarding exercise assessed on five criteria: Tool & Interface Design, Context & Memory, Orchestration & Logic, Observability & Tracing, Infrastructure & CI/CD.

This document is the source of truth for scope, architecture, and interfaces. It specifies **what** to build and **which primitives to build it from**. It does not prescribe prompt text or exact API signatures; those are the implementer's job, verified against the installed SDK versions.

---

## 2. Guiding principles

These are binding. Deviations need human decision.

### 2.1 ADK-first

Use ADK primitives before writing custom code. Almost every structural need in this system has a first-class ADK construct. Reaching for custom orchestration, custom state plumbing, or custom telemetry is a design failure, not a shortcut.

| Need | ADK primitive | Do not hand-roll |
|---|---|---|
| Step ordering | `SequentialAgent` | A Python function chaining `runner.run()` calls |
| Concurrency | `ParallelAgent` | `asyncio.gather` over agents |
| Iteration | `LoopAgent` with `escalate` termination | A `while` loop in the driver |
| Passing data between agents | `output_key` + session state | Return values threaded through Python |
| Delegating to a sub-agent as a callable | `AgentTool` | Manually invoking a nested runner |
| Custom tools | `FunctionTool` with typed signatures | Free-form JSON parsing |
| Lifecycle hooks | `before_agent` / `after_agent` / `before_tool` / `after_tool` callbacks | Wrapper functions around agent calls |
| Pausing for human input | `LongRunningFunctionTool` | A custom `AWAITING_INPUT` state machine |
| Session persistence | `VertexAiSessionService` | A database table |
| Long-term memory | `VertexAiMemoryBankService` | Firestore, a vector DB, a JSON blob |
| Binary/large payload storage | `GcsArtifactService` | Stuffing content into state |
| Tracing | ADK's built-in OpenTelemetry instrumentation | Manual span creation for standard agent/tool spans |
| Web search | ADK's built-in `google_search` grounding tool | A custom HTTP search client |

Custom code is expected in exactly four places: the memory-write callback, the LLM refinement router, custom span attributes, and the publish tools. Everything else should be configuration and composition.

### 2.2 GEAP-managed-first

Run on Gemini Enterprise Agent Platform (formerly Vertex AI Agent Builder). Prefer the platform's managed service for anything it offers.

- **Runtime**: Agent Runtime (Agent Engine Runtime). Not Cloud Run, not GKE.
- **Sessions**: Agent Engine Sessions, GA. Automatic when an `AdkApp` is deployed to Agent Runtime.
- **Long-term memory**: Agent Engine Memory Bank, GA. The default memory service on Agent Runtime, using the same Agent Engine instance.
- **Model**: Gemini via Vertex.
- **Grounding**: native Google Search grounding.
- **Observability**: Cloud Trace via `--trace_to_cloud`, plus the GEAP observability console and multi-agent topology view.
- **Identity**: Agent Identity (SPIFFE), with Agent Identity Auth Manager for user-delegated tool access. Never a service account. See §12A.

In-memory services are for local development only. If the deployed system uses `InMemorySessionService` or `InMemoryMemoryService`, the Context & Memory criterion is not met.

### 2.3 Demo-legibility

Every criterion needs a thing that can be shown on a screen in under a minute. Where a design choice trades implementation effort for demonstrability, favour demonstrability.

---

## 3. Problem statement

Before an external meeting, the user has no consolidated view of what the company is currently doing. The system produces an approved, shareable one-page brief from grounded public research, delivered as a Google Doc shared with a chosen recipient list, with preferences and prior briefs remembered across sessions.

---

## 4. User flow

1. User submits a company name, optionally overriding focus areas and recipients. `root_coordinator` preloads memory, so a returning user sees these pre-filled in the form. This is a single submission, not a gate.
2. If the company name is ambiguous, candidates are presented for selection. **HITL gate 1** (conditional).
3. Three researchers run in parallel. Progress streams per subagent.
4. `delta_agent` retrieves prior briefs for this company and computes what changed.
5. `composer` assembles the brief with inline source citations.
6. Brief presented. User approves or comments. **HITL gate 2.**
7. On comment: LLM router classifies the target section, that researcher alone reruns, composer reassembles. Loop, max 3 iterations.
8. On approval: Google Doc created and shared. Approval at gate 2 is the publish authorisation; there is no separate publish confirmation.
9. Post-approval callback writes the brief record to Memory Bank.

---

## 5. Assessment criteria mapping

Explicit so nothing is orphaned.

| Criterion | Where it is satisfied | Demo artifact |
|---|---|---|
| Tool & Interface Design | §11 tools with typed schemas, idempotent publish, graceful degradation; §12 REST + CLI over stock surfaces | Doc created once despite double approval |
| Context & Memory | §9 three layers, all managed; post-approval write callback; two retrieval modes | Second run on same company pre-fills and leads with delta |
| Orchestration & Logic | §7 sequential + parallel + loop + LLM routing | Trace showing 3 concurrent spans; targeted rerun faster than first pass |
| Observability & Tracing | §13 built-in OTel plus custom attributes | GEAP topology view; single trace with human-wait span |
| Infrastructure & CI/CD | §14 Terraform, pipeline, smoke test, versioned rollback | Green pipeline deploying to staging |

---

## 6. Architecture overview

```
Custom UI ──┐
REST API ───┼──> Agent Runtime (AdkApp) ──> root_coordinator
ADK web ────┘                                     │
                                                  ├─ Agent Engine Sessions   (session state)
                                                  ├─ Agent Engine Memory Bank (long-term)
                                                  ├─ GCS                      (artifacts)
                                                  ├─ Gemini + Search grounding
                                                  ├─ Google Drive API         (publish)
                                                  └─ Cloud Trace / Logging    (telemetry)
```

---

## 7. Agent design

### 7.1 Topology

```
root_coordinator                        LlmAgent (root)
└── brief_pipeline                      SequentialAgent
    ├── entity_disambiguator            LlmAgent
    ├── research_parallel               ParallelAgent
    │   ├── profile_researcher          LlmAgent + google_search
    │   ├── news_researcher             LlmAgent + google_search
    │   └── focus_researcher            LlmAgent + google_search
    ├── delta_agent                     LlmAgent + search_memory
    ├── refinement_loop                 LoopAgent (max_iterations=3)
    │   ├── composer                    LlmAgent
    │   ├── approval_gate               LlmAgent + approve_brief tool
    │   └── refinement_router           LlmAgent
    └── publisher                       LlmAgent + publish tools
```

`composer` sits inside the loop so that each refinement produces a fresh draft. On first pass the loop runs compose → gate; if approved, the gate escalates and the loop exits before the router runs.

### 7.2 Agent specifications

#### `root_coordinator` — LlmAgent

- **Role**: entry point. Preloads user memory to pre-fill the submission form, accepts company name plus optional preference overrides, hands off to the pipeline. Does not gate.
- **Tools**: `PreloadMemoryTool`
- **Sub-agents**: `brief_pipeline`
- **Writes state**: `user_preferences`, `company_input`
- **Notes**: keep this agent thin. It is a router and a memory-preloader, not a worker.

#### `entity_disambiguator` — LlmAgent

- **Role**: resolve the company name to a single unambiguous entity.
- **Tools**: `google_search`, `request_disambiguation` (long-running / gated)
- **Reads state**: `company_input`
- **Writes state (`output_key`)**: `resolved_entity` — `{name, domain, description, confidence}`
- **Logic**: if a single high-confidence match, pass through without a gate. If multiple candidates or low confidence, emit 2-3 candidates each with a one-line distinguisher and pause for selection.
- **Design point**: the gate is conditional on model confidence, not unconditional. This is a real HITL decision and worth calling out.

#### `profile_researcher` — LlmAgent

- **Role**: what the company does, business model, target segment, size, funding.
- **Tools**: `google_search`
- **Reads state**: `resolved_entity`
- **Writes state**: `research_profile`
- **Output contract**: bounded structured object, max 8 findings, each `{claim, source_url, source_date, confidence}`. Not prose.

#### `news_researcher` — LlmAgent

- **Role**: developments in the last 90 days.
- **Tools**: `google_search`
- **Writes state**: `research_news`
- **Output contract**: same shape. Every finding must carry a date; undated findings are dropped.

#### `focus_researcher` — LlmAgent

- **Role**: the user's stated focus areas.
- **Tools**: `google_search`
- **Reads state**: `resolved_entity`, `user_preferences.focus_areas`
- **Writes state**: `research_focus`
- **Degradation**: if no focus areas are set, return an empty result rather than inventing a topic.

#### `delta_agent` — LlmAgent

- **Role**: compare current findings against prior briefs for this company.
- **Tools**: `search_memory` (explicit, company-scoped query)
- **Reads state**: `resolved_entity`, `research_*`
- **Writes state**: `delta_summary`
- **Degradation**: on first-ever brief for a company, write an explicit "no prior brief" marker. The composer must render this, not silently omit the section, so the demo can show both states.

#### `composer` — LlmAgent

- **Role**: assemble the brief.
- **Reads state**: `research_profile`, `research_news`, `research_focus`, `delta_summary`, `refinement_directive` (if present)
- **Writes state**: `brief_draft`
- **Writes artifact**: `brief_draft_v{n}.md`
- **Output contract**: markdown, fixed section order, every claim carrying an inline source link. Never sees raw search output, only the bounded finding objects.
- **On refinement**: reads `refinement_directive` and the refreshed research key; leaves all other sections byte-identical to the previous draft.

#### `approval_gate` — LlmAgent

- **Role**: present the draft, capture the decision.
- **Tools**: `approve_brief`
- **Writes state**: `approval_decision` — `{status: approved|revise, comment}`
- **Termination**: on `approved`, set `escalate` to exit the loop.

#### `refinement_router` — LlmAgent

- **Role**: classify a free-text comment into a target section and a research directive.
- **Reads state**: `approval_decision.comment`, section list
- **Writes state**: `refinement_target` (one of the research keys, or `all`), `refinement_directive`
- **Why LLM, not string match**: comments frequently do not name a section — "the funding bit feels out of date" must route to `profile_researcher`. A fixed match cannot do this.
- **Fallback**: low classification confidence routes to `all`, i.e. full recompose.
- **Instrumentation**: emit the classification and its confidence as span attributes. Routing decisions must be visible in the trace.
- **Re-invocation**: the router triggers only the targeted researcher, via `AgentTool` or by conditionally skipping non-targets. Prior sections are read from state, not re-researched.

#### `publisher` — LlmAgent

- **Role**: create and share the Doc.
- **Tools**: `create_google_doc`, `share_doc`
- **Reads state**: `brief_draft`, `user_preferences.recipients`
- **Writes state**: `published_doc_url`
- **Callback**: `after_agent` writes the brief record to Memory Bank. See §9.4.

---

## 8. Session state schema

All keys live in ADK session state. Namespacing follows ADK's prefix conventions: `user:` for cross-session user scope, unprefixed for session scope, `temp:` for values that must not persist.

| Key | Scope | Written by | Shape |
|---|---|---|---|
| `company_input` | session | root_coordinator | string |
| `user_preferences` | `user:` | root_coordinator | `{focus_areas: [str], recipients: [email]}` |
| `resolved_entity` | session | entity_disambiguator | `{name, domain, description, confidence}` |
| `research_profile` | session | profile_researcher | `{findings: [Finding]}` |
| `research_news` | session | news_researcher | `{findings: [Finding]}` |
| `research_focus` | session | focus_researcher | `{findings: [Finding]}` |
| `delta_summary` | session | delta_agent | `{has_prior: bool, changes: [str]}` |
| `brief_draft` | session | composer | markdown string |
| `draft_version` | session | composer | int |
| `approval_decision` | session | approval_gate | `{status, comment}` |
| `refinement_target` | session | refinement_router | enum |
| `refinement_directive` | session | refinement_router | string |
| `published_doc_url` | session | publisher | url |

`Finding` = `{claim: str, source_url: str, source_date: date, confidence: float}`.

**Parallel-write constraint**: the three researchers execute concurrently against shared session state. Their `output_key` values must be distinct. Do not have them append to a common list.

---

## 9. Memory design

Three layers, all managed. This section carries the most assessment weight; implement it carefully.

### 9.1 Layer 1 — Session state (Agent Engine Sessions)

`VertexAiSessionService`, applied automatically when the `AdkApp` runs on Agent Runtime. `InMemorySessionService` locally. No code difference between environments beyond service construction.

Refinement reads prior sections from session state rather than re-researching them. This is what makes the second pass visibly faster, and it is the cheapest demonstration of state reuse available.

### 9.2 Layer 2 — Artifacts (GcsArtifactService)

`InMemoryArtifactService` locally, `GcsArtifactService` deployed.

Stored:
- `raw_search_{agent}_{n}.json` — raw grounding results, kept out of the composer's context
- `brief_draft_v{n}.md` — every draft version

Purpose is twofold: context compaction, and the ability to diff v1 against v2 to prove the targeted rerun only touched one section. That diff is a strong demo moment; produce it.

### 9.3 Layer 3 — Long-term memory (Memory Bank)

`VertexAiMemoryBankService`, the default memory service on Agent Runtime, pointing at the same Agent Engine instance.

**Two custom topics. Not three.** A third dilutes extraction quality on the two that drive the demo.

| Topic | Captures | TTL | Extraction guidance |
|---|---|---|---|
| `briefing_preferences` | Focus areas, recipient list, format leanings | None | Few-shots must teach the standing-vs-one-off distinction. "Always include funding history" is a preference; "add funding for this one" is not. |
| `company_brief_history` | Company, date, 3-5 headline facts, doc link | 90 days | Structured fact records, not narrative summary |

Other Memory Bank features in use: scope-based isolation by `user_id`, TTL-driven expiry, similarity-search retrieval.

### 9.4 Memory write strategy

**`add_memory` for brief records, extraction for preferences.**

`add_session_to_memory` is not orchestrated by the ADK runner; automating it requires an explicit callback. This is a design decision, not boilerplate, and should be presented as one.

The callback:
- Fires `after_agent` on `publisher`
- Fires **only on approval**, so rejected drafts never pollute long-term memory
- Calls `add_session_to_memory` to let extraction handle `briefing_preferences`
- Calls `add_memory` directly with a structured brief record for `company_brief_history`

Direct ingestion for the brief record is deliberate: the delta compares structured facts, and LLM extraction paraphrases differently on each run, which makes the second-run demo unreliable.

### 9.5 Memory read strategy

Two modes, chosen per access pattern:

- `PreloadMemoryTool` on `root_coordinator` — fires at turn start, needs no query, pre-fills preferences. Roughly one line.
- Explicit `search_memory` in `delta_agent` — scoped by company name. An unscoped similarity search would surface preferences and other companies' facts alongside the target.

---

## 10. HITL design

### 10.1 Gates

Two gates. Preference confirmation and a separate publish confirmation were both removed: preferences are collected in the initial submission alongside the company name, and approval at gate 2 already authorises publication. Neither added a decision the user was not already making.

| # | Gate | Trigger | Rationale |
|---|---|---|---|
| 1 | Entity disambiguation | Conditional on model confidence | Agent is genuinely uncertain; one click resolves |
| 2 | Approve / refine | After each composition | Core loop; drives the LoopAgent, and authorises publish |

**Demo note**: gate 1 is conditional, so a clean company name produces no interruption before the draft. Pick a genuinely ambiguous name for the demo, and have an unambiguous one ready to show the pass-through path.

### 10.2 Mechanism: LongRunningFunctionTool

Both gates are `LongRunningFunctionTool`. This is the ADK-recommended HITL primitive and replaces any hand-rolled pause state.

**How the pause works.** The tool returns an initial result (`{status: "pending", gate: ..., payload: ...}`). The runner pauses the agent run and returns control to the client. The invocation ends; no connection is held during the human wait.

**How the resume works.** The client constructs a `types.FunctionResponse` carrying the **same `id` and `name` as the original `FunctionCall`**, wraps it in a `types.Content(role="user", parts=[...])`, and sends it as `new_message` on a follow-up `run_async`. ADK feeds it to the LLM and the agent continues.

**Detection.** During leg 1, the event whose `long_running_tool_ids` contains the call id marks the pause. Capture the entire `FunctionCall` object, not just the gate id.

**Recovering the pending call — do not add a store.** Read the session's events back from Agent Engine Sessions and find the last event with `long_running_tool_ids` set. This is ADK-first, needs no extra table, and survives a server restart.

### 10.3 Transport: two blocking legs, no polling

The pause ends the invocation, so there is no long-lived connection to manage and nothing to poll. Polling exists to let a third-party client discover state changes; the CLI is not a third party, it is the caller.

**CLI.** `runner.run_async()` is an async generator of events. The CLI iterates it, printing per-subagent progress as events arrive, and the generator ends when the agent pauses at the gate. Against the deployed runtime this is `stream_query`, same shape. No SSE, no polling, no status endpoint.

```
leg 1:  iterate events -> print progress -> generator ends at gate
        capture the FunctionCall (id + name) from the non-partial event
pause:  read decision from stdin. No connection held.
leg 2:  send FunctionResponse as new_message -> iterate to completion
```

**REST.** Synchronous, two routes. No status route, no state machine.

```
POST /briefs                  -> runs leg 1, returns at the gate
                                 {brief_id, gate, payload}
POST /briefs/{id}/decision    -> runs leg 2, returns {doc_url}
```

**Timeout caveat.** Leg 1 runs three parallel grounded researchers and may take 30-60 seconds. Acceptable for a CLI and for most HTTP timeouts. If it approaches the runtime's request limit, the fix is an async job plus a status route. Do not build that speculatively.

### 10.4 Known hazards

Verify each against the installed ADK version before building.

| Hazard | Detail | Mitigation |
|---|---|---|
| Partial-event ID mismatch | Open bug: the `functionCall.id` on a partial event differs from the id in the stored non-partial event. Resuming with the partial id fails with `Function call event not found for function response id`. Applies to CLI event iteration, not just SSE. | Capture the id only from non-partial events |
| Resume feature invocation binding | If ADK's Resume feature is configured, the long-running function response must also carry the `invocation_id` of the originating invocation, or a new invocation starts instead of resuming | Persist `invocation_id` alongside the call, or leave the Resume feature off in v1 |
| Two confirmation paths | `tool_context.request_confirmation()` targets a synthetic `adk_request_confirmation` call id, not the tool's own id | Pick one pattern. This design uses plain `LongRunningFunctionTool` throughout. |
| Consecutive gates | Multiple sequential long-running tools require the client to handle each pause in turn | Gate 1 is conditional and gate 2 is in a loop, so back-to-back pauses are possible. Test the path where both fire. |
| Agent Runtime passthrough | Whether a `Content` containing a `function_response` part can be sent as `new_message` through the deployed Agent Runtime endpoint | Verify early in phase 4; it gates the whole HITL design |

### 10.5 Idempotency

Approval may be submitted twice — double-click, retry, duplicate poll. `create_google_doc` must be idempotent on `(brief_id, draft_version)`. A repeated approval returns the existing doc URL and creates nothing. This is the single clearest Tool Design demo available; build it and show it.

---

## 11. Tool specifications

All custom tools are `FunctionTool` with typed signatures and docstrings the model can act on. No free-form JSON parsing.

| Tool | Signature | Notes |
|---|---|---|
| `google_search` | ADK built-in | Native grounding. **Verify**: built-in tools have combination constraints in some ADK versions; if a researcher needs another tool alongside it, wrap via `AgentTool`. |
| `PreloadMemoryTool` | ADK built-in | Preferences preload on root |
| `search_memory` | ADK built-in | Company-scoped retrieval in `delta_agent` |
| `request_disambiguation` | `(candidates: list[Entity]) -> dict` | `LongRunningFunctionTool`. Returns pending; resolved via FunctionResponse. |
| `approve_brief` | `(draft: str) -> dict` | `LongRunningFunctionTool`. Returns pending; resolved to approve or revise+comment. |
| `create_google_doc` | `(title: str, markdown: str, brief_id: str, version: int) -> DocRef` | Drive create with mimeType conversion. Idempotent on `(brief_id, version)`. |
| `share_doc` | `(doc_id: str, emails: list[str]) -> ShareResult` | One permissions call per recipient. Partial failure returns per-recipient status; does not raise. |

**Cross-cutting tool requirements:**
- Bounded retries with backoff on transient Drive/API errors
- Graceful degradation: a tool failure returns a structured error the agent can reason about, never an unhandled exception that kills the run
- Every tool call is traced with arguments redacted where they contain emails

---

## 12. Interfaces

**No web application is built.** GEAP and ADK supply two usable surfaces out of the box, and the one thing they cannot do is driven by a CLI. A browser UI would add a day and demonstrate frontend work rather than agent design.

### 12.1 Stock surfaces — free, use them

| Surface | Purpose | Notes |
|---|---|---|
| `adk web` | Local development, Trace View | Primary dev loop through phases 1-3 |
| `agents-cli playground` | Local alternative with memory inspection | Useful in phase 5 for inspecting Memory Bank state |
| GEAP console Playground | Verify the deployed agent runs | Playground tab on the deployment's Metrics page |
| GEAP observability console | Traces, per-agent metrics, multi-agent topology | This is the Observability demo; see §13 |

**Why these are not sufficient alone.** The console Playground is a chat pane for confirming an agent responds. Resuming a paused `LongRunningFunctionTool` requires capturing a `FunctionCall` id and posting back a matching `FunctionResponse` (§10.2), which no stock chat surface exposes. The HITL loop needs a client that can do this.

**Agent Studio is out of scope.** It is a low-code visual designer. This assignment is code-first ADK with CI/CD, and a visually-designed agent cannot be version-controlled or pipeline-deployed in the way the Infrastructure criterion expects.

### 12.2 Built surfaces — two, both small

**REST endpoint** (§10.3). The programmatic interface. Three routes. This is what CI smoke-tests and what proves the agent graph is not entangled with any UI.

**CLI client.** Runs leg 1, renders the draft to the terminal, reads the decision from stdin, runs leg 2. Roughly eighty lines, no build step, no deploy target. It drives the full HITL loop including refinement iterations, and it is honest about what the system does.

The CLI is also the fastest path to a repeatable demo: a shell script can run the whole flow twice against the same company to show the memory delta.

### 12.3 On adding a hosted web UI

**The CLI is not the optional part.** Nothing else can drive `LongRunningFunctionTool` pause and resume: `adk web` cannot, and neither can the console Playground. From phase 2 onward a client that captures a `FunctionCall` and posts back a `FunctionResponse` is required to develop and test the HITL loop at all. The CLI is on the critical path regardless of the demo format.

A web UI is therefore purely additive. What it costs:

- Hosting plus a second deploy target in the pipeline
- CORS
- Progress display, which pushes back toward SSE and reintroduces the partial-event id mismatch (§10.4)
- A frontend build in CI

Roughly a day, none of it assessed.

**Do not ship it unauthenticated.** The endpoint creates Google Docs and shares them to arbitrary addresses under the service account's identity. An open URL means anyone can write into the project's Drive and mail links to strangers. On an assignment about an enterprise agent platform this is the wrong signal regardless of whether it is abused. If hosted, put IAP in front of it or restrict access to the org.

**Decision rule.** If the author drives the demo live, the CLI is sufficient and strictly cheaper. If assessors must self-serve at their own convenience, a hosted UI earns its cost — and then it is worth doing with auth. Defer this to phase 8, when the remaining budget is known.

## 12A. Identity and access

**This is a requirement, not a design option.** Agent Identity with SPIFFE-formatted identities is course material and must be used as taught. There is no fallback path, and the coding agent must not substitute an alternative under time pressure. If the identity configuration is not working, fix it; do not route around it.

The anti-pattern this section prevents: an application service account performing Drive writes on everyone's behalf.

### 12A.1 Agent Identity for the agent's own GCP access

Deploy to Agent Runtime **with the identity flag configured**. If it is not set, Agent Runtime silently falls back to service accounts for backward compatibility. A deployment that appears to work but is running on a service account has not met the requirement, so verify the identity after deploy rather than assuming.

Properties that make this the correct primitive:
- Per-agent SPIFFE-formatted identity, supported directly in IAM, replacing shared service accounts
- Not shared across workloads, cannot be impersonated, no long-lived key generation
- Credentials bound by a default Context-Aware Access policy enforcing mTLS, so tokens are certificate-bound and un-replayable outside the intended runtime
- Audit logs attribute actions to the agent

Grants:

| Role | For |
|---|---|
| `roles/aiplatform.agentContextEditor` | Default on creation |
| `roles/aiplatform.agentDefaultAccess` | Default on creation |
| `roles/aiplatform.expressUser` | Inference, Sessions, Memory Bank |
| Storage object roles on the artifact bucket | `GcsArtifactService` |

**Gotcha**: agent identities cannot be granted legacy Cloud Storage bucket roles (e.g. `storage.legacyBucketReader`). Use modern object-level roles.

### 12A.2 Drive access — user-delegated through Agent Identity Auth Manager

The Doc is created **as the requesting user, in their Drive**. Not deposited into a bot account and shared outward.

Agent Identity supports this through **Agent Identity Auth Manager for user-delegated tool access**, which provides an audit trail showing both the agent's and the user's identity. With Agent Gateway in the path, end-user credentials are encrypted by the auth manager and decrypted at the gateway, so the agent never handles the raw credential.

Consequences worth stating:
- No ambient service-account Drive access exists, so §12.3's hosted-endpoint concern is structurally resolved rather than mitigated
- The Doc's owner is the user, so it survives independently of the agent's lifecycle
- Audit logs answer "who caused this Doc to exist" with both identities, which is the point of the delegation model

### 12A.3 Explicitly rejected

Listed so that no implementation shortcut is mistaken for a decision:

- Custom or default service account as the agent's runtime identity
- Domain-wide delegation
- A shared bot account owning generated Docs
- Static service account keys anywhere, including Secret Manager
- Bypassing delegation by having the CLI perform Drive calls with the developer's own credentials

### 12A.4 Verification

Identity is verified, not assumed. After deploy, confirm the agent's identity in the Agent Runtime console or via the API, and confirm an audit log entry for a Doc creation shows both the agent identity and the delegated user. This verification belongs in the CI smoke test (§14.3).

Follow the course material for exact configuration and Terraform representation. This document records the requirement; it does not restate the recipe.

## 13. Observability

ADK 1.17+ emits OpenTelemetry natively following the OTel GenAI semantic conventions, and deploying with `--trace_to_cloud` exports to Cloud Trace. Most of this is configuration; spend the saved budget on the custom attributes below.

### 13.1 Free

- Cloud Trace ingestion via `--trace_to_cloud`
- ADK web UI Trace View for local debugging
- GEAP observability console: per-agent dashboards, trace explorer, multi-agent topology view showing live relationships and traffic

### 13.2 Custom — this is what differentiates the submission

- **Span attributes per subagent**: token counts in and out, model, latency, tool calls made
- **A span around each HITL gate**, so human wait time is measured separately from agent time
- **Refinement counter**: iterations per brief, and which section each targeted
- **Router classification** emitted as a span attribute, so routing decisions are auditable
- **Grounding metadata surfaced into the brief**: every claim links to its source
- **UI provenance panel**: which agent produced each section, how long it took, what it cost

Implement via ADK callbacks, not by wrapping agents in custom functions.

### 13.3 Demo

Open the topology view, then one trace. Show the three researchers as concurrent spans, the human approval gap between compose and publish, and the second-pass trace with only one researcher span.

---

## 14. Infrastructure and CI/CD

### 14.1 Platform

- **Platform**: Gemini Enterprise Agent Platform
- **Runtime**: Agent Runtime, deployed via `adk deploy agent_engine --trace_to_cloud`
- **State**: Agent Engine Sessions + Memory Bank (managed, GA), GCS bucket for artifacts
- **Identity**: Agent Identity, configured at deploy. Not a custom service account. See §12A.
- **Secrets**: Secret Manager for third-party credentials only. No service account keys anywhere, including Secret Manager.
- **Billing**: enabled. Sessions and Memory Bank have been metered since January 2026; this is accounted for. Express Mode is not used, and would block Agent Runtime deployment.

### 14.2 IaC

Terraform for: the Agent Engine instance, Memory Bank configuration (topics, TTL), the GCS artifact bucket, the agent identity, and its IAM bindings (§12A.1).

Note that the Reasoning Engine Service Agent holds Memory Bank read/write permissions by default; under Agent Identity, grant `roles/aiplatform.expressUser` to the agent identity explicitly rather than relying on that fallback. Local development runs under developer ADC and needs its own grants.

### 14.3 Pipeline

1. Lint and type check
2. Unit tests on tools with mocked Drive and search clients
3. Contract test: full graph against recorded fixtures, search stubbed. Asserts graph structure, state keys written, and tool contracts — not answer quality.
4. Build and deploy to staging Agent Runtime
5. Post-deploy smoke test against the REST endpoint with a known company, including verification that the deployed agent runs under its agent identity (§12A.4)
6. Manual approval, then promote to prod

### 14.4 Rollback

Deploy by version. Keep the prior version addressable.

---

## 15. Suggested repository layout

```
meeting_prep/
  agents/
    root.py                 root_coordinator, brief_pipeline
    disambiguator.py
    researchers.py          profile, news, focus + research_parallel
    delta.py
    composer.py
    approval.py             approval_gate, refinement_router, refinement_loop
    publisher.py
  tools/
    drive.py                create_google_doc, share_doc
    hitl.py                 request_disambiguation, approve_brief
  callbacks/
    memory.py               post-approval memory write
    telemetry.py            custom span attributes
  schemas.py                Finding, Entity, Decision, DocRef
  app.py                    AdkApp assembly, service wiring
  server.py                 REST interface
  cli.py                    CLI client: leg 1, prompt, leg 2
infra/                      Terraform
tests/
  fixtures/
  test_tools.py
  test_graph_contract.py
.github/workflows/          or cloudbuild.yaml
```

---

## 16. Implementation phases

Ordered so that each phase is independently demonstrable.

**Phase 1 — Graph, local.** All agents, in-memory services, `adk web`. Acceptance: a brief is produced end to end for a real company, and the parallel branch is visible in the local Trace View.

**Phase 2 — HITL loop.** Both gates as `LongRunningFunctionTool`, pause/resume via FunctionResponse, LLM router, targeted rerun. Acceptance: a run pauses at the approval gate with no connection held, resumes correctly from a follow-up call, a comment naming no section still routes correctly, and the second pass reruns exactly one researcher.

**Phase 3 — Publish.** Drive tools with idempotency. Acceptance: double approval yields one Doc.

**Phase 4 — Deploy to Agent Runtime with Agent Identity.** Swap to managed services, configure the identity flag, wire user-delegated Drive access (§12A). Acceptance: brief produced through the deployed REST endpoint, session persisted in Agent Engine Sessions, the deployed agent verified to be running under its agent identity rather than a service account, and a Doc created in the requesting user's Drive with both identities present in the audit log.

**Phase 5 — Memory.** Topics, write callback, both read modes. Acceptance: second run on the same company pre-fills preferences and leads with a delta section.

**Phase 6 — Observability.** `--trace_to_cloud`, custom attributes, provenance panel. Acceptance: a Cloud Trace showing concurrent researchers, human wait time, and the router classification.

**Phase 7 — CI/CD.** Terraform, pipeline, smoke test. Acceptance: a green pipeline deploying to staging.

**Phase 8 — Optional web UI.** Additive only; the CLI already drives the full flow and Cloud Trace already shows provenance. Build only if phases 1-7 are green and assessors need self-serve access, and put auth in front of it (§12.3).

Memory sits at phase 5 rather than earlier because the write callback hangs off the publisher, which does not exist until phase 3. Do not reorder it earlier.

---

## 17. Risks and verification points

The coding agent must verify these against installed SDK versions rather than trusting this document.

| Risk | Verification |
|---|---|
| Built-in tool combination constraints | Whether `google_search` can coexist with other tools on one agent in the installed ADK version. If not, wrap via `AgentTool`. |
| `LongRunningFunctionTool` on Agent Runtime | Whether a `function_response` part can be sent as `new_message` through the deployed endpoint. Gates the entire HITL design; verify in phase 4. See §10.4. |
| Memory Bank custom topic configuration | Exact schema for topic definitions and few-shot examples |
| `add_memory` direct ingestion | Signature and whether it is exposed through `VertexAiMemoryBankService` or requires the Agent Engine SDK directly |
| Conditional skip inside `ParallelAgent` | Whether targeted rerun is cleanest as a conditional inside the parallel branch or as `AgentTool` invocation from the router |
| Artifact service on Agent Runtime | Whether `GcsArtifactService` needs explicit wiring or is provided |
| Agent Identity deploy flag | Exact flag and Terraform representation for enabling agent identity on Agent Runtime. Follow course material. Not optional; see §12A. |

Non-technical risk: **scope**. Realistic estimate is ~7 hours for phases 1-7 with a coding agent. The original 3-4 hour framing covers phases 1-3 only. Cut order if over budget: the optional web UI, then `focus_researcher` folded into `profile_researcher`, then the pipeline's manual approval stage.

---

## 18. Non-goals

Explicitly out of scope. Listed so the boundary reads as a decision, not an omission.

**Calendar-driven meeting resolution.** Free-text entry resolving to an event and company via attendee domains. Genuine agent work, but it adds a tool integration and an ambiguity class without touching any criterion the current design misses. If revisited, it must be agent-driven; a UI meeting picker adds nothing and will not be built.

**Internal context from Gmail or Drive.** Prior thread history would materially improve the brief, but needs a seeded demo account with plausible correspondence and multiplies the failure surface.

**Multi-company and comparative briefs.** One company per run.

**CRM integration and pipeline context.**

**Brief content quality tuning.** The composer prompt gets one pass.

**Email delivery.** Google Doc only. Email is marginally simpler but leaves nothing to inspect.

---

## 19. Decisions log

| Decision | Resolution | Rationale |
|---|---|---|
| Topic choice | Meeting prep / company brief | Easy to vibe-check, uses grounding, demo-friendly |
| Delivery artifact | Google Doc + share | Durable and inspectable; email leaves nothing behind |
| Calendar picker | Cut | UI-populated dropdown is a form field, not an agent capability |
| Long-term memory backend | Agent Engine Memory Bank | GA, default on Agent Runtime; Firestore would be a regression |
| Brief record write path | `add_memory` direct ingestion | Extraction paraphrases; the delta needs stable structured facts |
| Memory write trigger | `after_agent` on publisher, approval only | Rejected drafts must not pollute memory |
| Memory read | Preload for preferences, scoped search for delta | Two access patterns, two modes |
| Refinement router | LLM classifier | Comments frequently do not name a section |
| HITL mechanism | `LongRunningFunctionTool` | ADK-recommended primitive; replaces hand-rolled pause state |
| HITL transport | Two blocking legs. No polling, no SSE, no status endpoint. | The pause ends the invocation, and the CLI is the caller rather than a third party observing state |
| Pending-call recovery | Read back from Agent Engine Sessions events | Avoids a side store; ADK-first |
| Custom memory topics | Two | A third dilutes extraction on the two that matter |
| Runtime | Agent Runtime on GEAP | Assignment requirement; also gives Sessions and Memory Bank by default |
| Interfaces | Stock surfaces + REST + CLI. Web app deferred to phase 8. | The CLI is required to develop HITL at all, so it is not optional. A web UI adds hosting, CORS, SSE and a CI target for no assessed gain. |
| Agent Studio | Not used | Low-code visual design cannot be version-controlled or pipeline-deployed |
| Agent's GCP identity | Agent Identity (SPIFFE). Required, not optional. | Course requirement. Per-agent identity, mTLS-bound tokens, no long-lived keys, IAM-native |
| Drive access | User-delegated via Agent Identity Auth Manager | Doc owned by the user; audit trail shows both identities; no ambient service-account Drive access |
| Service accounts, domain-wide delegation, bot-owned Docs | Rejected outright | Not fallbacks. See §12A.3 |
