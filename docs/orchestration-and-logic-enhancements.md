# Orchestration and Logic Enhancements

New enhancements on top of the HLD.

---

## 1. Strategic model routing

### 1.1 Change

Replace the single model constant with a routing table in one module. Not literals scattered across agent definitions; a reviewable mapping.

```python
# meeting_prep/models.py
MODEL_ROUTING = {
    "entity_disambiguator": FLASH_LITE,
    "profile_researcher":   FLASH,
    "news_researcher":      FLASH,
    "focus_researcher":     FLASH,
    "delta_agent":          PRO,
    "composer":             PRO,
    "approval_gate":        FLASH_LITE,
    "refinement_router":    FLASH,
    "publisher":            FLASH_LITE,
}
```

### 1.2 Rationale to state in the writeup

Routing is justified by task shape, not by tiering for its own sake.

| Tier | Agents | Why |
|---|---|---|
| Flash-Lite | disambiguator, approval gate, publisher | Structured, near-deterministic work. Fixed output shapes, no synthesis. |
| Flash | three researchers, refinement router | High volume — the parallel block multiplies cost by three. Grounded extraction into a fixed schema is well within Flash. |
| Pro | composer, delta agent | The only two agents doing genuine synthesis. The composer must preserve citation fidelity across four inputs; the delta agent must reason about what materially changed. Errors here are visible in the product. |

The parallel block is the cost argument: three concurrent researchers on Pro would dominate spend for work that is extraction, not reasoning.

### 1.3 Escalation path

One conditional escalation, tied to Part 2: if the grounding self-check fails, the composer retries once on Pro with a corrective instruction. This is the only dynamic routing in the system and it is worth having, because static routing alone reads as configuration rather than strategy.

### 1.4 Observability

Emit the resolved model as a span attribute per agent (already specified in design §13.2). Report cost per tier in the writeup. Routing that cannot be observed cannot be defended.

---

## 2. Guardrails as ADK Plugins

### 2.1 Use Plugins, not callbacks

ADK documentation explicitly recommends Plugins over Callbacks for security guardrails and policies, citing better modularity. A Plugin extends `BasePlugin`, implements lifecycle callback methods, and is registered on the Runner, where it applies globally across every agent, model call, and tool call.

This matters for the writeup: choosing Plugins over per-agent callbacks is the documented pattern, and it means the policy layer is separable from the agent graph.

### 2.2 Three plugins

#### `PublishPolicyPlugin` — `before_tool`

Hard policy gate on `create_google_doc` and `share_doc`. Denies the call unless:
- `approval_decision.status == "approved"` in session state
- Every recipient is in an allowed domain list
- An idempotency key `(brief_id, draft_version)` is present

Returning a value from `before_tool` short-circuits execution, so denial is a structured error the agent can reason about rather than an exception.

This is the highest-value guardrail: it makes "nothing is published without approval" a runtime-enforced invariant rather than a property of prompt wording.

#### `GroundingGuardPlugin` — `after_model` on the composer

The self-evaluation policy. Deterministic, no LLM call:
- Extract every claim line from the draft
- Assert each carries a source URL
- Assert each URL appears in the `research_*` findings in session state

On failure, reject the draft and trigger one regeneration with a corrective instruction, escalating to Pro (§1.3). Second failure surfaces to the user with the unsourced claims listed rather than silently publishing them.

Deterministic rather than LLM-judged is a deliberate choice: it is free, fast, and cannot itself hallucinate. Note it as such.

#### `BudgetPlugin` — `before_model` / `after_model`

Accumulates token usage and model-call count per invocation. Aborts with a clear message past a configured ceiling. Also the natural home for the refinement-iteration counter from design §13.2.

Cheap to build and it demonstrates the monitoring-and-metrics use of Plugins alongside the policy use.

### 2.3 `InjectionGuardPlugin`

`InjectionGuardPlugin` — `after_tool` on `google_search`. Retrieved web content is untrusted input reaching the model, so a scan for instruction-override patterns in search results is a genuine threat model for this system rather than a checkbox. Off-the-shelf options exist (`adk-atr-guardrail`, Cisco AI Defense) if writing one is not worth the time.

---

## 3. Integration

### 3.1 Registration

Plugins register on the Runner. Confirm they pass through `AdkApp` when deployed to Agent Runtime — this is a verification point, not an assumption, and it is the one thing that could invalidate the approach.
