"""
Authentication core

Provides password generation, JWT token creation/verification, and credential validation.
Also supports API Key authentication (Bearer tokens with the `arc-` prefix).

Pass credentials only through the ``Authorization`` header; reject session credentials in query parameters.
Native browser requests (``<img>`` / ``<video>`` src and native download navigation) cannot carry that header:
- Export-download endpoints use short-lived download tokens (``purpose=download``) as their sole query-parameter authentication method
- Static media files require no authentication
The frontend consumes SSE through fetch with headers, using the same dependency as ordinary protected endpoints (see ``docs/adr/0071``).
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
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict

from lib import PROJECT_ROOT
from lib.infra.api_errors import ApiError
from lib.infra.data_root_layout import DataRootLayout

logger = logging.getLogger(__name__)


class CurrentUserInfo(BaseModel):
    """Current authenticated user info."""

    id: str
    sub: str
    role: str = "admin"

    model_config = ConfigDict(frozen=True)


# JWT signing-key cache
_cached_token_secret: str | None = None

# Token lifetime: 7 days
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600

# Anonymous user identity returned when authentication is disabled
_ANONYMOUS_USER_SUB = "local"

# Environment values interpreted as authentication disabled. Empty strings are excluded: accidental .env `AUTH_ENABLED=` falls back to enabled,
# preventing silent fail-open behavior.
_AUTH_DISABLED_VALUES = frozenset({"false", "0", "no", "off"})


def is_auth_enabled() -> bool:
    """Parse ``AUTH_ENABLED``. Default ``true`` preserves existing deployments; empty values also use the default.

    ``false`` / ``0`` / ``no`` / ``off`` all disable authentication, case-insensitively.
    """
    return os.environ.get("AUTH_ENABLED", "true").strip().lower() not in _AUTH_DISABLED_VALUES


def warn_if_auth_disabled() -> None:
    """Log a WARNING when authentication is disabled, indicating all management interfaces require no authentication."""
    if is_auth_enabled():
        return
    logger.warning(
        "AUTH_ENABLED=false：全部管理接口无需认证即可访问。"
        "该模式仅适用于受独立网络边界保护的本机环境，远程部署请保持认证开启"
    )


def _anonymous_user() -> "CurrentUserInfo":
    """Fixed anonymous user returned when authentication is disabled."""
    from lib.db.base import DEFAULT_USER_ID

    return CurrentUserInfo(id=DEFAULT_USER_ID, sub=_ANONYMOUS_USER_SUB, role="admin")


# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

# Password hashing
_password_hash = PasswordHash.recommended()
_cached_password_hash: str | None = None


def generate_password(length: int = 16) -> str:
    """Generate a random alphanumeric password"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_token_secret() -> str:
    """Get the JWT signing key

    Prefer explicitly configured nonempty ``AUTH_TOKEN_SECRET``; otherwise use the key persisted in the data directory,
    generating on first use and retaining across restarts so logged-in sessions survive restart.
    """
    global _cached_token_secret

    env_secret = os.environ.get("AUTH_TOKEN_SECRET")
    if env_secret:
        return env_secret

    if _cached_token_secret is None:
        _cached_token_secret = _load_or_create_persisted_token_secret()
    return _cached_token_secret


def reset_token_secret_for_tests() -> None:
    """Clear the in-process signing-key cache to simulate process restart."""
    global _cached_token_secret
    _cached_token_secret = None


def ensure_token_secret() -> None:
    """Resolve the signing key at startup to avoid key-file I/O on request paths."""
    get_token_secret()


def _load_or_create_persisted_token_secret() -> str:
    """Read the data-directory signing key; if absent, generate and write with mode 0600.

    On read/write failure, use an in-process random key without blocking startup; logged-in sessions then expire after restart.
    """
    path = DataRootLayout.current().auth_token_secret_path
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    except OSError:
        logger.warning("无法读取 JWT 签名密钥文件 %s，本次启动改用临时密钥；请修复后重启或设置 AUTH_TOKEN_SECRET", path)
        return secrets.token_hex(32)

    if existing:
        return existing

    secret = secrets.token_hex(32)
    try:
        _write_owner_only(path, secret)
    except OSError:
        logger.warning(
            "无法写入 JWT 签名密钥文件 %s，本次启动改用临时密钥，重启后需重新登录；请检查数据目录权限或设置 AUTH_TOKEN_SECRET",
            path,
        )
        return secret
    logger.info("未设置 AUTH_TOKEN_SECRET，已生成 JWT 签名密钥并保存到 %s", path)
    return secret


