"""Tests for the JSON file exporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acme_sdk.exporters.json_file import JSONFileExporter
from acme_sdk.models import Span


@pytest.fixture
def output_dir(tmp_path) -> Path:
    return tmp_path / "test_traces"


@pytest.fixture
def exporter(output_dir) -> JSONFileExporter:
    return JSONFileExporter(output_dir=output_dir, pretty_print=True)


class TestJSONFileExporter:
    """Tests for JSON file export."""

    def test_creates_output_directory(self, output_dir):
        JSONFileExporter(output_dir=output_dir)
        assert output_dir.exists()

    def test_export_empty_batch(self, exporter):
        count = exporter.export([])
        assert count == 0

    def test_export_single_span(self, exporter, sample_span, output_dir):
        count = exporter.export([sample_span])
        assert count == 1
        files = exporter.list_output_files()
        assert len(files) == 1

    def test_export_writes_valid_json(self, exporter, sample_span, output_dir):
        exporter.export([sample_span])
        files = exporter.list_output_files()
        with open(files[0]) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_export_multiple_batches(self, exporter, sample_span):
        exporter.export([sample_span])
        exporter.export([sample_span])
        # Both should go to the same file (under max size)
        files = exporter.list_output_files()
        assert len(files) >= 1

    def test_custom_filename_prefix(self, output_dir):
        exporter = JSONFileExporter(
            output_dir=output_dir,
            filename_prefix="custom",
        )
        span = Span(name="test", service_name="svc", duration_ms=1.0)
        exporter.export([span])
        files = list(output_dir.glob("custom_*.json"))
        assert len(files) == 1

    def test_repr(self, exporter):
        repr_str = repr(exporter)
        assert "JSONFileExporter" in repr_str
