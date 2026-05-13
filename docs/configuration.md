# Configuration Reference

The Acme SDK can be configured through code, environment variables, or config files. This page documents all available options.

## Configuration Precedence

When multiple configuration sources are used, values are resolved in this order (highest priority first):

1. Constructor arguments passed directly to `AcmeClient`
2. Environment variables (`ACME_*`)
3. Config file values
4. Built-in defaults

## Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ACME_API_KEY` | string | — | API key for authentication |
| `ACME_ENDPOINT` | string | `https://ingest.acme-sdk.dev` | Base URL for the Acme ingest API |
| `ACME_TIMEOUT` | float | `30.0` | Request timeout in seconds |
| `ACME_COMPRESSION` | bool | `true` | Enable gzip compression |
| `ACME_BATCH_SIZE` | int | `512` | Maximum spans per export batch |
| `ACME_MAX_RETRIES` | int | `3` | Maximum retry attempts for failed requests |
| `ACME_LOG_LEVEL` | string | `WARNING` | SDK log level (DEBUG, INFO, WARNING, ERROR) |

Boolean values accept: `true`, `1`, `yes` (truthy) or `false`, `0`, `no` (falsy).

## Config File Format

### TOML

```toml
[acme]
api_key = "${ACME_API_KEY}"
endpoint = "https://ingest.acme-sdk.dev"
timeout = 30
compression = true
batch_size = 512
max_retries = 3
log_level = "WARNING"
```

### YAML

```yaml
acme:
  api_key: "${ACME_API_KEY}"
  endpoint: "https://ingest.acme-sdk.dev"
  timeout: 30
  compression: true
  batch_size: 512
  max_retries: 3
  log_level: "WARNING"
```

## Environment Variable Interpolation

Config files support environment variable substitution using `${VAR_NAME}` syntax:

```toml
[acme]
api_key = "${ACME_API_KEY}"
endpoint = "${ACME_ENDPOINT:-https://ingest.acme-sdk.dev}"
```

The `:-` syntax provides a default value if the variable is not set.

## AcmeConfig API

```python
from acme_sdk.config import AcmeConfig

# From environment
config = AcmeConfig.from_env()

# From file
config = AcmeConfig.from_file("acme.toml")

# Merge with overrides
prod_config = config.merge({"timeout": 60, "max_retries": 5})

# Direct construction
config = AcmeConfig(
    api_key="your-key",
    endpoint="https://ingest.acme-sdk.dev",
    timeout=30.0,
    compression=True,
    batch_size=512,
    max_retries=3,
)
```

## Retry Configuration

The SDK automatically retries requests that fail with transient errors. Retry behavior is controlled by:

- **max_retries**: Maximum number of retry attempts (default: 3)
- Retries use exponential backoff with jitter
- Retryable HTTP status codes: 429, 502, 503, 504
- Connection errors and timeouts are also retried
- The `Retry-After` header is respected when present

## Troubleshooting

### Connection Timeouts

If you're experiencing timeouts, try increasing the timeout value:

```python
client = AcmeClient(api_key="...", timeout=60.0)
```

### Debug Logging

Enable debug logging to see detailed request/response information:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("acme_sdk").setLevel(logging.DEBUG)
```

### Proxy Configuration

The SDK uses httpx, which respects standard proxy environment variables:

```bash
export HTTPS_PROXY="http://proxy.example.com:8080"
```