def _write_owner_only(path: Path, content: str) -> None:
    """Write a same-directory temporary file (mode 0600 at creation), then replace; failures leave no partial key file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # hard-delete: infra -- Signing-key temporary file after a failed write
        raise


def create_token(username: str, *, expiry_seconds: int = TOKEN_EXPIRY_SECONDS) -> str:
    """Create a JWT token

    Args:
        username: Username
        expiry_seconds: Token lifetime in seconds; web login retains 7 days, embedded agents use short lifetimes.

    Returns:
        JWT token string
    """
    now = time.time()
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + expiry_seconds,
    }
    return jwt.encode(payload, get_token_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    """Verify a session JWT token

    Tokens with a ``purpose`` claim (such as download tokens) are verified by their corresponding endpoints only, not used as session credentials.

    Args:
        token: JWT token string

    Returns:
        Return the payload dict on success, None on failure
    """
    try:
        payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None
    if "purpose" in payload:
        return None
    return payload


DOWNLOAD_TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


def create_download_token(username: str, project_name: str) -> str:
    """Issue short-lived download tokens for native browser download authentication"""
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
    """Verify a download token

    Returns:
        Return the payload dict on success

    Raises:
        jwt.ExpiredSignatureError: Token expired
        jwt.InvalidTokenError: Invalid token
        ValueError: purpose or project mismatch
    """
    if not is_auth_enabled():
        return {
            "sub": _ANONYMOUS_USER_SUB,
            "project": project_name,
            "purpose": "download",
        }
    payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
    if payload.get("purpose") != "download":
        raise ValueError("token purpose 不匹配")
    if payload.get("project") != project_name:
        raise ValueError("token project 不匹配")
    return payload


def _get_password_hash() -> str:
    """Get the current password hash (cached)"""
    global _cached_password_hash
    if _cached_password_hash is None:
        raw = os.environ.get("AUTH_PASSWORD", "")
        _cached_password_hash = _password_hash.hash(raw)
    return _cached_password_hash


def check_credentials(username: str, password: str) -> bool:
    """Validate username/password by hash comparison

    Read AUTH_USERNAME (default admin) and AUTH_PASSWORD environment variables.
    Perform hash verification even on username mismatch to prevent timing attacks.

    Return True unconditionally when ``AUTH_ENABLED=false``.
    """
    if not is_auth_enabled():
        return True
    expected_username = os.environ.get("AUTH_USERNAME", "admin")
    pw_hash = _get_password_hash()
    username_ok = secrets.compare_digest(username, expected_username)
    password_ok = _password_hash.verify(password, pw_hash)
    return username_ok and password_ok


def ensure_auth_password(env_path: str | None = None) -> str:
    """Ensure AUTH_PASSWORD is set

    If AUTH_PASSWORD is empty, generate a password, set the environment variable,
    write back to .env, and output through logger.warning to the console.

    Skip this entire step when ``AUTH_ENABLED=false`` (no generation or writeback).

    Args:
        env_path: .env path, defaulting to .env in the project root

    Returns:
        Current AUTH_PASSWORD; empty string when authentication is disabled.
    """
    if not is_auth_enabled():
        return ""
    password = os.environ.get("AUTH_PASSWORD")
    if password:
        return password

    # Generate a password automatically
    password = generate_password()
    os.environ["AUTH_PASSWORD"] = password

    # Write back to .env
    if env_path is None:
        env_path = str(PROJECT_ROOT / ".env")

    env_file = Path(env_path)
    try:
        if env_file.exists():
            try:
                lines = env_file.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                # Historical .env files may use local encodings such as cp936 / ANSI (early Windows users wrote Chinese comments/values);
                # do not force overwrites that could lose user content; log and skip automatic writeback.
                # The in-process password is already set in os.environ and works for this startup, without persistence.
                logger.warning(
                    "无法以 UTF-8 解码 %s，跳过 AUTH_PASSWORD 自动回写；"
                    "请将该文件转存为 UTF-8 后重启以持久化生成的密码",
                    env_path,
                )
                return password
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
            # Write in place (truncate + write) to preserve inode for Docker bind-mount compatibility
            with open(env_file, "r+", encoding="utf-8") as f:
                f.seek(0)
                f.write(new_content)
                f.truncate()
        else:
            env_file.write_text(f"AUTH_PASSWORD={password}\n", encoding="utf-8")
    except OSError:
        logger.warning("无法写入 .env 文件: %s", env_path)

    logger.warning("已自动生成认证密码，请查看 .env 文件中的 AUTH_PASSWORD 字段")
    return password


# ---------------------------------------------------------------------------
# API Key authentication support
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "arc-"
API_KEY_CACHE_TTL = 300  # 5 minutes

# LRU cache: key_hash to (payload_dict | None, expires_at_timestamp)
# None payload means missing or expired key (negative cache)
# Use OrderedDict for LRU: move_to_end on hits, popitem(last=False) on eviction
_api_key_cache: OrderedDict[str, tuple[dict | None, float]] = OrderedDict()
_API_KEY_CACHE_MAX = 512


def hash_api_key(key: str) -> str:
    """Compute an API Key's SHA-256 hash."""
    return hashlib.sha256(key.encode()).hexdigest()


