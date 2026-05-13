"""Tests for the OTLP exporter."""

from __future__ import annotations

import pytest
import httpx
import respx

from acme_sdk.client import AcmeClient
from acme_sdk.config import AcmeConfig
from acme_sdk.exporters.otlp import OTLPExporter, ExportResult
from acme_sdk.models import Span, Metric, MetricType


@pytest.fixture
def exporter(client) -> OTLPExporter:
    return OTLPExporter(client=client, compression=False)


class TestOTLPExporter:
    """Tests for OTLP HTTP export."""

    def test_export_empty_batch(self, exporter):
        result = exporter.export([])
        assert result.success is True
        assert result.exported_count == 0

    def test_export_single_span(self, exporter, sample_span):
        result = exporter.export([sample_span])
        assert result.success is True
        assert result.exported_count == 1

    def test_export_multiple_spans(self, exporter):
        spans = [
            Span(name=f"span-{i}", service_name="svc", duration_ms=float(i))
            for i in range(10)
        ]
        result = exporter.export(spans)
        assert result.success is True
        assert result.exported_count == 10

    def test_export_large_batch_is_chunked(self, client):
        exporter = OTLPExporter(client=client, max_batch_size=5)
        spans = [
            Span(name=f"span-{i}", service_name="svc", duration_ms=1.0)
            for i in range(12)
        ]
        result = exporter.export(spans)
        assert result.success is True
        assert result.exported_count == 12

    def test_export_metrics(self, exporter):
        metrics = [
            Metric(name="test.metric", value=42.0, metric_type=MetricType.GAUGE)
        ]
        result = exporter.export_metrics(metrics)
        assert result.success is True

    def test_stats_tracking(self, exporter, sample_span):
        exporter.export([sample_span])
        exporter.export([sample_span])
        stats = exporter.stats
        assert stats["total_exports"] == 2
        assert stats["total_errors"] == 0


class TestExportResult:
    """Tests for ExportResult."""

    def test_successful_result(self):
        result = ExportResult(success=True, exported_count=10)
        assert bool(result) is True
        assert result.errors == []

    def test_failed_result(self):
        result = ExportResult(
            success=False,
            exported_count=0,
            errors=["Connection refused"],
        )
        assert bool(result) is False
        assert len(result.errors) == 1
