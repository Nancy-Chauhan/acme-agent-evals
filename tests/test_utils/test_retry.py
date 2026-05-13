"""Tests for retry logic."""

from __future__ import annotations

import pytest
import httpx

from acme_sdk.utils.retry import RetryConfig, retry_with_backoff


class TestRetryConfig:
    """Tests for RetryConfig."""

    def test_default_config(self):
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0

    def test_invalid_max_retries(self):
        with pytest.raises(ValueError, match="non-negative"):
            RetryConfig(max_retries=-1)

    def test_invalid_base_delay(self):
        with pytest.raises(ValueError, match="positive"):
            RetryConfig(base_delay=0)

    def test_invalid_max_delay(self):
        with pytest.raises(ValueError, match="max_delay"):
            RetryConfig(base_delay=10.0, max_delay=1.0)

    def test_invalid_jitter_factor(self):
        with pytest.raises(ValueError, match="jitter_factor"):
            RetryConfig(jitter_factor=2.0)

    def test_compute_delay_exponential(self):
        config = RetryConfig(
            base_delay=1.0,
            backoff_multiplier=2.0,
            jitter_factor=0.0,
        )
        assert config.compute_delay(0) == 1.0
        assert config.compute_delay(1) == 2.0
        assert config.compute_delay(2) == 4.0

    def test_compute_delay_capped(self):
        config = RetryConfig(
            base_delay=1.0,
            max_delay=5.0,
            backoff_multiplier=2.0,
            jitter_factor=0.0,
        )
        assert config.compute_delay(10) == 5.0


class TestRetryWithBackoff:
    """Tests for the retry_with_backoff function."""

    def test_succeeds_on_first_attempt(self):
        result = retry_with_backoff(lambda: "success")
        assert result == "success"

    def test_retries_on_failure_then_succeeds(self):
        attempts = [0]

        def flaky():
            attempts[0] += 1
            if attempts[0] < 3:
                response = httpx.Response(503, request=httpx.Request("POST", "http://test"))
                raise httpx.HTTPStatusError(
                    "Service Unavailable",
                    request=response.request,
                    response=response,
                )
            return "success"

        result = retry_with_backoff(flaky, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert attempts[0] == 3

    def test_non_retryable_status_raises_immediately(self):
        def bad_request():
            response = httpx.Response(400, request=httpx.Request("POST", "http://test"))
            raise httpx.HTTPStatusError(
                "Bad Request",
                request=response.request,
                response=response,
            )

        with pytest.raises(httpx.HTTPStatusError):
            retry_with_backoff(bad_request, max_retries=3)

    def test_exhausted_retries_raises(self):
        def always_fails():
            response = httpx.Response(503, request=httpx.Request("POST", "http://test"))
            raise httpx.HTTPStatusError(
                "Service Unavailable",
                request=response.request,
                response=response,
            )

        with pytest.raises(httpx.HTTPStatusError):
            retry_with_backoff(always_fails, max_retries=1, base_delay=0.01)

    def test_on_retry_callback(self):
        callbacks: list[tuple[int, float]] = []

        def always_fails():
            response = httpx.Response(503, request=httpx.Request("POST", "http://test"))
            raise httpx.HTTPStatusError(
                "fail",
                request=response.request,
                response=response,
            )

        def on_retry(attempt, exc, delay):
            callbacks.append((attempt, delay))

        with pytest.raises(httpx.HTTPStatusError):
            retry_with_backoff(
                always_fails,
                max_retries=2,
                base_delay=0.01,
                on_retry=on_retry,
            )

        assert len(callbacks) == 2
