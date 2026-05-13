"""Authentication providers for the Acme SDK."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class AuthProvider(ABC):
    """Base class for authentication providers.

    All authentication providers must implement the get_headers() method
    which returns a dictionary of HTTP headers to include in requests.
    """

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """Return authentication headers for an HTTP request.

        Returns:
            Dictionary of header name to header value.
        """
        ...

    @abstractmethod
    def is_valid(self) -> bool:
        """Check whether the current credentials are still valid.

        Returns:
            True if the credentials are valid and usable.
        """
        ...


class APIKeyAuth(AuthProvider):
    """Authentication using a static API key.

    The API key is sent as a Bearer token in the Authorization header.

    Args:
        api_key: The API key string.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("API key cannot be empty")
        self._api_key = api_key.strip()

    def get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def is_valid(self) -> bool:
        return bool(self._api_key)

    def __repr__(self) -> str:
        masked = self._api_key[:4] + "..." + self._api_key[-4:]
        return f"APIKeyAuth(api_key={masked!r})"


@dataclass
class OAuthToken:
    """Represents an OAuth 2.0 access token with expiry tracking."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    _obtained_at: float = 0.0

    def __post_init__(self) -> None:
        if self._obtained_at == 0.0:
            self._obtained_at = time.time()

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        if self.expires_in is None:
            return False
        elapsed = time.time() - self._obtained_at
        # Refresh 60 seconds before actual expiry to avoid edge cases
        return elapsed >= (self.expires_in - 60)


class OAuthProvider(AuthProvider):
    """OAuth 2.0 client credentials authentication.

    Automatically handles token acquisition and refresh using the
    client credentials grant type.

    Args:
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        token_url: URL of the token endpoint.
        scopes: Optional list of OAuth scopes to request.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str,
        scopes: Optional[list[str]] = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        if not token_url:
            raise ValueError("token_url is required")

        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._scopes = scopes or []
        self._token: Optional[OAuthToken] = None
        self._http_client = httpx.Client(timeout=httpx.Timeout(10.0))

    def get_headers(self) -> dict[str, str]:
        """Return OAuth bearer token headers.

        Automatically fetches or refreshes the token if needed.
        """
        token = self._ensure_valid_token()
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    def is_valid(self) -> bool:
        """Check if we have a non-expired token."""
        return self._token is not None and not self._token.is_expired

    def _ensure_valid_token(self) -> OAuthToken:
        """Ensure we have a valid, non-expired token."""
        if self._token is None or self._token.is_expired:
            self._token = self._fetch_token()
        return self._token

    def _fetch_token(self) -> OAuthToken:
        """Fetch a new token from the OAuth token endpoint."""
        logger.debug("Fetching new OAuth token from %s", self._token_url)

        data: dict[str, Any] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scopes:
            data["scope"] = " ".join(self._scopes)

        try:
            response = self._http_client.post(self._token_url, data=data)
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("OAuth token request failed: %s", exc.response.status_code)
            raise AuthenticationError(
                f"Failed to obtain OAuth token: {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("OAuth token request error: %s", exc)
            raise AuthenticationError(f"OAuth token request failed: {exc}") from exc

        token = OAuthToken(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=token_data.get("expires_in"),
            refresh_token=token_data.get("refresh_token"),
            scope=token_data.get("scope"),
        )
        logger.info("Successfully obtained OAuth token (expires_in=%s)", token.expires_in)
        return token

    def revoke(self) -> None:
        """Revoke the current token and clear cached credentials."""
        self._token = None
        logger.debug("OAuth token revoked")

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http_client.close()

    def __repr__(self) -> str:
        return f"OAuthProvider(client_id={self._client_id!r}, token_url={self._token_url!r})"


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass
