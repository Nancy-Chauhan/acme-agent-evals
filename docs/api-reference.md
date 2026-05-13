# API Reference

## acme_sdk.client

### AcmeClient

The main HTTP client for communicating with the Acme Observability Platform.

```python
class AcmeClient(
    api_key: str | None = None,
    auth_provider: AuthProvider | None = None,
    endpoint: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    compression: bool = True,
    config: AcmeConfig | None = None,
)
```

**Methods:**

| Method | Description |
|--------|-------------|
| `send_spans(spans)` | Send a batch of spans to the platform |
| `send_events(events)` | Send a batch of events to the platform |
| `send_metrics(metrics)` | Send a batch of metrics to the platform |
| `health_check()` | Check if the platform is reachable |
| `close()` | Close the HTTP client |

## acme_sdk.models

### Span

Represents a single unit of work in a distributed trace.

```python
class Span(BaseModel):
    span_id: str           # Auto-generated 16-char hex
    trace_id: str | None   # Auto-generated 32-char hex
    parent_span_id: str | None
    name: str
    service_name: str
    kind: SpanKind         # Default: INTERNAL
    status: SpanStatus     # Default: UNSET
    start_time: datetime   # Default: now(UTC)
    end_time: datetime | None
    duration_ms: float | None
    attributes: dict[str, Any]
    events: list[Event]
    resource_attributes: dict[str, Any]
```

**Methods:**

| Method | Description |
|--------|-------------|
| `add_event(name, attributes)` | Add an event to the span |
| `set_status(status, description)` | Set the span status |
| `finish()` | Mark the span as finished |

### Trace

A distributed trace composed of multiple spans.

```python
class Trace(BaseModel):
    trace_id: str
    spans: list[Span]
    service_name: str
    attributes: dict[str, Any]
```

**Properties:**

| Property | Description |
|----------|-------------|
| `root_span` | The root span (no parent) |
| `duration_ms` | Total trace duration |
| `span_count` | Number of spans |

### Event

A timestamped event within a span.

```python
class Event(BaseModel):
    name: str
    timestamp: datetime
    attributes: dict[str, Any]
```

### Metric

A metric data point.

```python
class Metric(BaseModel):
    name: str
    value: float
    metric_type: MetricType  # Default: GAUGE
    timestamp: datetime
    unit: str | None
    tags: dict[str, str]
    service_name: str | None
    description: str | None
```

### Enums

| Enum | Values |
|------|--------|
| `SpanKind` | `INTERNAL`, `SERVER`, `CLIENT`, `PRODUCER`, `CONSUMER` |
| `SpanStatus` | `UNSET`, `OK`, `ERROR` |
| `MetricType` | `COUNTER`, `GAUGE`, `HISTOGRAM`, `SUMMARY` |

## acme_sdk.auth

### APIKeyAuth

```python
class APIKeyAuth(AuthProvider):
    def __init__(self, api_key: str) -> None: ...
    def get_headers(self) -> dict[str, str]: ...
    def is_valid(self) -> bool: ...
```

### OAuthProvider

```python
class OAuthProvider(AuthProvider):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scopes: list[str] | None = None,
    ) -> None: ...
    def get_headers(self) -> dict[str, str]: ...
    def is_valid(self) -> bool: ...
    def revoke(self) -> None: ...
    def close(self) -> None: ...
```

## acme_sdk.config

### AcmeConfig

```python
@dataclass
class AcmeConfig:
    api_key: str | None = None
    endpoint: str = "https://ingest.acme-sdk.dev"
    timeout: float = 30.0
    compression: bool = True
    batch_size: int = 512
    max_retries: int = 3
    log_level: str = "WARNING"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AcmeConfig: ...
    @classmethod
    def from_file(cls, path: str | Path) -> AcmeConfig: ...
    def merge(self, overrides: dict) -> AcmeConfig: ...
```

## acme_sdk.utils.retry

### retry_with_backoff

```python
def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    retryable_status_codes: set[int] | None = None,
    on_retry: Callable | None = None,
) -> T: ...
```

## acme_sdk.utils.batching

### BatchProcessor

```python
class BatchProcessor(Generic[T]):
    def __init__(
        self,
        export_fn: Callable[[list[T]], Any],
        batch_size: int = 512,
        flush_interval: float = 5.0,
        max_queue_size: int = 10000,
    ) -> None: ...
    def add(self, item: T) -> bool: ...
    def add_many(self, items: Sequence[T]) -> int: ...
    def flush(self) -> int: ...
    def shutdown(self, timeout: float | None = None) -> None: ...
```