def invalidate_api_key_cache(key_hash: str) -> None:
    """Immediately evict the specified key_hash entry (called when deleting keys)."""
    _api_key_cache.pop(key_hash, None)


def _get_cached_api_key_payload(key_hash: str) -> tuple[bool, dict | None]:
    """Look up the cache. Return (hit, payload or None); move hits to the end (LRU)."""
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
    """Write the cache, including LRU eviction.

    Positive-cache (non-None payload) TTL is bounded by the key's actual expiration,
    preventing expired keys from passing validation through the cache.
    """
    if len(_api_key_cache) >= _API_KEY_CACHE_MAX:
        # Evict the least recently used entry (OrderedDict front)
        _api_key_cache.popitem(last=False)
    ttl = API_KEY_CACHE_TTL
    if payload is not None and expires_at_ts is not None:
        time_to_expiry = expires_at_ts - time.monotonic()
        if time_to_expiry <= 0:
            # Key expired; write a negative-cache entry
            _api_key_cache[key_hash] = (None, time.monotonic() + API_KEY_CACHE_TTL)
            return
        ttl = min(ttl, time_to_expiry)
    _api_key_cache[key_hash] = (payload, time.monotonic() + ttl)


async def verify_api_key(token: str) -> dict | None:
    """Verify an API Key token; return payload dict or None (failed/expired/missing).

    Look up cache first, then database on a miss.
    After successful database lookup, update last_used_at asynchronously in the background without blocking responses.
    """
    key_hash = hash_api_key(token)

    # Cache lookup
    hit, cached_payload = _get_cached_api_key_payload(key_hash)
    if hit:
        return cached_payload

    # Database lookup
    from lib.db import async_session_factory
    from lib.db.repositories.api_key_repository import ApiKeyRepository

    async with async_session_factory() as session, session.begin():
        repo = ApiKeyRepository(session)
        row = await repo.get_by_hash(key_hash)

    if row is None:
        _set_api_key_cache(key_hash, None)
        return None

    # Check expiration
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
            # Convert expiration to a monotonic timestamp for the cache TTL upper bound
            remaining_secs = (exp_dt - datetime.now(UTC)).total_seconds()
            expires_at_monotonic = time.monotonic() + remaining_secs
        except (ValueError, TypeError):
            logger.warning("API Key expires_at 值格式无法解析，忽略过期检查: %r", expires_at)

    payload = {"sub": f"apikey:{row['name']}", "via": "apikey"}
    _set_api_key_cache(key_hash, payload, expires_at_ts=expires_at_monotonic)

    # Update last_used_at asynchronously (nonblocking; retain references against GC)
    import asyncio

    async def _touch():
        try:
            async with async_session_factory() as s, s.begin():
                await ApiKeyRepository(s).touch_last_used(key_hash)
        except Exception:
            logger.exception("更新 API Key last_used_at 失败（非致命）")

    _touch_task = asyncio.create_task(_touch())
    _touch_task.add_done_callback(lambda _: None)  # suppress "never retrieved" warning

    return payload


def _verify_and_get_payload(token: str) -> dict:
    """Synchronously verify JWT and raise 401 on failure (JWT paths only)."""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def _verify_and_get_payload_async(token: str) -> dict:
    """Asynchronously verify tokens in API Key (arc- prefix) and JWT modes."""
    if token.startswith(API_KEY_PREFIX):
        payload = await verify_api_key(token)
        if payload is None:
            raise HTTPException(
                status_code=401,
                detail="API Key 无效、已过期或不存在",
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
    token: Annotated[str | None, Depends(oauth2_scheme_optional)] = None,
) -> CurrentUserInfo:
    """Standard authentication dependency supporting JWT and API Key Bearer tokens.

    With ``AUTH_ENABLED=false``, ignore tokens and return anonymous admin directly.
    When enabled, missing tokens raise 401 (equivalent to the previous oauth2_scheme auto_error behavior).
    """
    if not is_auth_enabled():
        return _anonymous_user()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = await _verify_and_get_payload_async(token)
    return _payload_to_user(payload)


# Type aliases for FastAPI dependency injection
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]

#: Token sub used by embedded agents calling local services (see ``server/agent_runtime/options_assembler.py``).
EMBEDDED_AGENT_SUB = "embedded-agent"


async def reject_embedded_agent(user: CurrentUser) -> None:
    """Irrecoverable operations belong only to creators: reject embedded-agent tokens, preventing bypass through direct sandbox HTTP calls.

    With authentication disabled, every request is the same anonymous user and callers are indistinguishable; this check becomes ineffective, like other identity restrictions.
    """
    if user.sub == EMBEDDED_AGENT_SUB:
        raise ApiError("creator_only_operation", status_code=403)
