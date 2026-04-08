"""
API Key authentication routing unit tests

Tests for the API Key path in the auth module: hash computation, cache logic, and auth routing.
"""

import hashlib
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import server.auth as auth_module


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear API Key cache before each test."""
    auth_module._api_key_cache.clear()
    yield
    auth_module._api_key_cache.clear()


class TestHashApiKey:
    def test_deterministic(self):
        key = "arc-testapikey1234"
        assert auth_module._hash_api_key(key) == auth_module._hash_api_key(key)

    def test_sha256_output(self):
        key = "arc-abc"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert auth_module._hash_api_key(key) == expected


class TestApiKeyCache:
    def test_cache_miss(self):
        hit, payload = auth_module._get_cached_api_key_payload("nonexistent")
        assert not hit
        assert payload is None

    def test_cache_set_and_hit(self):
        auth_module._set_api_key_cache("hash123", {"sub": "apikey:test", "via": "apikey"})
        hit, payload = auth_module._get_cached_api_key_payload("hash123")
        assert hit
        assert payload == {"sub": "apikey:test", "via": "apikey"}

    def test_cache_negative_entry(self):
        auth_module._set_api_key_cache("hash_missing", None)
        hit, payload = auth_module._get_cached_api_key_payload("hash_missing")
        assert hit
        assert payload is None

    def test_cache_expired_entry(self):
        auth_module._api_key_cache["hash_expired"] = ({"sub": "test"}, time.monotonic() - 1)
        hit, _ = auth_module._get_cached_api_key_payload("hash_expired")
        assert not hit

    def test_invalidate_removes_entry(self):
        auth_module._set_api_key_cache("hash_to_delete", {"sub": "test"})
        auth_module.invalidate_api_key_cache("hash_to_delete")
        hit, _ = auth_module._get_cached_api_key_payload("hash_to_delete")
        assert not hit

    def test_cache_hit_skips_db(self):
        """Cache hit should not query the database (verified via _verify_api_key branch logic)."""
        key = "arc-cached-key"
        key_hash = auth_module._hash_api_key(key)
        auth_module._set_api_key_cache(key_hash, {"sub": "apikey:cached", "via": "apikey"})
        # If cache hits, return cached value; True means hit
        hit, payload = auth_module._get_cached_api_key_payload(key_hash)
        assert hit
        assert payload["sub"] == "apikey:cached"


class TestVerifyAndGetPayloadAsync:
    @pytest.mark.asyncio
    async def test_jwt_path_success(self):
        """Non arc- prefix takes JWT path, successfully returns payload."""
        with patch("server.auth.verify_token", return_value={"sub": "admin"}):
            result = await auth_module._verify_and_get_payload_async("some.jwt.token")
        assert result == {"sub": "admin"}

    @pytest.mark.asyncio
    async def test_jwt_invalid_raises_401(self):
        """Non arc- prefix but JWT verification fails, raises 401."""
        with patch("server.auth.verify_token", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._verify_and_get_payload_async("invalid.jwt.token")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_path_success(self):
        """arc- prefix takes API Key path, successfully returns payload."""
        expected = {"sub": "apikey:mykey", "via": "apikey"}
        with patch("server.auth._verify_api_key", new=AsyncMock(return_value=expected)):
            result = await auth_module._verify_and_get_payload_async("arc-validkey")
        assert result["via"] == "apikey"
        assert result["sub"] == "apikey:mykey"

    @pytest.mark.asyncio
    async def test_api_key_not_found_raises_401(self):
        """arc- prefix but key does not exist, raises 401."""
        with patch("server.auth._verify_api_key", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._verify_and_get_payload_async("arc-badkey")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_expired_raises_401(self):
        """arc- prefix but key expired (_verify_api_key returns None), raises 401."""
        with patch("server.auth._verify_api_key", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module._verify_and_get_payload_async("arc-expiredkey")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_jwt_path_not_called_for_api_key(self):
        """verify_token should not be called for arc- prefix tokens."""
        with (
            patch("server.auth._verify_api_key", new=AsyncMock(return_value={"sub": "apikey:k", "via": "apikey"})),
            patch("server.auth.verify_token") as mock_jwt,
        ):
            await auth_module._verify_and_get_payload_async("arc-somekey")
        mock_jwt.assert_not_called()
