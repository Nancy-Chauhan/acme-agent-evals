"""Batch export example for the Acme SDK.

This example demonstrates how to use the BatchProcessor for high-throughput
span collection. Spans are buffered and flushed in configurable batches,
either when the batch size is reached or on a timer.
"""

import time
import random

from acme_sdk import AcmeClient
from acme_sdk.models import Span, SpanKind, SpanStatus
from acme_sdk.exporters.otlp import OTLPExporter
from acme_sdk.utils.batching import BatchProcessor


def simulate_request() -> Span:
    """Simulate an HTTP request and return a span."""
    endpoints = [
        "/api/v1/users",
        "/api/v1/orders",
        "/api/v1/products",
        "/api/v1/inventory",
        "/api/v1/payments",
    ]
    methods = ["GET", "POST", "PUT", "DELETE"]
    status_codes = [200, 200, 200, 201, 204, 400, 404, 500]

    endpoint = random.choice(endpoints)
    method = random.choice(methods)
    status = random.choice(status_codes)
    duration = random.uniform(5, 500)

    span = Span(
        name=f"{method} {endpoint}",
        service_name="api-gateway",
        kind=SpanKind.SERVER,
        duration_ms=duration,
        status=SpanStatus.ERROR if status >= 500 else SpanStatus.OK,
        attributes={
            "http.method": method,
            "http.url": endpoint,
            "http.status_code": status,
            "http.response_size": random.randint(100, 10000),
        },
    )
    span.finish()
    return span


def main():
    # Initialize client and exporter
    client = AcmeClient(
        api_key="your-api-key-here",
        endpoint="https://ingest.acme-sdk.dev",
    )
    exporter = OTLPExporter(client=client)

    # Create a batch processor that flushes every 100 spans or every 2 seconds
    processor = BatchProcessor(
        export_fn=lambda batch: exporter.export(batch),
        batch_size=100,
        flush_interval=2.0,
        max_queue_size=5000,
    )

    print("Starting batch export simulation...")
    print(f"Batch size: 100, Flush interval: 2s")
    print()

    # Simulate 500 requests
    total_requests = 500
    for i in range(total_requests):
        span = simulate_request()
        processor.add(span)

        # Simulate varying request rates
        time.sleep(random.uniform(0.001, 0.01))

        if (i + 1) % 100 == 0:
            stats = processor.stats
            print(
                f"  Processed {i + 1}/{total_requests} requests "
                f"(exported: {stats['exported']}, "
                f"pending: {stats['pending']}, "
                f"dropped: {stats['dropped']})"
            )

    # Shutdown and flush remaining
    print("\nShutting down batch processor...")
    processor.shutdown(timeout=5.0)

    final_stats = processor.stats
    print(f"\nFinal stats:")
    print(f"  Total exported: {final_stats['exported']}")
    print(f"  Total dropped:  {final_stats['dropped']}")

    # Show exporter stats too
    exporter_stats = exporter.stats
    print(f"  Export calls:   {exporter_stats['total_exports']}")
    print(f"  Export errors:  {exporter_stats['total_errors']}")

    client.close()


if __name__ == "__main__":
    main()
