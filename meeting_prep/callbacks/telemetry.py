"""Structured JSON logging, explicit distributed tracing, and provenance telemetry.

Source of truth: docs/hld.md §13, §16
- Structured JSON logging with Google Cloud Logging format (severity, message, trace, spanId).
- Trace-log correlation via OpenTelemetry active span injection.
- Dual-layer Intent vs. Outcome logging across agents and tools.
- Explicit HITL gate wait time spans across Leg 1 pause and Leg 2 resume.
- Custom span attributes per subagent (model, latency, tokens, tool calls).
- Refinement routing span attributes (target, confidence, directive, iteration).
- Terminal UI provenance panel and session state provenance tracking.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from meeting_prep.config import PROJECT_ID, MODEL_NAME
from meeting_prep.telemetry.redaction import RedactionFilter, redact_data

logger = logging.getLogger(__name__)

# Tracer for Meeting Prep Copilot
_TRACER = trace.get_tracer("meeting_prep.telemetry", "1.0.0")


# -----------------------------------------------------------------------------
# Structured JSON Formatter with Cloud Trace Correlation
# -----------------------------------------------------------------------------

class JsonTraceFormatter(logging.Formatter):
    """Formats log records as structured JSON with Cloud Logging / OpenTelemetry correlation."""

    def __init__(self, project_id: Optional[str] = None) -> None:
        super().__init__()
        self.project_id = project_id or PROJECT_ID

    def format(self, record: logging.LogRecord) -> str:
        # 1. Base log record structure
        timestamp_str = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).isoformat()

        formatted_message = record.getMessage()

        entry: dict[str, Any] = {
            "timestamp": timestamp_str,
            "severity": record.levelname,
            "message": formatted_message,
            "logger": record.name,
            "component": getattr(record, "component", record.name),
            "event_type": getattr(record, "event_type", "log"),
        }

        # 2. Extract active OpenTelemetry trace context for 1:1 log-to-trace correlation
        span = trace.get_current_span()
        if span:
            ctx = span.get_span_context()
            if ctx and ctx.is_valid:
                trace_id_hex = f"{ctx.trace_id:032x}"
                span_id_hex = f"{ctx.span_id:016x}"
                trace_path = (
                    f"projects/{self.project_id}/traces/{trace_id_hex}"
                    if self.project_id
                    else trace_id_hex
                )
                entry["logging.googleapis.com/trace"] = trace_path
                entry["logging.googleapis.com/spanId"] = span_id_hex
                entry["logging.googleapis.com/trace_sampled"] = ctx.trace_flags.sampled

        # 3. Attach domain-specific fields
        for field in ("intent", "outcome", "outcome_status", "duration_ms", "brief_id", "version", "company"):
            val = getattr(record, field, None)
            if val is not None:
                entry[field] = val

        # 4. Include extra payload fields if attached
        payload = getattr(record, "payload", None)
        if payload and isinstance(payload, dict):
            entry.update(payload)

        # 5. Include exception information if present
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        # Sanitize entire output dictionary before stringifying
        sanitized = redact_data(entry)
        return json.dumps(sanitized, ensure_ascii=False)


from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor


_TELEMETRY_CONFIGURED = False


def configure_telemetry(
    project_id: Optional[str] = None,
    export_to_cloud: Optional[bool] = None,
) -> TracerProvider:
    """Ensure an OpenTelemetry SDK TracerProvider is registered so spans and trace contexts are recorded.

    If no concrete SDK provider is registered, initializes TracerProvider and optionally attaches
    CloudTraceSpanExporter when running in Google Cloud or when export_to_cloud is True.
    """
    global _TELEMETRY_CONFIGURED
    current_provider = trace.get_tracer_provider()
    if not isinstance(current_provider, TracerProvider):
        provider = TracerProvider()
        proj = project_id or PROJECT_ID or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
        should_export_cloud = export_to_cloud
        if should_export_cloud is None:
            should_export_cloud = (
                os.getenv("DEPLOYMENT_ENV", "").lower() == "cloud"
                or os.getenv("ENABLE_CLOUD_TRACE", "").lower() == "true"
            )
        if should_export_cloud and proj:
            try:
                from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
                exporter = CloudTraceSpanExporter(project_id=proj)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception as err:
                logger.warning("Could not initialize CloudTraceSpanExporter: %s", err)

        trace.set_tracer_provider(provider)
        _TELEMETRY_CONFIGURED = True
        return provider
    return current_provider


_LOGGING_CONFIGURED = False


def configure_logging(level: int = logging.INFO, project_id: Optional[str] = None) -> None:
    """Configure structured JSON logging with trace correlation on stdout."""
    global _LOGGING_CONFIGURED
    proj = project_id or PROJECT_ID or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")

    # Ensure an SDK TracerProvider is active so log records correlate with real trace/span IDs
    configure_telemetry(project_id=proj)

    if _LOGGING_CONFIGURED:
        return

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Replace existing handlers on root logger
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(JsonTraceFormatter(project_id=proj))
    stream_handler.addFilter(RedactionFilter())

    root_logger.addHandler(stream_handler)
    _LOGGING_CONFIGURED = True


# -----------------------------------------------------------------------------
# Intent vs Outcome Logging Helpers
# -----------------------------------------------------------------------------

def log_intent(
    log: logging.Logger,
    component: str,
    intent: str,
    **kwargs: Any,
) -> None:
    """Log an explicit operational intent before executing an agent or tool action."""
    extra = {
        "event_type": "intent",
        "component": component,
        "intent": intent,
        "payload": kwargs if kwargs else None,
    }
    log.info("INTENT: [%s] %s", component, intent, extra=extra)


def log_outcome(
    log: logging.Logger,
    component: str,
    outcome: str,
    status: str = "SUCCESS",
    duration_ms: Optional[float] = None,
    **kwargs: Any,
) -> None:
    """Log an explicit operational outcome after completing an agent or tool action."""
    extra = {
        "event_type": "outcome",
        "component": component,
        "outcome": outcome,
        "outcome_status": status,
        "duration_ms": duration_ms,
        "payload": kwargs if kwargs else None,
    }
    log.info("OUTCOME: [%s] (%s) %s", component, status, outcome, extra=extra)


# -----------------------------------------------------------------------------
# Distributed Tracing & Custom Span Helpers (HLD §13.2)
# -----------------------------------------------------------------------------

def get_tracer(name: str = "meeting_prep") -> trace.Tracer:
    """Return configured OpenTelemetry tracer."""
    return trace.get_tracer(name, "1.0.0")


def record_hitl_wait_span(
    gate_name: str,
    wait_duration_s: float,
    decision_status: str,
    company: str = "",
) -> None:
    """Explicitly emit an OpenTelemetry span representing human wait time at a HITL gate.

    HLD §13.2: A span around each HITL gate, so human wait time is measured separately
    from agent execution time.
    """
    tracer = get_tracer("meeting_prep.hitl")
    span_name = f"hitl_wait.{gate_name}"
    start_time_ns = time.time_ns() - int(wait_duration_s * 1e9)
    end_time_ns = time.time_ns()

    span = tracer.start_span(
        name=span_name,
        start_time=start_time_ns,
    )
    span.set_attribute("hitl.gate_name", gate_name)
    span.set_attribute("hitl.wait_duration_s", round(wait_duration_s, 3))
    span.set_attribute("hitl.wait_duration_ms", round(wait_duration_s * 1000, 1))
    span.set_attribute("hitl.decision_status", decision_status)
    if company:
        span.set_attribute("hitl.company", company)

    span.set_status(Status(StatusCode.OK))
    span.end(end_time=end_time_ns)

    logger.info(
        "Recorded HITL wait span for '%s': %.2fs (decision: %s)",
        gate_name,
        wait_duration_s,
        decision_status,
        extra={
            "event_type": "hitl_wait",
            "component": "hitl",
            "gate_name": gate_name,
            "wait_duration_s": round(wait_duration_s, 3),
            "decision_status": decision_status,
        },
    )


# -----------------------------------------------------------------------------
# Agent Lifecycle Telemetry Callbacks (HLD §13.2)
# -----------------------------------------------------------------------------

_AGENT_START_TIMES: dict[str, float] = {}


def before_agent_telemetry(callback_context: Any) -> None:
    """Agent before_agent lifecycle hook recording intent and starting timer."""
    agent_name = getattr(callback_context, "agent_name", "unknown_agent")
    _AGENT_START_TIMES[agent_name] = time.perf_counter()

    state = callback_context.state or {}
    company = state.get("company_input") or (state.get("resolved_entity") or {}).get("name") or "Unknown"

    log_intent(
        log=logger,
        component=agent_name,
        intent=f"Agent '{agent_name}' initiating step for company '{company}'",
        company=company,
        session_id=getattr(callback_context, "session_id", ""),
    )


def after_agent_telemetry(callback_context: Any) -> None:
    """Agent after_agent lifecycle hook recording outcome, subagent attributes, and provenance."""
    agent_name = getattr(callback_context, "agent_name", "unknown_agent")
    start_time = _AGENT_START_TIMES.pop(agent_name, None)
    duration_s = (time.perf_counter() - start_time) if start_time else 0.0
    duration_ms = round(duration_s * 1000, 2)

    state = callback_context.state or {}
    company = state.get("company_input") or (state.get("resolved_entity") or {}).get("name") or "Unknown"

    log_outcome(
        log=logger,
        component=agent_name,
        outcome=f"Agent '{agent_name}' completed execution in {duration_ms}ms",
        status="SUCCESS",
        duration_ms=duration_ms,
        company=company,
    )

    # 1. Inject custom attributes onto active OpenTelemetry span (HLD §13.2)
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("subagent.name", agent_name)
        span.set_attribute("subagent.model", MODEL_NAME)
        span.set_attribute("subagent.latency_ms", duration_ms)
        span.set_attribute("subagent.latency_s", round(duration_s, 3))

    # 2. Update brief_provenance in session state
    provenance = dict(state.get("brief_provenance") or {})
    section_map = {
        "profile_researcher": "Company Profile",
        "news_researcher": "Recent Developments",
        "focus_researcher": "Focus Areas",
        "delta_agent": "Executive Delta",
        "composer": "Brief Synthesis",
        "publisher": "Google Doc Publishing",
    }
    section_name = section_map.get(agent_name)
    if section_name:
        provenance[section_name] = {
            "agent": agent_name,
            "latency_s": round(duration_s, 2),
            "model": MODEL_NAME,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "COMPLETED",
        }
        state["brief_provenance"] = provenance


def record_router_classification_span(
    target: str,
    confidence: float,
    directive: str,
    iteration: int = 1,
) -> None:
    """Inject refinement router classification attributes onto active span (HLD §13.2)."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("refinement.target", str(target))
        span.set_attribute("refinement.confidence", float(confidence))
        span.set_attribute("refinement.directive", str(directive)[:150])
        span.set_attribute("refinement.iteration", int(iteration))

    log_outcome(
        log=logger,
        component="refinement_router",
        outcome=f"Routed refinement iteration {iteration} to '{target}' (confidence={confidence:.2f})",
        status="SUCCESS",
        target=target,
        confidence=confidence,
        iteration=iteration,
    )


