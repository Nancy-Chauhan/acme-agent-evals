"""Tests for the AcmeClient HTTP client."""

from __future__ import annotations

import pytest
import httpx
import respx

from acme_sdk.client import AcmeClient, DEFAULT_ENDPOINT, DEFAULT_TIMEOUT
from acme_sdk.config import AcmeConfig
from acme_sdk.models import Span, SpanKind


class TestClientInitialization:
    """Tests for client construction and configuration."""

    def test_create_with_api_key(self):
        client = AcmeClient(api_key="test-key-12345678")
        assert client._endpoint == DEFAULT_ENDPOINT
        assert client._timeout == DEFAULT_TIMEOUT
        client.close()

    def test_create_with_config(self):
        config = AcmeConfig(
            api_key="test-key-12345678",
            endpoint="https://custom.endpoint.dev",
            timeout=10.0,
        )
        client = AcmeClient(config=config)
        assert client._endpoint == "https://custom.endpoint.dev"
        assert client._timeout == 10.0
        client.close()

    def test_create_without_auth_raises(self):
        with pytest.raises(ValueError, match="Either api_key"):
            AcmeClient()

    def test_create_with_both_auth_raises(self):
        from acme_sdk.auth import APIKeyAuth

        with pytest.raises(ValueError, match="Cannot specify both"):
            AcmeClient(
                api_key="test-key-12345678",
                auth_provider=APIKeyAuth("other-key-12345678"),
            )

    def test_context_manager(self):
        with AcmeClient(api_key="test-key-12345678") as client:
            assert client is not None

    def test_repr(self):
        client = AcmeClient(api_key="test-key-12345678")
        repr_str = repr(client)
        assert "AcmeClient" in repr_str
        assert DEFAULT_ENDPOINT in repr_str
        client.close()


class TestClientRequests:
    """Tests for client HTTP operations."""

    def test_send_spans(self, client, sample_span):
        result = client.send_spans([sample_span])
        assert result["accepted"] is True

    def test_send_events(self, client, sample_event):
        result = client.send_events([sample_event])
        assert result["accepted"] is True

    def test_send_metrics(self, client, sample_metric):
        result = client.send_metrics([sample_metric])
        assert result["accepted"] is True

    def test_health_check_success(self, client):
        assert client.health_check() is True

    @respx.mock(base_url="https://ingest.acme-sdk.dev")
    def test_health_check_failure(self, respx_mock):
        respx_mock.get("/health").respond(503)
        client = AcmeClient(api_key="test-key-12345678")
        # Even a 503 returns a response, not an exception
        # health_check returns True only for 200
        assert client.health_check() is False
        client.close()

    @respx.mock(base_url="https://ingest.acme-sdk.dev")
    def test_send_spans_server_error(self, respx_mock):
        respx_mock.post("/v1/spans").respond(500)
        client = AcmeClient(api_key="test-key-12345678", max_retries=0)
        span = Span(name="test", service_name="svc", duration_ms=1.0)

        with pytest.raises(httpx.HTTPStatusError):
            client.send_spans([span])
        client.close()
