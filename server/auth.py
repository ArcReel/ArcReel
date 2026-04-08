"""
Authentication core module.

Provides password generation, JWT token creation/verification, and credential validation.
Also supports API Key authentication (Bearer tokens with `arc-` prefix).
"""

import hashlib
import logging
import os
import secrets
import string
import time
from collections import OrderedDict
from datetime import UTC
from pathlib import Path
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict

from lib import PROJECT_ROOT

logger = logging.getLogger(__name__)


class CurrentUserInfo(BaseModel):
    """Current authenticated user info."""

    id: str
    sub: str
    role: str = "admin"

    model_config = ConfigDict(frozen=True)


# JWT signing key cache
_cached_token_secret: str | None = None

# Token expiry: 7 days
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

# Password hash
_password_hash = PasswordHash.recommended()
_cached_password_hash: str | None = None


def generate_password(length: int = 16) -> str:
    """Generate a random alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_token_secret() -> str:
    """Get the JWT signing key.

    Prefers the AUTH_TOKEN_SECRET environment variable; auto-generates and caches if not set.
    """
    global _cached_token_secret

    env_secret = os.environ.get("AUTH_TOKEN_SECRET")
    if env_secret:
        return env_secret

    if _cached_token_secret is not None:
        return _cached_token_secret

    _cached_token_secret = secrets.token_hex(32)
    logger.info("JWT signing key auto-generated")
    return _cached_token_secret


def create_token(username: str) -> str:
    """Create a JWT token.

    Args:
        username: Username.

    Returns:
        JWT token string.
    """
    now = time.time()
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, get_token_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    """Verify a JWT token.

    Args:
        token: JWT token string.

    Returns:
        Payload dict on success, None on failure.
    """
    try:
        payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
        return payload
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None


DOWNLOAD_TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


def create_download_token(username: str, project_name: str) -> str:
    """Issue a short-lived download token for browser-native download authentication."""
    now = time.time()
    payload = {
        "sub": username,
        "project": project_name,
        "purpose": "download",
        "iat": now,
        "exp": now + DOWNLOAD_TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, get_token_secret(), algorithm="HS256")


def verify_download_token(token: str, project_name: str) -> dict:
    """Verify a download token.

    Returns:
        Payload dict on success.

    Raises:
        jwt.ExpiredSignatureError: Token has expired.
        jwt.InvalidTokenError: Token is invalid.
        ValueError: Purpose or project mismatch.
    """
    payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
    if payload.get("purpose") != "download":
        raise ValueError("token purpose mismatch")
    if payload.get("project") != project_name:
        raise ValueError("token project mismatch")
    return payload


def _get_password_hash() -> str:
    """Get the hashed value of the current password (cached)."""
    global _cached_password_hash
    if _cached_password_hash is None:
        raw = os.environ.get("AUTH_PASSWORD", "")
        _cached_password_hash = _password_hash.hash(raw)
    return _cached_password_hash


def check_credentials(username: str, password: str) -> bool:
    """Verify username and password using hash comparison.

    Reads from AUTH_USERNAME (default: admin) and AUTH_PASSWORD environment variables.
    Hash verification is performed even when the username doesn't match, to prevent timing attacks.
    """
    expected_username = os.environ.get("AUTH_USERNAME", "admin")
    pw_hash = _get_password_hash()
    username_ok = secrets.compare_digest(username, expected_username)
    password_ok = _password_hash.verify(password, pw_hash)
    return username_ok and password_ok


def ensure_auth_password(env_path: str | None = None) -> str:
    """Ensure AUTH_PASSWORD is set.

    If the AUTH_PASSWORD environment variable is empty, auto-generates a password,
    sets it in the environment, writes it back to the .env file, and logs a warning.

    Args:
        env_path: Path to the .env file; defaults to .env in the project root.

    Returns:
        The current AUTH_PASSWORD value.
    """
    password = os.environ.get("AUTH_PASSWORD")
    if password:
        return password

    # Auto-generate password
    password = generate_password()
    os.environ["AUTH_PASSWORD"] = password

    # Write back to .env file
    if env_path is None:
        env_path = str(PROJECT_ROOT / ".env")

    env_file = Path(env_path)
    try:
        if env_file.exists():
            lines = env_file.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if not found and line.strip().startswith("AUTH_PASSWORD="):
                    new_lines.append(f"AUTH_PASSWORD={password}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"AUTH_PASSWORD={password}")
            new_content = "\n".join(new_lines) + "\n"
            # Use in-place write (truncate + write) to preserve inode, compatible with Docker bind mounts
            with open(env_file, "r+") as f:
                f.seek(0)
                f.write(new_content)
                f.truncate()
        else:
            env_file.write_text(f"AUTH_PASSWORD={password}\n")
    except OSError:
        logger.warning("Unable to write to .env file: %s", env_path)

    logger.warning("Authentication password auto-generated. Check the AUTH_PASSWORD field in the .env file.")
    return password


# ---------------------------------------------------------------------------
# API Key authentication support
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "arc-"
API_KEY_CACHE_TTL = 300  # 5 minutes

# LRU cache: key_hash → (payload_dict | None, expires_at_timestamp)
# payload None means the key doesn't exist or has expired (negative cache)
# Uses OrderedDict for LRU: move_to_end on hit, popitem(last=False) on eviction
_api_key_cache: OrderedDict[str, tuple[dict | None, float]] = OrderedDict()
_API_KEY_CACHE_MAX = 512


def _hash_api_key(key: str) -> str:
    """Compute the SHA-256 hash of an API Key."""
    return hashlib.sha256(key.encode()).hexdigest()


def invalidate_api_key_cache(key_hash: str) -> None:
    """Immediately clear the cache entry for the given key_hash (called when a key is deleted)."""
    _api_key_cache.pop(key_hash, None)


def _get_cached_api_key_payload(key_hash: str) -> tuple[bool, dict | None]:
    """Look up from cache. Returns (hit, payload or None). Moves entry to end on hit (LRU)."""
    entry = _api_key_cache.get(key_hash)
    if entry is None:
        return False, None
    payload, expiry = entry
    if time.monotonic() > expiry:
        _api_key_cache.pop(key_hash, None)
        return False, None
    _api_key_cache.move_to_end(key_hash)
    return True, payload


def _set_api_key_cache(key_hash: str, payload: dict | None, expires_at_ts: float | None = None) -> None:
    """Write to cache (with LRU eviction).

    Positive cache (non-None payload) TTL is capped by the key's actual expiry time,
    to prevent an expired key from passing validation while still in cache.
    """
    if len(_api_key_cache) >= _API_KEY_CACHE_MAX:
        # Evict least recently used entry (LRU: head of OrderedDict)
        _api_key_cache.popitem(last=False)
    ttl = API_KEY_CACHE_TTL
    if payload is not None and expires_at_ts is not None:
        time_to_expiry = expires_at_ts - time.monotonic()
        if time_to_expiry <= 0:
            # Key already expired, write to negative cache
            _api_key_cache[key_hash] = (None, time.monotonic() + API_KEY_CACHE_TTL)
            return
        ttl = min(ttl, time_to_expiry)
    _api_key_cache[key_hash] = (payload, time.monotonic() + ttl)


async def _verify_api_key(token: str) -> dict | None:
    """Verify an API Key token, returning a payload dict or None (invalid/expired/not found).

    First checks the cache, then falls back to the database on cache miss.
    On successful DB lookup, updates last_used_at asynchronously in the background.
    """
    key_hash = _hash_api_key(token)

    # Cache lookup
    hit, cached_payload = _get_cached_api_key_payload(key_hash)
    if hit:
        return cached_payload

    # Database lookup
    from lib.db import async_session_factory
    from lib.db.repositories.api_key_repository import ApiKeyRepository

    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            row = await repo.get_by_hash(key_hash)

    if row is None:
        _set_api_key_cache(key_hash, None)
        return None

    # Check expiry
    expires_at = row.get("expires_at")
    expires_at_monotonic: float | None = None
    if expires_at:
        from datetime import datetime

        try:
            exp_dt = expires_at
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if datetime.now(UTC) >= exp_dt:
                _set_api_key_cache(key_hash, None)
                return None
            # Convert expiry to monotonic timestamp for cache TTL upper bound calculation
            remaining_secs = (exp_dt - datetime.now(UTC)).total_seconds()
            expires_at_monotonic = time.monotonic() + remaining_secs
        except (ValueError, TypeError):
            logger.warning("API Key expires_at value cannot be parsed, skipping expiry check: %r", expires_at)

    payload = {"sub": f"apikey:{row['name']}", "via": "apikey"}
    _set_api_key_cache(key_hash, payload, expires_at_ts=expires_at_monotonic)

    # Async update of last_used_at (non-blocking; hold reference to prevent GC)
    import asyncio

    async def _touch():
        try:
            async with async_session_factory() as s:
                async with s.begin():
                    await ApiKeyRepository(s).touch_last_used(key_hash)
        except Exception:
            logger.exception("Failed to update API Key last_used_at (non-fatal)")

    _touch_task = asyncio.create_task(_touch())
    _touch_task.add_done_callback(lambda _: None)  # suppress "never retrieved" warning

    return payload


def _verify_and_get_payload(token: str) -> dict:
    """Synchronously verify a JWT token and raise 401 on failure. (JWT path only)"""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Token is invalid or has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def _verify_and_get_payload_async(token: str) -> dict:
    """Asynchronously verify a token; supports both API Key (arc- prefix) and JWT modes."""
    if token.startswith(API_KEY_PREFIX):
        payload = await _verify_api_key(token)
        if payload is None:
            raise HTTPException(
                status_code=401,
                detail="API Key is invalid, expired, or does not exist",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    # JWT path
    return _verify_and_get_payload(token)


def _payload_to_user(payload: dict) -> CurrentUserInfo:
    """Convert a verified JWT/API-key payload to CurrentUserInfo."""
    from lib.db.base import DEFAULT_USER_ID

    sub = payload.get("sub", "")
    return CurrentUserInfo(id=DEFAULT_USER_ID, sub=sub, role="admin")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> CurrentUserInfo:
    """Standard authentication dependency — supports JWT and API Key Bearer tokens."""
    payload = await _verify_and_get_payload_async(token)
    return _payload_to_user(payload)


async def get_current_user_flexible(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)] = None,
    query_token: str | None = Query(None, alias="token"),
) -> CurrentUserInfo:
    """SSE authentication dependency — supports both Authorization header and ?token= query param."""
    raw = token or query_token
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = await _verify_and_get_payload_async(raw)
    return _payload_to_user(payload)


# Type aliases for FastAPI dependency injection
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]
CurrentUserFlexible = Annotated[CurrentUserInfo, Depends(get_current_user_flexible)]
