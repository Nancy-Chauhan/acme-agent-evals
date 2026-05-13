"""JSON file exporter for writing telemetry data to local files."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from acme_sdk.models import Span, Metric
from acme_sdk.utils.serialization import serialize_spans, serialize_metrics

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "./acme_traces"
DEFAULT_MAX_FILE_SIZE_MB = 100


class JSONFileExporter:
    """Export telemetry data to local JSON files.

    Writes spans and metrics to JSON files in a configurable output directory.
    Files are automatically rotated when they exceed the maximum file size.
    Useful for local development, debugging, and offline analysis.

    Args:
        output_dir: Directory to write JSON files to. Created if it doesn't exist.
        max_file_size_mb: Maximum file size in megabytes before rotation.
        pretty_print: Whether to indent JSON output for readability.
        filename_prefix: Prefix for output filenames.
    """

    def __init__(
        self,
        output_dir: str | Path = DEFAULT_OUTPUT_DIR,
        max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB,
        pretty_print: bool = False,
        filename_prefix: str = "acme_traces",
    ) -> None:
        self._output_dir = Path(output_dir)
        self._max_file_size = max_file_size_mb * 1024 * 1024  # Convert to bytes
        self._pretty_print = pretty_print
        self._filename_prefix = filename_prefix
        self._current_file: Optional[Path] = None
        self._export_count = 0

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[Span]) -> int:
        """Export spans to a JSON file.

        Args:
            spans: Sequence of Span objects to write.

        Returns:
            Number of spans written.
        """
        if not spans:
            return 0

        file_path = self._get_current_file()
        data = serialize_spans(spans)

        self._write_json(file_path, data)
        self._export_count += len(spans)

        logger.info("Wrote %d spans to %s", len(spans), file_path)
        return len(spans)

    def export_metrics(self, metrics: Sequence[Metric]) -> int:
        """Export metrics to a JSON file.

        Args:
            metrics: Sequence of Metric objects to write.

        Returns:
            Number of metrics written.
        """
        if not metrics:
            return 0

        file_path = self._get_metrics_file()
        data = serialize_metrics(metrics)

        self._write_json(file_path, data)
        return len(metrics)

    def _write_json(self, file_path: Path, data: dict[str, Any]) -> None:
        """Write JSON data to a file, appending to existing content."""
        existing_data: list[dict[str, Any]] = []

        if file_path.exists() and file_path.stat().st_size > 0:
            try:
                with open(file_path, "r") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        existing_data = content
                    else:
                        existing_data = [content]
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("Failed to read existing file %s: %s", file_path, exc)
                existing_data = []

        existing_data.append(data)

        indent = 2 if self._pretty_print else None
        with open(file_path, "w") as f:
            json.dump(existing_data, f, indent=indent, default=str)

    def _get_current_file(self) -> Path:
        """Get the current output file, rotating if necessary."""
        if self._current_file is None or self._should_rotate(self._current_file):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"{self._filename_prefix}_{timestamp}.json"
            self._current_file = self._output_dir / filename

        return self._current_file

    def _get_metrics_file(self) -> Path:
        """Get the output file for metrics."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{self._filename_prefix}_metrics_{timestamp}.json"
        return self._output_dir / filename

    def _should_rotate(self, file_path: Path) -> bool:
        """Check if the current file exceeds the maximum size."""
        if not file_path.exists():
            return False
        return file_path.stat().st_size >= self._max_file_size

    def list_output_files(self) -> list[Path]:
        """List all output files in the output directory."""
        return sorted(self._output_dir.glob(f"{self._filename_prefix}_*.json"))

    def cleanup(self, max_age_hours: int = 24) -> int:
        """Remove output files older than the specified age.

        Args:
            max_age_hours: Maximum age of files to keep, in hours.

        Returns:
            Number of files removed.
        """
        import time

        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0

        for file_path in self.list_output_files():
            if file_path.stat().st_mtime < cutoff:
                file_path.unlink()
                removed += 1
                logger.debug("Removed old trace file: %s", file_path)

        return removed

    def __repr__(self) -> str:
        return (
            f"JSONFileExporter(output_dir={self._output_dir!r}, "
            f"max_file_size_mb={self._max_file_size // (1024 * 1024)})"
        )
