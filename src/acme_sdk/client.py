"""Main HTTP client for the Acme Observability Platform."""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import httpx

from acme_sdk.auth import AuthProvider, APIKeyAuth
from acme_sdk.config import AcmeConfig
from acme_sdk.models import Span, Trace, Event, Metric
from acme_sdk.utils.retry import retry_with_backoff
from acme_sdk.utils.serialization import serialize_spans, serialize_events, serialize_metrics

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://ingest.acme-sdk.dev"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3


class AcmeClient:
    """HTTP client for sending telemetry data to the Acme platform.

    Supports API key and OAuth authentication, automatic retries with
    exponential backoff, and configurable timeouts.

    Args:
        api_key: API key for authentication. Mutually exclusive with auth_provider.
        auth_provider: Custom authentication provider. Mutually exclusive with api_key.
        endpoint: Base URL for the Acme ingest API.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts for failed requests.
        compression: Whether to enable gzip compression for request bodies.
        config: Optional AcmeConfig instance. If provided, other parameters
            are used as overrides.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        auth_provider: Optional[AuthProvider] = None,
        endpoint: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        compression: bool = True,
        config: Optional[AcmeConfig] = None,
    ) -> None:
        if api_key and auth_provider:
            raise ValueError("Cannot specify both api_key and auth_provider")

        if config:
            self._endpoint = endpoint or config.endpoint or DEFAULT_ENDPOINT
            self._timeout = timeout or config.timeout or DEFAULT_TIMEOUT
            self._max_retries = max_retries if max_retries is not None else config.max_retries
            self._compression = compression if compression is not None else config.compression
        else:
            self._endpoint = endpoint or DEFAULT_ENDPOINT
            self._timeout = timeout or DEFAULT_TIMEOUT
            self._max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
            self._compression = compression

        if auth_provider:
            self._auth = auth_provider
        elif api_key:
            self._auth = APIKeyAuth(api_key)
        elif config and config.api_key:
            self._auth = APIKeyAuth(config.api_key)
        else:
            raise ValueError("Either api_key, auth_provider, or config with api_key is required")

        self._client = httpx.Client(
            base_url=self._endpoint.rstrip("/"),
            timeout=httpx.Timeout(self._timeout),
            headers=self._build_default_headers(),
        )
        logger.info("AcmeClient initialized (endpoint=%s)", self._endpoint)

    def _build_default_headers(self) -> dict[str, str]:
        """Build default headers for all requests."""
        headers = {
            "User-Agent": "acme-agent-evals/1.1.0",
            "Content-Type": "application/json",
        }
        if self._compression:
            headers["Content-Encoding"] = "gzip"
            headers["Accept-Encoding"] = "gzip"
        return headers

    def _prepare_request(self, path: str, payload: dict[str, Any]) -> httpx.Request:
        """Prepare an authenticated request."""
        headers = self._auth.get_headers()
        return self._client.build_request(
            method="POST",
            url=path,
            json=payload,
            headers=headers,
        )

    def send_spans(self, spans: Sequence[Span]) -> dict[str, Any]:
        """Send a batch of spans to the Acme platform.

        Args:
            spans: Sequence of Span objects to export.

        Returns:
            Response data from the API.

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx status code
                after all retry attempts are exhausted.
        """
        payload = serialize_spans(spans)
        return self._send("/v1/spans", payload)

    def send_events(self, events: Sequence[Event]) -> dict[str, Any]:
        """Send a batch of events to the Acme platform.

        Args:
            events: Sequence of Event objects to export.

        Returns:
            Response data from the API.
        """
        payload = serialize_events(events)
        return self._send("/v1/events", payload)

    def send_metrics(self, metrics: Sequence[Metric]) -> dict[str, Any]:
        """Send a batch of metrics to the Acme platform.

        Args:
            metrics: Sequence of Metric objects to export.

        Returns:
            Response data from the API.
        """
        payload = serialize_metrics(metrics)
        return self._send("/v1/metrics", payload)

    def _send(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a request with automatic retry logic.

        Uses exponential backoff with jitter for transient failures.
        """

        def _do_request() -> dict[str, Any]:
            request = self._prepare_request(path, payload)
            response = self._client.send(request)
            response.raise_for_status()
            return response.json()

        return retry_with_backoff(
            _do_request,
            max_retries=self._max_retries,
            retryable_status_codes={429, 502, 503, 504},
        )

    def health_check(self) -> bool:
        """Check if the Acme platform is reachable.

        Returns:
            True if the platform responds with a 200 status code.
        """
        try:
            response = self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        self._client.close()
        logger.debug("AcmeClient closed")

    def __enter__(self) -> "AcmeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"AcmeClient(endpoint={self._endpoint!r}, "
            f"timeout={self._timeout}, "
            f"compression={self._compression})"
        )
