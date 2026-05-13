"""Retry logic with exponential backoff and jitter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Optional, Set, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_BASE_DELAY = 0.5  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_JITTER_FACTOR = 0.5
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class RetryConfig:
    """Configuration for retry behavior.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        backoff_multiplier: Multiplier applied to delay after each attempt.
        jitter_factor: Random jitter factor (0.0 to 1.0) to prevent thundering herd.
        retryable_status_codes: Set of HTTP status codes that should trigger a retry.
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
        jitter_factor: float = DEFAULT_JITTER_FACTOR,
        retryable_status_codes: Optional[Set[int]] = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if base_delay <= 0:
            raise ValueError("base_delay must be positive")
        if max_delay < base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if not 0.0 <= jitter_factor <= 1.0:
            raise ValueError("jitter_factor must be between 0.0 and 1.0")

        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.jitter_factor = jitter_factor
        self.retryable_status_codes = retryable_status_codes or DEFAULT_RETRYABLE_STATUS_CODES

    def compute_delay(self, attempt: int) -> float:
        """Compute the delay for a given retry attempt.

        Uses exponential backoff with decorrelated jitter.

        Args:
            attempt: The current attempt number (0-indexed).

        Returns:
            Delay in seconds before the next retry.
        """
        # Exponential backoff
        delay = self.base_delay * (self.backoff_multiplier ** attempt)

        # Cap at max delay
        delay = min(delay, self.max_delay)

        # Add jitter: uniform random between delay * (1 - jitter) and delay * (1 + jitter)
        if self.jitter_factor > 0:
            jitter_range = delay * self.jitter_factor
            delay = delay + random.uniform(-jitter_range, jitter_range)

        return max(0, delay)


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    retryable_status_codes: Optional[Set[int]] = None,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> T:
    """Execute a function with automatic retry and exponential backoff.

    Retries on transient HTTP errors (specific status codes) and connection
    errors. Non-retryable errors are raised immediately.

    Args:
        func: Callable to execute. Should raise httpx exceptions on failure.
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay between retries.
        max_delay: Maximum delay between retries.
        retryable_status_codes: HTTP status codes that trigger a retry.
        on_retry: Optional callback invoked before each retry with
            (attempt_number, exception, delay).

    Returns:
        The return value of func.

    Raises:
        The last exception encountered after all retries are exhausted.
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_status_codes=retryable_status_codes or DEFAULT_RETRYABLE_STATUS_CODES,
    )

    last_exception: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):
        try:
            return func()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in config.retryable_status_codes:
                raise  # Non-retryable status code

            last_exception = exc
            if attempt < config.max_retries:
                delay = _get_retry_delay(exc, config, attempt)
                logger.warning(
                    "Request failed with status %d, retrying in %.2fs (attempt %d/%d)",
                    exc.response.status_code,
                    delay,
                    attempt + 1,
                    config.max_retries,
                )
                if on_retry:
                    on_retry(attempt, exc, delay)
                time.sleep(delay)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exception = exc
            if attempt < config.max_retries:
                delay = config.compute_delay(attempt)
                logger.warning(
                    "Connection error: %s, retrying in %.2fs (attempt %d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    config.max_retries,
                )
                if on_retry:
                    on_retry(attempt, exc, delay)
                time.sleep(delay)

    # All retries exhausted
    assert last_exception is not None
    logger.error("All %d retry attempts exhausted", config.max_retries)
    raise last_exception


def _get_retry_delay(
    exc: httpx.HTTPStatusError,
    config: RetryConfig,
    attempt: int,
) -> float:
    """Get the retry delay, respecting Retry-After header if present."""
    retry_after = exc.response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass  # Not a numeric Retry-After, fall through to computed delay

    return config.compute_delay(attempt)
