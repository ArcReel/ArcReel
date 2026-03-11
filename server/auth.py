"""
认证核心模块

提供密码生成、JWT token 创建/验证、凭据校验等功能。
同时支持 API Key 认证（`arc-` 前缀的 Bearer token）。
"""

import hashlib
import logging
import os
import secrets
import string
import time
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash

from lib import PROJECT_ROOT

logger = logging.getLogger(__name__)

# JWT 签名密钥缓存
_cached_token_secret: Optional[str] = None

# Token 有效期：7 天
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token", auto_error=False
)

# 密码哈希
_password_hash = PasswordHash.recommended()
_cached_password_hash: Optional[str] = None


def generate_password(length: int = 16) -> str:
    """生成随机字母数字密码"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_token_secret() -> str:
    """获取 JWT 签名密钥

    优先使用 AUTH_TOKEN_SECRET 环境变量，否则自动生成并缓存。
    """
    global _cached_token_secret

    env_secret = os.environ.get("AUTH_TOKEN_SECRET")
    if env_secret:
        return env_secret

    if _cached_token_secret is not None:
        return _cached_token_secret

    _cached_token_secret = secrets.token_hex(32)
    logger.info("已自动生成 JWT 签名密钥")
    return _cached_token_secret


def create_token(username: str) -> str:
    """创建 JWT token

    Args:
        username: 用户名

    Returns:
        JWT token 字符串
    """
    now = time.time()
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, get_token_secret(), algorithm="HS256")


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT token

    Args:
        token: JWT token 字符串

    Returns:
        成功返回 payload dict，失败返回 None
    """
    try:
        payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
        return payload
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None


DOWNLOAD_TOKEN_EXPIRY_SECONDS = 300  # 5 分钟


def create_download_token(username: str, project_name: str) -> str:
    """签发短时效下载 token，用于浏览器原生下载认证"""
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
    """验证下载 token

    Returns:
        成功返回 payload dict

    Raises:
        jwt.ExpiredSignatureError: token 已过期
        jwt.InvalidTokenError: token 无效
        ValueError: purpose 或 project 不匹配
    """
    payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
    if payload.get("purpose") != "download":
        raise ValueError("token purpose 不匹配")
    if payload.get("project") != project_name:
        raise ValueError("token project 不匹配")
    return payload


def _get_password_hash() -> str:
    """获取当前密码的哈希值（缓存）"""
    global _cached_password_hash
    if _cached_password_hash is None:
        raw = os.environ.get("AUTH_PASSWORD", "")
        _cached_password_hash = _password_hash.hash(raw)
    return _cached_password_hash


def check_credentials(username: str, password: str) -> bool:
    """校验用户名密码（使用哈希比对）

    从 AUTH_USERNAME（默认 admin）和 AUTH_PASSWORD 环境变量读取。
    即使用户名不匹配也执行哈希验证，防止时序攻击。
    """
    expected_username = os.environ.get("AUTH_USERNAME", "admin")
    pw_hash = _get_password_hash()
    username_ok = secrets.compare_digest(username, expected_username)
    password_ok = _password_hash.verify(password, pw_hash)
    return username_ok and password_ok


def ensure_auth_password(env_path: Optional[str] = None) -> str:
    """确保 AUTH_PASSWORD 已设置

    如果 AUTH_PASSWORD 环境变量为空，自动生成密码，写入环境变量，
    回写到 .env 文件，并用 logger.warning 输出到控制台。

    Args:
        env_path: .env 文件路径，默认为项目根目录的 .env

    Returns:
        当前的 AUTH_PASSWORD 值
    """
    password = os.environ.get("AUTH_PASSWORD")
    if password:
        return password

    # 自动生成密码
    password = generate_password()
    os.environ["AUTH_PASSWORD"] = password

    # 回写到 .env 文件
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
            # 使用原地写入（truncate + write）保留 inode，兼容 Docker bind mount
            with open(env_file, "r+") as f:
                f.seek(0)
                f.write(new_content)
                f.truncate()
        else:
            env_file.write_text(f"AUTH_PASSWORD={password}\n")
    except OSError:
        logger.warning("无法写入 .env 文件: %s", env_path)

    logger.warning(
        "已自动生成认证密码，请查看 .env 文件中的 AUTH_PASSWORD 字段"
    )
    return password


# ---------------------------------------------------------------------------
# API Key 认证支持
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "arc-"
API_KEY_CACHE_TTL = 300  # 5 分钟

# LRU 缓存：key_hash → (payload_dict | None, expires_at_timestamp)
# payload 为 None 表示 key 不存在或已过期（负缓存）
# 使用 OrderedDict 实现 LRU：命中时 move_to_end，淘汰时 popitem(last=False)
_api_key_cache: OrderedDict[str, tuple[Optional[dict], float]] = OrderedDict()
_API_KEY_CACHE_MAX = 512


def _hash_api_key(key: str) -> str:
    """计算 API Key 的 SHA-256 哈希。"""
    return hashlib.sha256(key.encode()).hexdigest()


