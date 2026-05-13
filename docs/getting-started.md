# Getting Started with the Acme SDK

This guide will help you install and configure the Acme SDK for Python and send your first traces to the Acme Observability Platform.

## Prerequisites

- Python 3.9 or later
- An Acme platform account with an API key
- pip (or your preferred Python package manager)

## Installation

Install the SDK using pip:

```bash
pip install acme-sdk
```

For projects that need gRPC transport (lower latency, better for high-throughput):

```bash
pip install acme-sdk[grpc]
```

## Getting Your API Key

1. Log in to the [Acme Dashboard](https://app.acme-sdk.dev)
2. Navigate to **Settings > API Keys**
3. Click **Create New Key**
4. Copy the key and store it securely

> **Security Note:** Never commit API keys to version control. Use environment variables or a secrets manager.

## Your First Trace

Here's a minimal example that creates a span and exports it to the Acme platform:

```python
from acme_sdk import AcmeClient
from acme_sdk.models import Span, SpanKind

# Initialize with your API key
client = AcmeClient(api_key="your-api-key-here")

# Create a span representing some work
span = Span(
    name="process_order",
    service_name="order-service",
    kind=SpanKind.SERVER,
    attributes={
        "order.id": "ORD-12345",
        "order.total": 99.99,
        "customer.tier": "premium",
    },
)

# Mark the span as completed
span.finish()

# Send it to Acme
result = client.send_spans([span])
print(f"Sent {result.get('span_count', 0)} spans")

client.close()
```

## Using Environment Variables

For production deployments, configure the SDK via environment variables:

```bash
export ACME_API_KEY="your-api-key-here"
export ACME_ENDPOINT="https://ingest.acme-sdk.dev"
export ACME_COMPRESSION=true
```

```python
from acme_sdk import AcmeClient
from acme_sdk.config import AcmeConfig

config = AcmeConfig.from_env()
client = AcmeClient(config=config)
```

## Using a Config File

Create an `acme.toml` configuration file:

```toml
[acme]
api_key = "${ACME_API_KEY}"
endpoint = "https://ingest.acme-sdk.dev"
timeout = 30
compression = true
batch_size = 512
```

```python
from acme_sdk.config import AcmeConfig
from acme_sdk import AcmeClient

config = AcmeConfig.from_file("acme.toml")
client = AcmeClient(config=config)
```

## Creating Traces with Multiple Spans

Most real-world operations involve multiple spans forming a trace:

```python
from acme_sdk.models import Trace, SpanKind

trace = Trace(service_name="order-service")

# Root span for the overall request
root = trace.add_span("handle_request", kind=SpanKind.SERVER)

# Child span for database access
db_span = trace.add_span(
    "query_inventory",
    kind=SpanKind.CLIENT,
    parent_span_id=root.span_id,
    attributes={"db.system": "postgresql", "db.operation": "SELECT"},
)
db_span.finish()

# Child span for an external API call
api_span = trace.add_span(
    "call_payment_api",
    kind=SpanKind.CLIENT,
    parent_span_id=root.span_id,
    attributes={"http.method": "POST", "http.url": "https://payments.example.com"},
)
api_span.finish()

root.finish()

# Export all spans in the trace
client.send_spans(trace.spans)
```

## Next Steps

- [Configuration Reference](configuration.md) — Full list of configuration options
- [Exporters Guide](exporters.md) — Learn about different export backends
- [API Reference](api-reference.md) — Detailed API documentation
