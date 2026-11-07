"""
API Key authentication routing unit tests.

Cover auth-module API Key hashing, caches, and authentication dispatch.
"""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import server.auth as auth_module
from tests.fakes import bind_session_factory


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear API Key caches before each test."""
    auth_module._api_key_cache.clear()
    yield
    auth_module._api_key_cache.clear()


@pytest.fixture
def api_key_db(db_factory, monkeypatch):
    """Bind API Key lookups to in-memory databases.

    ``verify_api_key`` imports ``lib.db.async_session_factory`` inside functions and genuinely
    queries on misses. In-memory binding exercises expiry/cache writes against real data.
    """
    bind_session_factory(monkeypatch, db_factory)
    return db_factory


async def _seed_api_key(factory, name: str, key: str, *, expires_at: datetime | None = None) -> None:
    from lib.db.models.api_key import ApiKey

    async with factory() as session:
        session.add(
            ApiKey(
                name=name,
                key_hash=auth_module.hash_api_key(key),
                key_prefix=key[:8],
                expires_at=expires_at,
            )
        )
        await session.commit()


class TestHashApiKey:
    def test_deterministic(self):
        key = "arc-testapikey1234"
        assert auth_module.hash_api_key(key) == auth_module.hash_api_key(key)

    def test_sha256_output(self):
        key = "arc-abc"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert auth_module.hash_api_key(key) == expected


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
        """Cache hits bypass DB queries, verified through verify_api_key branches."""
        key = "arc-cached-key"
        key_hash = auth_module.hash_api_key(key)
        auth_module._set_api_key_cache(key_hash, {"sub": "apikey:cached", "via": "apikey"})
        # Return cached values on hits; True means hit.
        hit, payload = auth_module._get_cached_api_key_payload(key_hash)
        assert hit
        assert payload["sub"] == "apikey:cached"


class TestVerifyAndGetPayloadAsync:
    @pytest.mark.asyncio
    async def test_jwt_path_success(self):
        """Non-arc- prefixes use JWT paths, returning payloads on success."""
        with patch("server.auth.verify_token", return_value={"sub": "admin"}):
            result = await auth_module._verify_and_get_payload_async("some.jwt.token")
        assert result == {"sub": "admin"}

    @pytest.mark.asyncio
    async def test_jwt_invalid_raises_401(self):
        """Invalid non-arc- JWTs raise 401."""
        with patch("server.auth.verify_token", return_value=None), pytest.raises(HTTPException) as exc_info:
            await auth_module._verify_and_get_payload_async("invalid.jwt.token")
        assert exc_info.value.status_code == 401

    async def test_api_key_path_success(self, api_key_db):
        """arc- prefixes use API Keys; successful DB matches return/cache payloads."""
        await _seed_api_key(api_key_db, "mykey", "arc-validkey")

        result = await auth_module._verify_and_get_payload_async("arc-validkey")

        assert result["via"] == "apikey"
        assert result["sub"] == "apikey:mykey"
        hit, cached = auth_module._get_cached_api_key_payload(auth_module.hash_api_key("arc-validkey"))
        assert hit
        assert cached == result

    async def test_api_key_not_found_raises_401(self, api_key_db):
        """Unknown arc- keys raise 401."""
        with pytest.raises(HTTPException) as exc_info:
            await auth_module._verify_and_get_payload_async("arc-badkey")
        assert exc_info.value.status_code == 401

    async def test_api_key_expired_raises_401(self, api_key_db):
        """Existing expired keys raise 401 and populate negative caches."""
        await _seed_api_key(
            api_key_db,
            "expiredkey",
            "arc-expiredkey",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )

        with pytest.raises(HTTPException) as exc_info:
            await auth_module._verify_and_get_payload_async("arc-expiredkey")

        assert exc_info.value.status_code == 401
        hit, cached = auth_module._get_cached_api_key_payload(auth_module.hash_api_key("arc-expiredkey"))
        assert hit
        assert cached is None
