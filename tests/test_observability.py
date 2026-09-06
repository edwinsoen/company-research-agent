"""Unit tests for Phase 6 Observability, Distributed Tracing, and Redaction."""

import io
import json
import logging
import unittest
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from meeting_prep.telemetry.redaction import (
    RedactionPipeline,
    RedactionFilter,
    redact_text,
    redact_data,
    redact_email,
)
from meeting_prep.callbacks.telemetry import (
    JsonTraceFormatter,
    log_intent,
    log_outcome,
    record_hitl_wait_span,
    record_router_classification_span,
    format_provenance_table,
)


class TestObservability(unittest.TestCase):
    """Test suite verifying structured logging, distributed tracing, and redaction."""

    def test_redaction_pipeline_patterns(self):
        """Verify regex patterns for emails, phones, tokens, API keys, and IP addresses."""
        pipeline = RedactionPipeline()

        # Email
        self.assertEqual(
            pipeline.redact_text("Contact john.doe@example.com for info"),
            "Contact j******e@example.com for info",
        )
        self.assertEqual(redact_email("edwin@google.com"), "e***n@google.com")

        # Phone
        self.assertEqual(
            pipeline.redact_text("Call +1 555-123-4567 or (800) 555-0199"),
            "Call [PHONE_REDACTED] or [PHONE_REDACTED]",
        )

        # Tokens & API keys
        oauth_prefix = "ya" + "29"
        self.assertEqual(
            pipeline.redact_text(f"Header: Bearer {oauth_prefix}.a0AfH6SMD_secret12345678901234567890"),
            "Header: [BEARER_TOKEN_REDACTED]",
        )
        self.assertEqual(
            pipeline.redact_text(f"Standalone {oauth_prefix}.a0AfH6SMD_secret12345678901234567890 token"),
            "Standalone [BEARER_TOKEN_REDACTED] token",
        )
        self.assertEqual(
            pipeline.redact_text("Key AIzaSyD1234567890123456789012345678901"),
            "Key [API_KEY_REDACTED]",
        )
        self.assertEqual(
            pipeline.redact_text("Key AQ.MockSyntheticGcpApiKey1234567890abcdef"),
            "Key [API_KEY_REDACTED]",
        )

        # IP address
        self.assertEqual(
            pipeline.redact_text("Host 192.168.1.10 connected"),
            "Host [IP_REDACTED] connected",
        )

    def test_redaction_pipeline_recursive_data(self):
        """Verify recursive sanitization of nested dictionaries, lists, and sensitive key names."""
        oauth_prefix = "ya" + "29"
        data = {
            "user_token": f"{oauth_prefix}.sample_secret_token",
            "api_key": "AIzaSyD1234567890123456789012345678901",
            "recipients": ["alice@domain.org", "bob@example.com"],
            "recipient_map": {"executive.reviewer@corp-partner.com": "success"},
            "metadata": {
                "phone": "415-555-0100",
                "auth_header": "Bearer token123",
                "nested_list": [
                    {"server_ip": "10.0.0.1", "password": "SuperSecretPassword123"}
                ],
            },
        }

        sanitized = redact_data(data)

        self.assertEqual(sanitized["user_token"], "[REDACTED_SECRET]")
        self.assertEqual(sanitized["api_key"], "[REDACTED_SECRET]")
        self.assertEqual(sanitized["recipients"], ["a***e@domain.org", "b*b@example.com"])
        self.assertIn("e****************r@corp-partner.com", sanitized["recipient_map"])
        self.assertNotIn("executive.reviewer@corp-partner.com", sanitized["recipient_map"])
        self.assertEqual(sanitized["metadata"]["phone"], "[PHONE_REDACTED]")
        self.assertEqual(sanitized["metadata"]["auth_header"], "[REDACTED_SECRET]")
        self.assertEqual(sanitized["metadata"]["nested_list"][0]["server_ip"], "[IP_REDACTED]")
        self.assertEqual(sanitized["metadata"]["nested_list"][0]["password"], "[REDACTED_SECRET]")

    def test_json_trace_formatter_and_trace_correlation(self):
        """Verify JSON formatting and OpenTelemetry trace/span ID correlation in logs."""
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test_tracer")

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        formatter = JsonTraceFormatter(project_id="test-proj-123")
        handler.setFormatter(formatter)
        handler.addFilter(RedactionFilter())

        test_log = logging.getLogger("test_trace_correlation")
        test_log.setLevel(logging.INFO)
        test_log.handlers = [handler]
        test_log.propagate = False

        # 1. Log outside of span
        test_log.info("Uncorrelated test message", extra={"event_type": "test_event"})
        raw_json1 = stream.getvalue().strip().split("\n")[-1]
        parsed1 = json.loads(raw_json1)

        self.assertEqual(parsed1["severity"], "INFO")
        self.assertEqual(parsed1["message"], "Uncorrelated test message")
        self.assertEqual(parsed1["event_type"], "test_event")
        self.assertNotIn("logging.googleapis.com/trace", parsed1)

        # 2. Log within active span
        with tracer.start_as_current_span("parent_test_span") as span:
            test_log.info("Correlated test message", extra={"event_type": "span_event"})
            ctx = span.get_span_context()
            expected_trace_id = f"{ctx.trace_id:032x}"
            expected_span_id = f"{ctx.span_id:016x}"

        raw_json2 = stream.getvalue().strip().split("\n")[-1]
        parsed2 = json.loads(raw_json2)

        self.assertEqual(parsed2["severity"], "INFO")
        self.assertEqual(parsed2["message"], "Correlated test message")
        self.assertEqual(parsed2["event_type"], "span_event")
        self.assertEqual(
            parsed2["logging.googleapis.com/trace"],
            f"projects/test-proj-123/traces/{expected_trace_id}",
        )
        self.assertEqual(parsed2["logging.googleapis.com/spanId"], expected_span_id)

    def test_intent_and_outcome_logging(self):
        """Verify structured intent and outcome logs."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonTraceFormatter(project_id="test-project"))
        handler.addFilter(RedactionFilter())

        test_log = logging.getLogger("test_intent_outcome")
        test_log.setLevel(logging.INFO)
        test_log.handlers = [handler]
        test_log.propagate = False

        log_intent(test_log, "test_component", "Starting unit test operation", item_count=5)
        log_outcome(test_log, "test_component", "Unit test completed", status="SUCCESS", duration_ms=12.5)

        lines = stream.getvalue().strip().split("\n")
        intent_record = json.loads(lines[0])
        outcome_record = json.loads(lines[1])

        self.assertEqual(intent_record["event_type"], "intent")
        self.assertEqual(intent_record["intent"], "Starting unit test operation")
        self.assertEqual(intent_record["component"], "test_component")
        self.assertEqual(intent_record["item_count"], 5)

        self.assertEqual(outcome_record["event_type"], "outcome")
        self.assertEqual(outcome_record["outcome"], "Unit test completed")
        self.assertEqual(outcome_record["outcome_status"], "SUCCESS")
        self.assertEqual(outcome_record["duration_ms"], 12.5)

    def test_hitl_wait_span_and_router_classification(self):
        """Verify recording of HITL wait spans and router classification span attributes."""
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("meeting_prep.hitl")

        with patch("meeting_prep.callbacks.telemetry.get_tracer", return_value=tracer):
            record_hitl_wait_span(
                gate_name="approve_brief",
                wait_duration_s=3.25,
                decision_status="approved",
                company="Stripe",
            )

        spans = exporter.get_finished_spans()
        hitl_spans = [s for s in spans if s.name == "hitl_wait.approve_brief"]
        self.assertEqual(len(hitl_spans), 1)
        hitl_span = hitl_spans[0]
        self.assertEqual(hitl_span.attributes["hitl.gate_name"], "approve_brief")
        self.assertEqual(hitl_span.attributes["hitl.wait_duration_s"], 3.25)
        self.assertEqual(hitl_span.attributes["hitl.decision_status"], "approved")
        self.assertEqual(hitl_span.attributes["hitl.company"], "Stripe")

    def test_format_provenance_table(self):
        """Verify UI provenance panel formatting into clean ASCII table."""
        provenance = {
            "Company Profile": {"agent": "profile_researcher", "latency_s": 2.14, "status": "COMPLETED"},
            "Recent Developments": {"agent": "news_researcher", "latency_s": 1.85, "status": "COMPLETED"},
            "Executive Delta": {"agent": "delta_agent", "latency_s": 0.95, "status": "COMPLETED"},
        }
        table = format_provenance_table(provenance)
        self.assertIn("SECTION", table)
        self.assertIn("PRODUCED BY", table)
        self.assertIn("Company Profile", table)
        self.assertIn("profile_researcher", table)
        self.assertIn("2.14s", table)
        self.assertIn("TOTAL PIPELINE LATENCY", table)


if __name__ == "__main__":
    unittest.main()
