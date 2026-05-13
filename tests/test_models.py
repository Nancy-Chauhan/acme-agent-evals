"""Tests for data models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from acme_sdk.models import (
    Event,
    Metric,
    MetricType,
    Span,
    SpanBatch,
    SpanKind,
    SpanStatus,
    Trace,
)


class TestSpan:
    """Tests for the Span model."""

    def test_create_minimal_span(self):
        span = Span(name="test", service_name="svc")
        assert span.name == "test"
        assert span.service_name == "svc"
        assert span.kind == SpanKind.INTERNAL
        assert span.status == SpanStatus.UNSET
        assert span.trace_id is not None
        assert span.span_id is not None

    def test_auto_generates_trace_id(self):
        span = Span(name="test", service_name="svc")
        assert len(span.trace_id) == 32

    def test_uses_provided_trace_id(self):
        span = Span(name="test", service_name="svc", trace_id="custom-trace-id")
        assert span.trace_id == "custom-trace-id"

    def test_add_event(self):
        span = Span(name="test", service_name="svc")
        event = span.add_event("test-event", {"key": "val"})
        assert len(span.events) == 1
        assert event.name == "test-event"
        assert event.attributes["key"] == "val"

    def test_set_status(self):
        span = Span(name="test", service_name="svc")
        span.set_status(SpanStatus.ERROR, "something went wrong")
        assert span.status == SpanStatus.ERROR
        assert span.attributes["status.description"] == "something went wrong"

    def test_finish_sets_end_time(self):
        span = Span(name="test", service_name="svc")
        assert span.end_time is None
        span.finish()
        assert span.end_time is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_duration_computed_from_times(self):
        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        span = Span(
            name="test",
            service_name="svc",
            start_time=start,
            end_time=end,
        )
        assert span.duration_ms == 1000.0

    def test_empty_name_raises(self):
        with pytest.raises(Exception):
            Span(name="", service_name="svc")


class TestEvent:
    """Tests for the Event model."""

    def test_create_event(self):
        event = Event(name="test-event")
        assert event.name == "test-event"
        assert event.timestamp is not None
        assert event.attributes == {}

    def test_blank_name_raises(self):
        with pytest.raises(Exception):
            Event(name="   ")


class TestTrace:
    """Tests for the Trace model."""

    def test_create_trace(self):
        trace = Trace(service_name="my-service")
        assert trace.trace_id is not None
        assert trace.spans == []
        assert trace.span_count == 0

    def test_add_span(self):
        trace = Trace(service_name="my-service")
        span = trace.add_span("operation")
        assert trace.span_count == 1
        assert span.trace_id == trace.trace_id
        assert span.service_name == "my-service"

    def test_root_span(self):
        trace = Trace(service_name="svc")
        root = trace.add_span("root")
        child = trace.add_span("child", parent_span_id=root.span_id)
        assert trace.root_span == root

    def test_no_root_span(self):
        trace = Trace(service_name="svc")
        span = trace.add_span("child", parent_span_id="external-parent")
        assert trace.root_span is None


class TestMetric:
    """Tests for the Metric model."""

    def test_create_metric(self):
        metric = Metric(name="request.count", value=42.0)
        assert metric.name == "request.count"
        assert metric.value == 42.0
        assert metric.metric_type == MetricType.GAUGE

    def test_invalid_metric_name(self):
        with pytest.raises(Exception):
            Metric(name="invalid name!", value=1.0)

    def test_metric_with_tags(self):
        metric = Metric(
            name="http.duration",
            value=150.0,
            tags={"method": "GET", "path": "/api"},
        )
        assert metric.tags["method"] == "GET"


class TestSpanBatch:
    """Tests for the SpanBatch model."""

    def test_empty_batch(self):
        batch = SpanBatch(spans=[])
        assert batch.is_empty is True
        assert batch.size == 0

    def test_batch_with_spans(self):
        spans = [
            Span(name="span1", service_name="svc"),
            Span(name="span2", service_name="svc"),
        ]
        batch = SpanBatch(spans=spans)
        assert batch.size == 2
        assert batch.is_empty is False
