"""Pull-based desired-state synchronization from the cloud account center."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import platform
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.app_data_dir import app_data_dir
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.url_utils import normalize_base_url
from lib.db import async_session_factory
from lib.db.base import utc_now
from lib.db.models.credential import ProviderCredential
from lib.db.models.user import AccountCenterConnection, AccountCenterLoginTicket, User
from lib.db.repositories.credential_repository import CredentialRepository

logger = logging.getLogger(__name__)

_SUPPORTED_SECRET_FIELDS = ("api_key", "access_key", "secret_key")
_DEVICE_ID_FILE = ".account_center_device_id"
_DEVICE_KEY_FILE = ".account_center_device_key"


@dataclass(frozen=True)
class DeviceRegistration:
    device_id: str
    encrypted_token: str


def build_config_schema(system_id: str) -> dict[str, object]:
    providers: list[dict[str, object]] = []
    for provider_id, meta in PROVIDER_REGISTRY.items():
        fields = [key for key in meta.required_keys if key in meta.secret_keys and key in _SUPPORTED_SECRET_FIELDS]
        groups = meta.credential_groups or ([fields] if fields else [])
        providers.append(
            {
                "id": provider_id,
                "name": meta.display_name,
                "description": meta.description,
                "media_types": list(meta.media_types),
                "centrally_configurable": bool(fields),
                "secret_fields": [
                    {
                        "key": key,
                        "label": {"api_key": "API Key", "access_key": "Access Key", "secret_key": "Secret Key"}[key],
                    }
                    for key in fields
                ],
                "secret_field_groups": groups,
                "supports_base_url": "base_url" in meta.optional_keys,
            }
        )
    return {"system_id": system_id, "providers": providers}


async def register_device(access_token: str, account_center_sub: str) -> DeviceRegistration:
    """Exchange a verified center user session for an opaque long-lived device token."""
    from server.services.account_center import AccountCenterError, account_center_config

    config = account_center_config()
    center_root = config.issuer_url.removesuffix("/auth/v1")
    installation_id = await asyncio.to_thread(_load_or_create_device_id)
    identity_suffix = hashlib.sha256(account_center_sub.encode("utf-8")).hexdigest()[:16]
    device_id = f"{installation_id}:{identity_suffix}"
    try:
        app_version = version("arcreel")
    except PackageNotFoundError:
        app_version = "development"
    payload = {
        "system_id": config.system_id,
        "device_id": device_id,
        "device_name": platform.node() or "ArcReel",
        "platform": f"{platform.system()} {platform.release()}",
        "app_version": app_version,
        "capabilities": {"config_schema": build_config_schema(config.system_id)},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": config.publishable_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{center_root}/functions/v1/center-client-sync/register", headers=headers, json=payload
            )
    except httpx.HTTPError as exc:
        raise AccountCenterError(
            "无法登记本地 ArcReel 设备，请稍后重试", 503, "DEVICE_REGISTRATION_UNAVAILABLE"
        ) from exc
    if not response.is_success:
        message, code = _center_error(response, "本地设备登记失败", "DEVICE_REGISTRATION_FAILED")
        raise AccountCenterError(message, response.status_code, code)
    data = response.json()
    raw_token = str(data.get("device_token") or "")
    returned_id = str(data.get("device_id") or "")
    if not raw_token or returned_id != device_id:
        raise AccountCenterError("账号中心返回了无效设备凭据", 503, "DEVICE_REGISTRATION_INVALID")
    return DeviceRegistration(device_id=device_id, encrypted_token=_encrypt_token(raw_token))


async def attach_ticket_connection(
    session: AsyncSession,
    ticket: AccountCenterLoginTicket,
    user: User,
) -> AccountCenterConnection | None:
    """Transfer the registration captured by a login ticket to its bound local user."""
    if not ticket.device_id or not ticket.device_token_encrypted:
        return None
    connection = await session.get(AccountCenterConnection, user.id)
    if connection is None:
        connection = AccountCenterConnection(
            user_id=user.id,
            account_center_sub=ticket.account_center_sub,
            device_id=ticket.device_id,
            device_token_encrypted=ticket.device_token_encrypted,
        )
        session.add(connection)
    else:
        connection.account_center_sub = ticket.account_center_sub
        connection.device_id = ticket.device_id
        connection.device_token_encrypted = ticket.device_token_encrypted
        connection.last_sync_error = None
    return connection


async def sync_user_connection(user_id: str) -> bool:
    """Pull and atomically apply one user's complete centrally-managed credential snapshot."""
    from server.services.account_center import account_center_config

    async with async_session_factory() as session:
        connection = await session.get(AccountCenterConnection, user_id)
        if connection is None:
            return False
        device_id = connection.device_id
        account_center_sub = connection.account_center_sub
        config = account_center_config(require_oauth=False)
        if not config.issuer_url or not config.publishable_key:
            return False
        try:
            device_token = _decrypt_token(connection.device_token_encrypted)
        except ValueError as exc:
            await _mark_failed(session, user_id, str(exc))
            return False
        center_root = config.issuer_url.removesuffix("/auth/v1")
        headers = {
            "Authorization": f"Bearer {device_token}",
            "apikey": config.publishable_key,
            "x-device-id": device_id,
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{center_root}/functions/v1/center-client-sync/config", headers=headers)
        except httpx.HTTPError as exc:
            await _mark_failed(session, user_id, f"账号中心不可达：{exc.__class__.__name__}")
            return False
        if not response.is_success:
            message, _ = _center_error(response, "配置同步失败", "CONFIG_SYNC_FAILED")
            await _mark_failed(session, user_id, message)
            return False
        payload = response.json()
        if str(payload.get("account_center_sub") or "") != account_center_sub:
            await _mark_failed(session, user_id, "账号中心返回的用户身份不匹配")
            return False
        revision = int(payload.get("revision") or 0)
        credentials = payload.get("credentials")
        if not isinstance(credentials, list):
            await _mark_failed(session, user_id, "账号中心返回的配置格式无效")
            return False
        try:
            await _apply_snapshot(session, user_id, revision, credentials)
            connection.config_revision = revision
            connection.last_sync_at = utc_now()
            connection.last_sync_status = "succeeded"
            connection.last_sync_error = None
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await _mark_failed(session, user_id, f"应用配置失败：{exc}")
            await _send_ack(center_root, config.publishable_key, device_id, device_token, revision, "failed", str(exc))
            return False
        await _send_ack(center_root, config.publishable_key, device_id, device_token, revision, "succeeded", None)
        return True


async def _apply_snapshot(
    session: AsyncSession,
    user_id: str,
    revision: int,
    raw_credentials: list[object],
    *,
    management_source: str = "account_center",
) -> None:
    repo = CredentialRepository(session, user_id)
    configured: set[str] = set()
    for item in raw_credentials:
        if not isinstance(item, dict):
            raise ValueError("配置项格式无效")
        provider_id = str(item.get("provider_id") or "")
        meta = PROVIDER_REGISTRY.get(provider_id)
        if meta is None:
            logger.warning("中台下发了当前 ArcReel 不支持的供应商：%s", provider_id)
            continue
        allowed = [key for key in meta.secret_keys if key in _SUPPORTED_SECRET_FIELDS]
        values = {key: _optional_secret(item.get(key)) for key in allowed}
        groups = meta.credential_groups or ([allowed] if allowed else [])
        if not groups or not any(all(values.get(key) for key in group) for group in groups):
            raise ValueError(f"供应商 {provider_id} 的凭据字段不完整")
        configured.add(provider_id)
        managed_result = await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider_id,
                ProviderCredential.management_source == management_source,
            )
        )
        credential = managed_result.scalar_one_or_none()
        if credential is None:
            credential = await repo.create(
                provider=provider_id,
                name=str(item.get("name") or "数据中台分配")[:128],
                api_key=values.get("api_key"),
                access_key=values.get("access_key"),
                secret_key=values.get("secret_key"),
                base_url=normalize_base_url(_optional_secret(item.get("base_url"))),
            )
        else:
            updates: dict[str, str | None] = {
                "name": str(item.get("name") or "数据中台分配")[:128],
                "base_url": normalize_base_url(_optional_secret(item.get("base_url"))),
            }
            updates.update(values)
            await repo.update(credential.id, **updates)
        credential.management_source = management_source
        credential.management_revision = revision
        if not credential.is_active:
            await repo.activate(credential.id, provider_id)
            credential.is_active = True

    managed = await session.execute(
        select(ProviderCredential).where(
            ProviderCredential.user_id == user_id,
            ProviderCredential.management_source == management_source,
        )
    )
    for credential in managed.scalars():
        if credential.provider not in configured:
            await repo.delete(credential.id)


