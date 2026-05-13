"""Console exporter for printing telemetry data to stdout."""

from __future__ import annotations

import json
import sys
import logging
from io import TextIOBase
from typing import Any, Optional, Sequence, TextIO

from acme_sdk.models import Span, Metric, SpanStatus

logger = logging.getLogger(__name__)

# ANSI color codes
_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}


class ConsoleExporter:
    """Export telemetry data to the console for debugging.

    Prints human-readable representations of spans and metrics to stdout
    or a configurable output stream. Supports colorized output for
    terminal environments.

    Args:
        output: Output stream to write to. Defaults to sys.stdout.
        colorize: Whether to use ANSI color codes. Defaults to True.
        verbose: Whether to include all span attributes. Defaults to False.
        json_output: Whether to output raw JSON instead of formatted text.
    """

    def __init__(
        self,
        output: Optional[TextIO] = None,
        colorize: bool = True,
        verbose: bool = False,
        json_output: bool = False,
    ) -> None:
        self._output = output or sys.stdout
        self._colorize = colorize and hasattr(self._output, "isatty") and self._output.isatty()
        self._verbose = verbose
        self._json_output = json_output
        self._export_count = 0

    def export(self, spans: Sequence[Span]) -> int:
        """Export spans to the console.

        Args:
            spans: Sequence of Span objects to display.

        Returns:
            Number of spans printed.
        """
        if not spans:
            return 0

        if self._json_output:
            return self._export_json(spans)

        for span in spans:
            self._print_span(span)

        self._export_count += len(spans)
        return len(spans)

    def export_metrics(self, metrics: Sequence[Metric]) -> int:
        """Export metrics to the console.

        Args:
            metrics: Sequence of Metric objects to display.

        Returns:
            Number of metrics printed.
        """
        if not metrics:
            return 0

        for metric in metrics:
            self._print_metric(metric)

        return len(metrics)

    def _print_span(self, span: Span) -> None:
        """Print a single span in a human-readable format."""
        status_color = self._get_status_color(span.status)
        status_icon = self._get_status_icon(span.status)

        # Header line
        header = f"{status_icon} {self._style(span.name, 'bold')} "
        header += f"[{self._style(span.service_name, 'cyan')}]"
        self._write(header)

        # Details
        self._write(f"  Span ID:  {span.span_id}")
        self._write(f"  Trace ID: {span.trace_id}")
        if span.parent_span_id:
            self._write(f"  Parent:   {span.parent_span_id}")
        self._write(f"  Kind:     {span.kind.value}")
        self._write(f"  Status:   {self._style(span.status.value, status_color)}")

        if span.duration_ms is not None:
            duration_str = f"{span.duration_ms:.2f}ms"
            self._write(f"  Duration: {duration_str}")

        self._write(f"  Start:    {span.start_time.isoformat()}")
        if span.end_time:
            self._write(f"  End:      {span.end_time.isoformat()}")

        # Attributes
        if span.attributes and self._verbose:
            self._write(f"  Attributes:")
            for key, value in span.attributes.items():
                self._write(f"    {self._style(key, 'dim')}: {value}")

        # Events
        if span.events:
            self._write(f"  Events ({len(span.events)}):")
            for event in span.events:
                self._write(f"    - {event.name} @ {event.timestamp.isoformat()}")

        self._write("")  # Blank line between spans

    def _print_metric(self, metric: Metric) -> None:
        """Print a single metric."""
        type_str = self._style(metric.metric_type.value, "magenta")
        self._write(
            f"  {self._style(metric.name, 'bold')} "
            f"[{type_str}] = {metric.value}"
            f"{' ' + metric.unit if metric.unit else ''}"
        )
        if metric.tags:
            tags_str = ", ".join(f"{k}={v}" for k, v in metric.tags.items())
            self._write(f"    tags: {tags_str}")

    def _export_json(self, spans: Sequence[Span]) -> int:
        """Export spans as raw JSON."""
        for span in spans:
            data = span.model_dump(mode="json")
            self._write(json.dumps(data, indent=2, default=str))

        self._export_count += len(spans)
        return len(spans)

    def _style(self, text: str, style: str) -> str:
        """Apply ANSI styling if colorization is enabled."""
        if not self._colorize or style not in _COLORS:
            return text
        return f"{_COLORS[style]}{text}{_COLORS['reset']}"

    def _get_status_color(self, status: SpanStatus) -> str:
        """Get the color name for a span status."""
        return {
            SpanStatus.OK: "green",
            SpanStatus.ERROR: "red",
            SpanStatus.UNSET: "yellow",
        }.get(status, "white")

    def _get_status_icon(self, status: SpanStatus) -> str:
        """Get a unicode icon for a span status."""
        icons = {
            SpanStatus.OK: self._style("✓", "green"),
            SpanStatus.ERROR: self._style("✗", "red"),
            SpanStatus.UNSET: self._style("○", "yellow"),
        }
        return icons.get(status, "?")

    def _write(self, text: str) -> None:
        """Write a line to the output stream."""
        self._output.write(text + "\n")

    def __repr__(self) -> str:
        return f"ConsoleExporter(colorize={self._colorize}, verbose={self._verbose})"
