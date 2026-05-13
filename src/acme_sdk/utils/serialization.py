"""Serialization helpers for converting telemetry data to wire formats."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from acme_sdk.models import Span, Event, Metric, Trace


def serialize_spans(spans: Sequence[Span]) -> dict[str, Any]:
    """Serialize a sequence of spans to the Acme ingest format.

    Args:
        spans: Sequence of Span objects.

    Returns:
        Dictionary matching the Acme /v1/spans API schema.
    """
    return {
        "resource_spans": [
            {
                "resource": {
                    "attributes": _serialize_attributes(span.resource_attributes),
                },
                "scope_spans": [
                    {
                        "spans": [_serialize_span(span)],
                    }
                ],
            }
            for span in spans
        ]
    }


def serialize_events(events: Sequence[Event]) -> dict[str, Any]:
    """Serialize a sequence of events to the Acme ingest format.

    Args:
        events: Sequence of Event objects.

    Returns:
        Dictionary matching the Acme /v1/events API schema.
    """
    return {
        "events": [
            {
                "name": event.name,
                "timestamp": _format_timestamp(event.timestamp),
                "attributes": _serialize_attributes(event.attributes),
            }
            for event in events
        ]
    }


def serialize_metrics(metrics: Sequence[Metric]) -> dict[str, Any]:
    """Serialize a sequence of metrics to the Acme ingest format.

    Args:
        metrics: Sequence of Metric objects.

    Returns:
        Dictionary matching the Acme /v1/metrics API schema.
    """
    return {
        "resource_metrics": [
            {
                "scope_metrics": [
                    {
                        "metrics": [
                            {
                                "name": metric.name,
                                "type": metric.metric_type.value,
                                "value": metric.value,
                                "unit": metric.unit,
                                "timestamp": _format_timestamp(metric.timestamp),
                                "tags": metric.tags,
                                "description": metric.description,
                            }
                        ]
                    }
                ],
                "resource": {
                    "service_name": metric.service_name,
                },
            }
            for metric in metrics
        ]
    }


def serialize_trace(trace: Trace) -> dict[str, Any]:
    """Serialize a complete trace to the Acme ingest format.

    Args:
        trace: Trace object containing spans.

    Returns:
        Dictionary matching the Acme trace format.
    """
    return {
        "trace_id": trace.trace_id,
        "service_name": trace.service_name,
        "attributes": _serialize_attributes(trace.attributes),
        "spans": [_serialize_span(span) for span in trace.spans],
        "span_count": trace.span_count,
    }


def _serialize_span(span: Span) -> dict[str, Any]:
    """Serialize a single span."""
    data: dict[str, Any] = {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "name": span.name,
        "kind": span.kind.value,
        "status": {
            "code": span.status.value,
        },
        "start_time_unix_nano": _to_unix_nano(span.start_time),
        "end_time_unix_nano": _to_unix_nano(span.end_time) if span.end_time else None,
        "attributes": _serialize_attributes(span.attributes),
        "service_name": span.service_name,
    }

    if span.parent_span_id:
        data["parent_span_id"] = span.parent_span_id

    if span.events:
        data["events"] = [
            {
                "name": event.name,
                "time_unix_nano": _to_unix_nano(event.timestamp),
                "attributes": _serialize_attributes(event.attributes),
            }
            for event in span.events
        ]

    if span.duration_ms is not None:
        data["duration_ms"] = span.duration_ms

    return data


def _serialize_attributes(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert an attributes dictionary to the OTLP key-value format."""
    result = []
    for key, value in attrs.items():
        result.append({
            "key": key,
            "value": _serialize_attribute_value(value),
        })
    return result


def _serialize_attribute_value(value: Any) -> dict[str, Any]:
    """Serialize a single attribute value to the OTLP value format."""
    if isinstance(value, bool):
        return {"bool_value": value}
    elif isinstance(value, int):
        return {"int_value": value}
    elif isinstance(value, float):
        return {"double_value": value}
    elif isinstance(value, str):
        return {"string_value": value}
    elif isinstance(value, (list, tuple)):
        return {
            "array_value": {
                "values": [_serialize_attribute_value(v) for v in value]
            }
        }
    elif isinstance(value, dict):
        return {
            "kvlist_value": {
                "values": [
                    {"key": k, "value": _serialize_attribute_value(v)}
                    for k, v in value.items()
                ]
            }
        }
    else:
        return {"string_value": str(value)}


def _format_timestamp(dt: datetime) -> str:
    """Format a datetime as ISO 8601 with UTC timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _to_unix_nano(dt: datetime) -> int:
    """Convert a datetime to Unix nanoseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def spans_to_json(spans: Sequence[Span], indent: int = 2) -> str:
    """Convenience function to serialize spans to a JSON string.

    Args:
        spans: Sequence of Span objects.
        indent: JSON indentation level.

    Returns:
        JSON string representation.
    """
    data = serialize_spans(spans)
    return json.dumps(data, indent=indent, default=str)