# -----------------------------------------------------------------------------
# Terminal UI Provenance Panel (HLD §13.2)
# -----------------------------------------------------------------------------

def format_provenance_table(provenance: dict[str, Any]) -> str:
    """Format brief provenance metadata into a clean ASCII terminal report."""
    if not provenance:
        return "   No provenance metadata recorded."

    lines = [
        "┌" + "─" * 70 + "┐",
        f"│ {'SECTION':<24} │ {'PRODUCED BY':<20} │ {'LATENCY':<8} │ {'STATUS':<9} │",
        "├" + "─" * 70 + "┤",
    ]

    total_latency = 0.0
    for section, details in provenance.items():
        if isinstance(details, dict):
            agent = details.get("agent", "unknown")
            lat = details.get("latency_s", 0.0)
            status = details.get("status", "OK")
            total_latency += lat
            lat_str = f"{lat:.2f}s"
            lines.append(f"│ {section:<24} │ {agent:<20} │ {lat_str:<8} │ {status:<9} │")

    lines.append("├" + "─" * 70 + "┤")
    tot_str = f"{total_latency:.2f}s"
    lines.append(f"│ {'TOTAL PIPELINE LATENCY':<47} │ {tot_str:<8} │ {'DONE':<9} │")
    lines.append("└" + "─" * 70 + "┘")
    return "\n".join(lines)