def invalidate_api_key_cache(key_hash: str) -> None:
    """立即清除指定 key_hash 的缓存条目（key 删除时调用）。"""
    _api_key_cache.pop(key_hash, None)


def _get_cached_api_key_payload(key_hash: str) -> tuple[bool, Optional[dict]]:
    """从缓存中查找。返回 (命中, payload 或 None)。命中时将条目移至末尾（LRU）。"""
    entry = _api_key_cache.get(key_hash)
    if entry is None:
        return False, None
    payload, expiry = entry
    if time.monotonic() > expiry:
        _api_key_cache.pop(key_hash, None)
        return False, None
    _api_key_cache.move_to_end(key_hash)
    return True, payload


def _set_api_key_cache(key_hash: str, payload: Optional[dict], expires_at_ts: Optional[float] = None) -> None:
    """写入缓存（含 LRU 淘汰）。

    正向缓存（payload 非 None）TTL 以 key 实际过期时间为上界，
    避免 key 过期后仍在缓存中通过验证的安全问题。
    """
    if len(_api_key_cache) >= _API_KEY_CACHE_MAX:
        # 淘汰最久未使用的条目（LRU：OrderedDict 头部）
        _api_key_cache.popitem(last=False)
    ttl = API_KEY_CACHE_TTL
    if payload is not None and expires_at_ts is not None:
        time_to_expiry = expires_at_ts - time.monotonic()
        if time_to_expiry <= 0:
            # key 已过期，写入负缓存
            _api_key_cache[key_hash] = (None, time.monotonic() + API_KEY_CACHE_TTL)
            return
        ttl = min(ttl, time_to_expiry)
    _api_key_cache[key_hash] = (payload, time.monotonic() + ttl)


async def _verify_api_key(token: str) -> Optional[dict]:
    """验证 API Key token，返回 payload dict 或 None（失败/过期/不存在）。

    内部先查缓存，缓存未命中再查数据库。
    查库成功后更新 last_used_at（后台异步，不阻塞响应）。
    """
    key_hash = _hash_api_key(token)

    # 缓存查询
    hit, cached_payload = _get_cached_api_key_payload(key_hash)
    if hit:
        return cached_payload

    # 数据库查询
    from lib.db import async_session_factory
    from lib.db.repositories.api_key_repository import ApiKeyRepository

    async with async_session_factory() as session:
        async with session.begin():
            repo = ApiKeyRepository(session)
            row = await repo.get_by_hash(key_hash)

    if row is None:
        _set_api_key_cache(key_hash, None)
        return None

    # 检查过期
    expires_at = row.get("expires_at")
    expires_at_monotonic: Optional[float] = None
    if expires_at:
        from datetime import datetime, timezone
        try:
            # get_by_hash 返回 ORM 原生 datetime 对象；兼容旧式 ISO 字符串路径
            if isinstance(expires_at, str):
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            else:
                exp_dt = expires_at
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp_dt:
                _set_api_key_cache(key_hash, None)
                return None
            # 将过期时刻转换为 monotonic 时间戳，供缓存 TTL 上界计算
            remaining_secs = (exp_dt - datetime.now(timezone.utc)).total_seconds()
            expires_at_monotonic = time.monotonic() + remaining_secs
        except (ValueError, TypeError):
            logger.warning("API Key expires_at 值格式无法解析，忽略过期检查: %r", expires_at)

    payload = {"sub": f"apikey:{row['name']}", "via": "apikey"}
    _set_api_key_cache(key_hash, payload, expires_at_ts=expires_at_monotonic)

    # 异步更新 last_used_at（不阻塞，保存引用防止 GC）
    import asyncio

    async def _touch():
        try:
            async with async_session_factory() as s:
                async with s.begin():
                    await ApiKeyRepository(s).touch_last_used(key_hash)
        except Exception:
            logger.exception("更新 API Key last_used_at 失败（非致命）")

    _touch_task = asyncio.create_task(_touch())
    _touch_task.add_done_callback(lambda _: None)  # suppress "never retrieved" warning

    return payload


def _verify_and_get_payload(token: str) -> dict:
    """同步验证 JWT token 并在失败时抛出 401 异常。（仅用于 JWT 路径）"""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def _verify_and_get_payload_async(token: str) -> dict:
    """异步验证 token，支持 API Key（arc- 前缀）和 JWT 两种模式。"""
    if token.startswith(API_KEY_PREFIX):
        payload = await _verify_api_key(token)
        if payload is None:
            raise HTTPException(
                status_code=401,
                detail="API Key 无效、已过期或不存在",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
    # JWT 路径
    return _verify_and_get_payload(token)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict:
    """标准认证依赖 — 支持 JWT 和 API Key Bearer token。"""
    return await _verify_and_get_payload_async(token)


async def get_current_user_flexible(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)] = None,
    query_token: str | None = Query(None, alias="token"),
) -> dict:
    """SSE 认证依赖 — 同时支持 Authorization header 和 ?token= query param。"""
    raw = token or query_token
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="缺少认证 token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _verify_and_get_payload_async(raw)
