"""Basic tracing example with the Acme SDK.

This example demonstrates how to create traces and spans and export them
to the Acme platform using the OTLP exporter.
"""

from acme_sdk import AcmeClient
from acme_sdk.models import Trace, Span, SpanKind, SpanStatus, Event
from acme_sdk.exporters.otlp import OTLPExporter


def main():
    # Initialize the client
    client = AcmeClient(
        api_key="your-api-key-here",
        endpoint="https://ingest.acme-sdk.dev",
    )

    # Create a trace for an HTTP request
    trace = Trace(
        service_name="order-service",
        attributes={"deployment.environment": "production"},
    )

    # Root span: handling the incoming request
    root_span = trace.add_span(
        "POST /api/v1/orders",
        kind=SpanKind.SERVER,
        attributes={
            "http.method": "POST",
            "http.url": "/api/v1/orders",
            "http.target": "/api/v1/orders",
        },
    )

    # Child span: validating the order
    validate_span = trace.add_span(
        "validate_order",
        kind=SpanKind.INTERNAL,
        parent_span_id=root_span.span_id,
        attributes={"order.item_count": 3},
    )
    validate_span.set_status(SpanStatus.OK)
    validate_span.finish()

    # Child span: querying the database
    db_span = trace.add_span(
        "SELECT inventory",
        kind=SpanKind.CLIENT,
        parent_span_id=root_span.span_id,
        attributes={
            "db.system": "postgresql",
            "db.statement": "SELECT * FROM inventory WHERE product_id IN (...)",
            "db.operation": "SELECT",
        },
    )
    db_span.set_status(SpanStatus.OK)
    db_span.finish()

    # Child span: calling payment service
    payment_span = trace.add_span(
        "POST payments.internal/charge",
        kind=SpanKind.CLIENT,
        parent_span_id=root_span.span_id,
        attributes={
            "http.method": "POST",
            "http.url": "https://payments.internal/charge",
            "http.status_code": 200,
        },
    )
    payment_span.add_event("payment_authorized", {"amount": 149.99, "currency": "USD"})
    payment_span.set_status(SpanStatus.OK)
    payment_span.finish()

    # Finish the root span
    root_span.set_status(SpanStatus.OK)
    root_span.attributes["http.status_code"] = 201
    root_span.finish()

    # Export the trace
    exporter = OTLPExporter(client=client)
    result = exporter.export(trace.spans)

    print(f"Trace ID: {trace.trace_id}")
    print(f"Spans exported: {result.exported_count}")
    print(f"Export success: {result.success}")

    client.close()


if __name__ == "__main__":
    main()
