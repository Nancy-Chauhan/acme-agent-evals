# Exporters Guide

The Acme SDK ships with three built-in exporters for different use cases. You can also implement custom exporters by following the exporter protocol.

## OTLP Exporter

The **OTLPExporter** sends telemetry data over HTTP using the OpenTelemetry Protocol. This is the recommended exporter for production deployments.

### Features

- HTTP/1.1 and HTTP/2 transport
- Gzip compression (enabled by default)
- Automatic batch chunking for large payloads
- Retry on transient failures

### Usage

```python
from acme_sdk import AcmeClient
from acme_sdk.exporters.otlp import OTLPExporter

client = AcmeClient(api_key="your-key")
exporter = OTLPExporter(
    client=client,
    compression=True,
    max_batch_size=1000,
)

result = exporter.export(spans)
if result.success:
    print(f"Exported {result.exported_count} spans")
else:
    print(f"Export errors: {result.errors}")
```

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `client` | AcmeClient | required | HTTP client instance |
| `compression` | bool | `True` | Enable gzip compression |
| `max_batch_size` | int | `1000` | Maximum items per request |
| `headers` | dict | `None` | Additional HTTP headers |

## JSON File Exporter

The **JSONFileExporter** writes telemetry data to local JSON files. Useful for development, debugging, and offline analysis.

### Features

- Automatic file rotation by size
- Pretty-print option for readability
- Configurable output directory and file naming
- Built-in cleanup for old files

### Usage

```python
from acme_sdk.exporters.json_file import JSONFileExporter

exporter = JSONFileExporter(
    output_dir="./traces",
    pretty_print=True,
    max_file_size_mb=50,
)

exporter.export(spans)

# List output files
for f in exporter.list_output_files():
    print(f)

# Clean up files older than 24 hours
removed = exporter.cleanup(max_age_hours=24)
```

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output_dir` | str/Path | `./acme_traces` | Output directory |
| `max_file_size_mb` | int | `100` | Max file size before rotation |
| `pretty_print` | bool | `False` | Indent JSON output |
| `filename_prefix` | str | `acme_traces` | Prefix for filenames |

## Console Exporter

The **ConsoleExporter** prints telemetry data to stdout in a human-readable format. Great for development and debugging.

### Features

- Colorized output with ANSI codes
- Verbose mode shows all attributes
- JSON output mode for machine-readable output
- Configurable output stream

### Usage

```python
from acme_sdk.exporters.console import ConsoleExporter

exporter = ConsoleExporter(colorize=True, verbose=True)
exporter.export(spans)
```

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output` | TextIO | `sys.stdout` | Output stream |
| `colorize` | bool | `True` | Use ANSI colors |
| `verbose` | bool | `False` | Show all attributes |
| `json_output` | bool | `False` | Output as JSON |

## Custom Exporters

You can create custom exporters by implementing the export interface:

```python
from acme_sdk.models import Span
from typing import Sequence

class MyCustomExporter:
    def export(self, spans: Sequence[Span]) -> int:
        for span in spans:
            # Your custom export logic here
            send_to_custom_backend(span.model_dump())
        return len(spans)
```

## Combining Exporters

You can use multiple exporters simultaneously:

```python
exporters = [
    OTLPExporter(client=client),
    ConsoleExporter(verbose=True),
    JSONFileExporter(output_dir="./debug_traces"),
]

for exporter in exporters:
    exporter.export(spans)
```
