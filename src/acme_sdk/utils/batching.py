"""Batch processing utilities for the Acme SDK.

Provides a background batch processor that collects items and flushes
them in configurable intervals, used by exporters to reduce API calls.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 512
DEFAULT_FLUSH_INTERVAL = 5.0  # seconds
DEFAULT_MAX_QUEUE_SIZE = 2048


class BatchProcessor:
    """Collects items and flushes them in batches on a background thread.

    Parameters:
        flush_callback: Called with a list of items when a batch is ready.
        max_batch_size: Maximum items per flush.
        flush_interval: Seconds between automatic flushes.
        max_queue_size: Maximum items buffered before dropping.
    """

    def __init__(
        self,
        flush_callback: Callable[[list[Any]], None] | None = None,
        max_batch_size: int | None = None,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        *,
        export_fn: Callable[[list[Any]], None] | None = None,
        batch_size: int | None = None,
    ) -> None:
        if flush_callback is None:
            flush_callback = export_fn
        if flush_callback is None:
            raise ValueError("flush_callback or export_fn is required")
        if max_batch_size is None:
            max_batch_size = batch_size if batch_size is not None else DEFAULT_BATCH_SIZE
        if max_batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be greater than 0")
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be greater than 0")

        self._flush_callback = flush_callback
        self._max_batch_size = max_batch_size
        self._flush_interval = flush_interval
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._shutdown = False
        self._lock = threading.Lock()
        self._exported = 0
        self._dropped = 0

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        atexit.register(self.shutdown)

    def add(self, item: Any) -> bool:
        """Add an item to the batch queue. Returns False if queue is full."""
        if self._shutdown:
            return False
        try:
            self._queue.put_nowait(item)
            if self._queue.qsize() >= self._max_batch_size:
                self.flush()
            return True
        except queue.Full:
            self._dropped += 1
            logger.warning("Batch queue full, dropping item")
            return False

    def add_many(self, items: list[Any]) -> int:
        """Add multiple items and return the number accepted."""
        return sum(1 for item in items if self.add(item))

    @property
    def pending_count(self) -> int:
        """Number of items currently waiting in the queue."""
        return self._queue.qsize()

    @property
    def stats(self) -> dict[str, int]:
        """Return basic processor counters."""
        return {
            "pending": self.pending_count,
            "exported": self._exported,
            "dropped": self._dropped,
        }

    def _run(self) -> None:
        """Background loop: flush when batch is full or interval elapses."""
        while not self._shutdown:
            self.flush()
            time.sleep(self._flush_interval)

    def _drain_batch(self) -> list[Any]:
        """Drain up to max_batch_size items from the queue."""
        batch: list[Any] = []
        while len(batch) < self._max_batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def flush(self) -> int:
        """Flush one batch immediately and return the number of items exported."""
        batch = self._drain_batch()
        if not batch:
            return 0
        try:
            self._flush_callback(batch)
            self._exported += len(batch)
        except Exception:
            logger.exception("Flush callback failed for batch of %d items", len(batch))
        return len(batch)

    def shutdown(self, timeout: float | None = None) -> None:
        """Flush remaining items and stop the background thread."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True

        self.flush()

        if timeout is None:
            timeout = self._flush_interval * 3
        self._thread.join(timeout=timeout)


def _ensure_flush_on_shutdown(processor: BatchProcessor) -> None:
    """Ensure final flush happens before interpreter exit.

    This is registered as an atexit handler and ensures that the
    background flush thread completes before the interpreter exits.
    """
    if not processor._shutdown:
        processor.shutdown(timeout=processor._flush_interval * 3)
