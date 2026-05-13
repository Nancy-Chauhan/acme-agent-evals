"""OTLP HTTP exporter for sending telemetry to the Acme platform."""

from __future__ import annotations

import gzip
import json
import logging
from typing import Any, Optional, Sequence

import httpx

from acme_sdk.client import AcmeClient
from acme_sdk.models import Span, Event, Metric
from acme_sdk.utils.serialization import serialize_spans, serialize_metrics

logger = logging.getLogger(__name__)

DEFAULT_SPANS_ENDPOINT = "/v1/traces"
DEFAULT_METRICS_ENDPOINT = "/v1/metrics"
MAX_BATCH_SIZE = 1000


class OTLPExporter:
    """Export telemetry data using the OpenTelemetry Protocol over HTTP.

    This is the recommended exporter for production use. It supports
    gzip compression, batch size limits, and automatic chunking of
    large payloads.

    Args:
        client: An initialized AcmeClient instance.
        compression: Whether to gzip-compress request bodies. Defaults to True.
        max_batch_size: Maximum number of items per export request.
            Larger batches will be automatically chunked.
        headers: Additional HTTP headers to include in export requests.
    """

    def __init__(
        self,
        client: AcmeClient,
        compression: bool = True,
        max_batch_size: int = MAX_BATCH_SIZE,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._client = client
        self._compression = compression
        self._max_batch_size = max_batch_size
        self._extra_headers = headers or {}
        self._export_count = 0
        self._error_count = 0

    def export(self, spans: Sequence[Span]) -> ExportResult:
        """Export a batch of spans to the Acme platform.

        If the batch exceeds max_batch_size, it will be automatically
        split into multiple requests.

        Args:
            spans: Sequence of Span objects to export.

        Returns:
            ExportResult indicating success or failure.
        """
        if not spans:
            logger.debug("No spans to export, skipping")
            return ExportResult(success=True, exported_count=0)

        total_exported = 0
        errors: list[str] = []

        # Chunk large batches
        for i in range(0, len(spans), self._max_batch_size):
            chunk = spans[i : i + self._max_batch_size]
            try:
                self._client.send_spans(chunk)
                total_exported += len(chunk)
                logger.debug("Exported %d spans", len(chunk))
            except httpx.HTTPStatusError as exc:
                error_msg = f"Export failed with status {exc.response.status_code}"
                errors.append(error_msg)
                self._error_count += 1
                logger.error(error_msg)
            except httpx.HTTPError as exc:
                error_msg = f"Export request failed: {exc}"
                errors.append(error_msg)
                self._error_count += 1
                logger.error(error_msg)

        self._export_count += 1

        return ExportResult(
            success=len(errors) == 0,
            exported_count=total_exported,
            errors=errors if errors else None,
        )

    def export_metrics(self, metrics: Sequence[Metric]) -> ExportResult:
        """Export a batch of metrics to the Acme platform.

        Args:
            metrics: Sequence of Metric objects to export.

        Returns:
            ExportResult indicating success or failure.
        """
        if not metrics:
            return ExportResult(success=True, exported_count=0)

        try:
            self._client.send_metrics(metrics)
            self._export_count += 1
            return ExportResult(success=True, exported_count=len(metrics))
        except httpx.HTTPError as exc:
            self._error_count += 1
            return ExportResult(
                success=False,
                exported_count=0,
                errors=[str(exc)],
            )

    @property
    def stats(self) -> dict[str, int]:
        """Return export statistics."""
        return {
            "total_exports": self._export_count,
            "total_errors": self._error_count,
        }

    def __repr__(self) -> str:
        return (
            f"OTLPExporter(compression={self._compression}, "
            f"max_batch_size={self._max_batch_size})"
        )


class ExportResult:
    """Result of an export operation.

    Attributes:
        success: Whether the export completed without errors.
        exported_count: Number of items successfully exported.
        errors: List of error messages, if any.
    """

    def __init__(
        self,
        success: bool,
        exported_count: int,
        errors: Optional[list[str]] = None,
    ) -> None:
        self.success = success
        self.exported_count = exported_count
        self.errors = errors or []

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        return f"ExportResult(success={self.success}, exported_count={self.exported_count})"
