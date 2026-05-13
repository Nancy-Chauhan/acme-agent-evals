"""Data models for the Acme SDK telemetry types."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field, field_validator


class SpanKind(str, Enum):
    """Enumeration of span kinds following OpenTelemetry conventions."""

    INTERNAL = "INTERNAL"
    SERVER = "SERVER"
    CLIENT = "CLIENT"
    PRODUCER = "PRODUCER"
    CONSUMER = "CONSUMER"


class SpanStatus(str, Enum):
    """Status of a span execution."""

    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


class MetricType(str, Enum):
    """Types of metrics supported by the Acme platform."""

    COUNTER = "COUNTER"
    GAUGE = "GAUGE"
    HISTOGRAM = "HISTOGRAM"
    SUMMARY = "SUMMARY"


class Event(BaseModel):
    """Represents a timestamped event within a span.

    Events are used to record discrete occurrences during a span's lifetime,
    such as log entries, exceptions, or application-specific events.
    """

    name: str = Field(..., min_length=1, max_length=256)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Event name cannot be blank")
        return v.strip()


class Span(BaseModel):
    """Represents a single unit of work in a distributed trace.

    A span tracks an operation with timing information, status, and
    arbitrary attributes. Spans can be nested to form a trace tree.
    """

    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    trace_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=512)
    service_name: str = Field(..., min_length=1, max_length=256)
    kind: SpanKind = SpanKind.INTERNAL
    status: SpanStatus = SpanStatus.UNSET
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    resource_attributes: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Auto-generate trace_id if not provided and compute duration."""
        if self.trace_id is None:
            self.trace_id = uuid.uuid4().hex
        if self.duration_ms is None and self.end_time is not None:
            delta = self.end_time - self.start_time
            self.duration_ms = delta.total_seconds() * 1000

    def add_event(self, name: str, attributes: Optional[dict[str, Any]] = None) -> Event:
        """Add an event to this span.

        Args:
            name: Event name.
            attributes: Optional event attributes.

        Returns:
            The created Event instance.
        """
        event = Event(name=name, attributes=attributes or {})
        self.events.append(event)
        return event

    def set_status(self, status: SpanStatus, description: Optional[str] = None) -> None:
        """Set the span status.

        Args:
            status: The new status for this span.
            description: Optional human-readable status description.
        """
        self.status = status
        if description:
            self.attributes["status.description"] = description

    def finish(self) -> None:
        """Mark the span as finished by setting the end time and computing duration."""
        self.end_time = datetime.now(timezone.utc)
        delta = self.end_time - self.start_time
        self.duration_ms = delta.total_seconds() * 1000


class Trace(BaseModel):
    """Represents a distributed trace composed of multiple spans.

    A trace is a directed acyclic graph of spans that represents the
    end-to-end journey of a request through a distributed system.
    """

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    spans: list[Span] = Field(default_factory=list)
    service_name: str = Field(..., min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    def add_span(self, name: str, **kwargs: Any) -> Span:
        """Create and add a new span to this trace.

        Args:
            name: Span name.
            **kwargs: Additional span parameters.

        Returns:
            The created Span instance.
        """
        span = Span(
            name=name,
            trace_id=self.trace_id,
            service_name=self.service_name,
            **kwargs,
        )
        self.spans.append(span)
        return span

    @property
    def root_span(self) -> Optional[Span]:
        """Return the root span of the trace (span with no parent)."""
        for span in self.spans:
            if span.parent_span_id is None:
                return span
        return None

    @property
    def duration_ms(self) -> Optional[float]:
        """Return the total trace duration based on the root span."""
        root = self.root_span
        return root.duration_ms if root else None

    @property
    def span_count(self) -> int:
        """Return the number of spans in this trace."""
        return len(self.spans)


class Metric(BaseModel):
    """Represents a metric data point.

    Metrics capture numerical measurements about a system, such as
    request counts, response times, or resource utilization.
    """

    name: str = Field(..., min_length=1, max_length=256)
    value: float
    metric_type: MetricType = MetricType.GAUGE
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    unit: Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)
    service_name: Optional[str] = None
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_metric_name(cls, v: str) -> str:
        """Validate that metric names follow conventions."""
        if not v.replace(".", "").replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Metric name {v!r} contains invalid characters. "
                "Use only alphanumeric characters, dots, underscores, and hyphens."
            )
        return v


class SpanBatch(BaseModel):
    """A batch of spans for bulk export.

    Used internally by exporters to group spans for efficient transport.
    """

    spans: Sequence[Span]
    batch_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def size(self) -> int:
        return len(self.spans)

    @property
    def is_empty(self) -> bool:
        return len(self.spans) == 0
