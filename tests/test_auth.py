"""Tests for authentication providers."""

from __future__ import annotations

import pytest
import respx
import httpx

from acme_sdk.auth import APIKeyAuth, OAuthProvider, OAuthToken, AuthenticationError


class TestAPIKeyAuth:
    """Tests for API key authentication."""

    def test_creates_bearer_header(self):
        auth = APIKeyAuth("my-api-key-1234")
        headers = auth.get_headers()
        assert headers["Authorization"] == "Bearer my-api-key-1234"

    def test_strips_whitespace(self):
        auth = APIKeyAuth("  my-key  ")
        headers = auth.get_headers()
        assert headers["Authorization"] == "Bearer my-key"

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            APIKeyAuth("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            APIKeyAuth("   ")

    def test_is_valid(self):
        auth = APIKeyAuth("my-key-12345678")
        assert auth.is_valid() is True

    def test_repr_masks_key(self):
        auth = APIKeyAuth("my-secret-api-key")
        repr_str = repr(auth)
        assert "my-s" in repr_str
        assert "-key" in repr_str
        assert "my-secret-api-key" not in repr_str


class TestOAuthToken:
    """Tests for OAuth token handling."""

    def test_not_expired_when_no_expiry(self):
        token = OAuthToken(access_token="test-token")
        assert token.is_expired is False

    def test_not_expired_when_fresh(self):
        token = OAuthToken(access_token="test-token", expires_in=3600)
        assert token.is_expired is False

    def test_expired_token(self):
        import time

        token = OAuthToken(
            access_token="test-token",
            expires_in=1,  # Expires in 1 second
            _obtained_at=time.time() - 120,  # Obtained 2 minutes ago
        )
        assert token.is_expired is True


class TestOAuthProvider:
    """Tests for OAuth 2.0 authentication."""

    def test_requires_client_id(self):
        with pytest.raises(ValueError, match="client_id and client_secret"):
            OAuthProvider(
                client_id="",
                client_secret="secret",
                token_url="https://auth.example.com/token",
            )

    def test_requires_token_url(self):
        with pytest.raises(ValueError, match="token_url"):
            OAuthProvider(
                client_id="id",
                client_secret="secret",
                token_url="",
            )

    @respx.mock
    def test_fetches_token(self, respx_mock):
        respx_mock.post("https://auth.example.com/token").respond(
            200,
            json={
                "access_token": "new-token-xyz",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

        provider = OAuthProvider(
            client_id="test-id",
            client_secret="test-secret",
            token_url="https://auth.example.com/token",
        )

        headers = provider.get_headers()
        assert headers["Authorization"] == "Bearer new-token-xyz"
        provider.close()

    @respx.mock
    def test_token_fetch_failure(self, respx_mock):
        respx_mock.post("https://auth.example.com/token").respond(401)

        provider = OAuthProvider(
            client_id="bad-id",
            client_secret="bad-secret",
            token_url="https://auth.example.com/token",
        )

        with pytest.raises(AuthenticationError):
            provider.get_headers()
        provider.close()

    def test_revoke_clears_token(self):
        provider = OAuthProvider(
            client_id="test-id",
            client_secret="test-secret",
            token_url="https://auth.example.com/token",
        )
        provider._token = OAuthToken(access_token="test")
        assert provider.is_valid() is True
        provider.revoke()
        assert provider.is_valid() is False
        provider.close()
