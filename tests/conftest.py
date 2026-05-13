"""Shared test fixtures for the Acme SDK test suite."""

from __future__ import annotations

import pytest
import httpx
import respx

from acme_sdk.client import AcmeClient
from acme_sdk.auth import APIKeyAuth, OAuthProvider
from acme_sdk.config import AcmeConfig
from acme_sdk.models import Span, Event, Metric, Trace, SpanKind, SpanStatus, MetricType


TEST_API_KEY = "test-api-key-1234567890"
TEST_ENDPOINT = "https://ingest.acme-sdk.dev"


@pytest.fixture
def api_key() -> str:
    return TEST_API_KEY


@pytest.fixture
def config(api_key: str) -> AcmeConfig:
    return AcmeConfig(
        api_key=api_key,
        endpoint=TEST_ENDPOINT,
        timeout=5.0,
        compression=False,
        batch_size=100,
        max_retries=1,
    )


@pytest.fixture
def mock_api():
    """Mock the Acme API endpoints using respx."""
    with respx.mock(base_url=TEST_ENDPOINT, assert_all_called=False) as mock:
        # Health check
        mock.get("/health").respond(200, json={"status": "ok"})

        # Span ingestion
        mock.post("/v1/spans").respond(
            202,
            json={"accepted": True, "span_count": 1},
        )

        # Event ingestion
        mock.post("/v1/events").respond(
            202,
            json={"accepted": True, "event_count": 1},
        )

        # Metrics ingestion
        mock.post("/v1/metrics").respond(
            202,
            json={"accepted": True, "metric_count": 1},
        )

        yield mock


@pytest.fixture
def client(config: AcmeConfig, mock_api) -> AcmeClient:
    """Create a test client with mocked API."""
    c = AcmeClient(config=config)
    yield c
    c.close()


@pytest.fixture
def sample_span() -> Span:
    return Span(
        name="test-operation",
        service_name="test-service",
        kind=SpanKind.SERVER,
        duration_ms=42.0,
        attributes={"http.method": "GET", "http.status_code": 200},
    )


@pytest.fixture
def sample_event() -> Event:
    return Event(
        name="test-event",
        attributes={"key": "value"},
    )


@pytest.fixture
def sample_metric() -> Metric:
    return Metric(
        name="test.request.duration",
        value=150.5,
        metric_type=MetricType.HISTOGRAM,
        unit="ms",
        tags={"endpoint": "/api/v1/data"},
        service_name="test-service",
    )


@pytest.fixture
def sample_trace() -> Trace:
    trace = Trace(service_name="test-service")
    root = trace.add_span("root-operation", kind=SpanKind.SERVER)
    child = trace.add_span(
        "child-operation",
        kind=SpanKind.CLIENT,
        parent_span_id=root.span_id,
    )
    return trace