async def _mark_failed(session: AsyncSession, user_id: str, message: str) -> None:
    connection = await session.get(AccountCenterConnection, user_id)
    if connection is None:
        return
    connection.last_sync_at = utc_now()
    connection.last_sync_status = "failed"
    connection.last_sync_error = message[:500]
    await session.commit()
    logger.warning("账号中心配置同步失败 user=%s: %s", connection.user_id, message)


async def _send_ack(
    center_root: str,
    publishable_key: str,
    device_id: str,
    device_token: str,
    revision: int,
    status: str,
    error: str | None,
) -> None:
    headers = {
        "Authorization": f"Bearer {device_token}",
        "apikey": publishable_key,
        "x-device-id": device_id,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{center_root}/functions/v1/center-client-sync/ack",
                headers=headers,
                json={"revision": revision, "status": status, "error": error},
            )
    except httpx.HTTPError:
        logger.warning("无法向账号中心回报配置同步状态", exc_info=True)


class AccountCenterSyncWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="account-center-config-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        interval = max(
            15,
            int(
                os.environ.get(
                    "ARCREEL_CLOUD_SYNC_INTERVAL_SECONDS",
                    os.environ.get("ACCOUNT_CENTER_SYNC_INTERVAL_SECONDS", "60"),
                )
            ),
        )
        while not self._stop.is_set():
            try:
                async with async_session_factory() as session:
                    result = await session.execute(select(AccountCenterConnection.user_id))
                    user_ids = list(result.scalars())
                for user_id in user_ids:
                    if self._stop.is_set():
                        break
                    await sync_user_connection(user_id)
                from lib.db.models.user import ArcReelCloudSession
                from server.services.arcreel_cloud import sync_cloud_user

                async with async_session_factory() as session:
                    result = await session.execute(select(ArcReelCloudSession.user_id))
                    cloud_user_ids = list(result.scalars())
                for user_id in cloud_user_ids:
                    if self._stop.is_set():
                        break
                    await sync_cloud_user(user_id)
            except Exception:
                logger.exception("账号中心后台配置同步异常")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass


def _load_or_create_device_id() -> str:
    path = app_data_dir() / _DEVICE_ID_FILE
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 8:
            return value
    value = str(uuid.uuid4())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
    return value


def _fernet() -> Fernet:
    path = app_data_dir() / _DEVICE_KEY_FILE
    if path.exists():
        key = path.read_bytes().strip()
    else:
        key = base64.urlsafe_b64encode(os.urandom(32))
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(key)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    return Fernet(key)


def _encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_token(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ValueError("本地设备凭据无法解密；请重新从数据中台进入 ArcReel") from exc


def _optional_secret(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _center_error(response: httpx.Response, fallback: str, fallback_code: str) -> tuple[str, str]:
    try:
        detail = response.json().get("error") or response.json().get("detail") or {}
    except ValueError:
        detail = {}
    if isinstance(detail, dict):
        return str(detail.get("message") or fallback), str(detail.get("code") or fallback_code)
    return fallback, fallback_code
