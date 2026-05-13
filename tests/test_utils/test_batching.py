"""Tests for batch processing utilities."""

from __future__ import annotations

import time
import threading

import pytest

from acme_sdk.utils.batching import BatchProcessor


class TestBatchProcessor:
    """Tests for the BatchProcessor."""

    def test_add_and_flush(self):
        exported: list[list[int]] = []
        processor = BatchProcessor(
            export_fn=lambda batch: exported.append(batch),
            batch_size=5,
            flush_interval=60.0,  # Long interval so we control flushing
        )

        for i in range(3):
            processor.add(i)

        assert processor.pending_count == 3
        flushed = processor.flush()
        assert flushed == 3
        assert exported == [[0, 1, 2]]
        processor.shutdown(timeout=1.0)

    def test_auto_flush_on_batch_size(self):
        exported: list[list[int]] = []
        processor = BatchProcessor(
            export_fn=lambda batch: exported.append(batch),
            batch_size=3,
            flush_interval=60.0,
        )

        for i in range(3):
            processor.add(i)

        # Should have auto-flushed
        assert len(exported) == 1
        assert exported[0] == [0, 1, 2]
        processor.shutdown(timeout=1.0)

    def test_add_many(self):
        exported: list[list[str]] = []
        processor = BatchProcessor(
            export_fn=lambda batch: exported.append(batch),
            batch_size=100,
            flush_interval=60.0,
        )

        added = processor.add_many(["a", "b", "c"])
        assert added == 3
        assert processor.pending_count == 3
        processor.shutdown(timeout=1.0)

    def test_buffer_overflow_drops(self):
        processor = BatchProcessor(
            export_fn=lambda batch: None,
            batch_size=100,
            flush_interval=60.0,
            max_queue_size=5,
        )

        for i in range(10):
            processor.add(i)

        stats = processor.stats
        assert stats["dropped"] > 0
        processor.shutdown(timeout=1.0)

    def test_shutdown_flushes_remaining(self):
        exported: list[list[int]] = []
        processor = BatchProcessor(
            export_fn=lambda batch: exported.append(batch),
            batch_size=100,
            flush_interval=60.0,
        )

        for i in range(5):
            processor.add(i)

        processor.shutdown(timeout=2.0)
        assert len(exported) == 1
        assert len(exported[0]) == 5

    def test_invalid_batch_size(self):
        with pytest.raises(ValueError, match="batch_size"):
            BatchProcessor(export_fn=lambda x: None, batch_size=0)

    def test_invalid_flush_interval(self):
        with pytest.raises(ValueError, match="flush_interval"):
            BatchProcessor(export_fn=lambda x: None, flush_interval=-1)

    def test_stats(self):
        processor = BatchProcessor(
            export_fn=lambda batch: None,
            batch_size=3,
            flush_interval=60.0,
        )

        for i in range(3):
            processor.add(i)

        stats = processor.stats
        assert stats["exported"] == 3
        assert stats["dropped"] == 0
        processor.shutdown(timeout=1.0)

    def test_cannot_add_after_shutdown(self):
        processor = BatchProcessor(
            export_fn=lambda batch: None,
            batch_size=100,
            flush_interval=60.0,
        )
        processor.shutdown(timeout=1.0)
        assert processor.add("item") is False
