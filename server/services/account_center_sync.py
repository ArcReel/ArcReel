"""Pull-based desired-state synchronization from the cloud account center."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.app_data_dir import app_data_dir
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ManagedProviderConfigRepository
from lib.config.url_utils import normalize_base_url
from lib.db import async_session_factory
from lib.db.base import utc_now
from lib.db.models.credential import ProviderCredential
from lib.db.models.user import AccountCenterConnection, AccountCenterLoginTicket, User
from lib.db.repositories.credential_repository import CredentialRepository

logger = logging.getLogger(__name__)

_SUPPORTED_SECRET_FIELDS = ("api_key", "access_key", "secret_key")
_CREDENTIAL_FIELDS = frozenset({"api_key", "access_key", "secret_key", "credentials_path", "base_url"})
_VERTEX_CREDENTIALS_FIELD = "credentials_json"
_MAX_VERTEX_CREDENTIALS_BYTES = 1024 * 1024
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
                "fields": [
                    {
                        "key": key,
                        "required": key in meta.required_keys,
                        "type": _provider_field_type(key),
                    }
                    for key in (*meta.required_keys, *meta.optional_keys)
                    if key not in _CREDENTIAL_FIELDS
                ],
                "credential_file": (
                    {"key": _VERTEX_CREDENTIALS_FIELD, "label": "Vertex 服务账号 JSON", "accept": ".json"}
                    if "credentials_path" in meta.required_keys
                    else None
                ),
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
    agent_credential: object = None,
    global_configs: object = None,
) -> None:
    repo = CredentialRepository(session, user_id)
    managed_config_repo = ManagedProviderConfigRepository(session, user_id, management_source)
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
        vertex_json = _optional_secret(item.get(_VERTEX_CREDENTIALS_FIELD))
        if "credentials_path" in meta.required_keys:
            if not vertex_json:
                raise ValueError(f"供应商 {provider_id} 缺少 Vertex 服务账号 JSON")
        elif not groups or not any(all(values.get(key) for key in group) for group in groups):
            raise ValueError(f"供应商 {provider_id} 的凭据字段不完整")
        config_values = _provider_config_values(meta.required_keys, meta.optional_keys, item)
        for required_key in meta.required_keys:
            if required_key not in _CREDENTIAL_FIELDS and not config_values.get(required_key):
                raise ValueError(f"供应商 {provider_id} 缺少必填配置 {required_key}")
        _validate_managed_provider_config(provider_id, config_values)
        configured.add(provider_id)
        managed_result = await session.execute(
            select(ProviderCredential).where(
                ProviderCredential.user_id == user_id,
                ProviderCredential.provider == provider_id,
                ProviderCredential.management_source == management_source,
            )
        )
        credential = managed_result.scalar_one_or_none()
        credentials_path = (
            _materialize_vertex_credentials(user_id, management_source, vertex_json)
            if vertex_json is not None
            else None
        )
        if credential is None:
            credential = await repo.create(
                provider=provider_id,
                name=str(item.get("name") or "数据中台分配")[:128],
                api_key=values.get("api_key"),
                access_key=values.get("access_key"),
                secret_key=values.get("secret_key"),
                base_url=normalize_base_url(_optional_secret(item.get("base_url"))),
            )
            if credentials_path:
                await repo.update(credential.id, credentials_path=credentials_path)
        else:
            updates: dict[str, str | None] = {
                "name": str(item.get("name") or "数据中台分配")[:128],
                "base_url": normalize_base_url(_optional_secret(item.get("base_url"))),
            }
            updates.update(values)
            if credentials_path:
                updates["credentials_path"] = credentials_path
            await repo.update(credential.id, **updates)
        credential.management_source = management_source
        credential.management_revision = revision
        if not credential.is_active:
            await repo.activate(credential.id, provider_id)
            credential.is_active = True
        await managed_config_repo.replace_provider(
            provider_id,
            config_values,
            secret_keys=set(meta.secret_keys),
            revision=revision,
        )

    managed = await session.execute(
        select(ProviderCredential).where(
            ProviderCredential.user_id == user_id,
            ProviderCredential.management_source == management_source,
        )
    )
    for credential in managed.scalars():
        if credential.provider not in configured:
            if credential.credentials_path:
                _remove_managed_vertex_credentials(credential.credentials_path, user_id, management_source)
            await repo.delete(credential.id)
            await managed_config_repo.delete_provider(credential.provider)

    if management_source == "arcreel_cloud":
        await _apply_agent_credential(session, user_id, revision, agent_credential)
        await _apply_character_catalog(session, revision, global_configs)


async def _apply_agent_credential(
    session: AsyncSession,
    user_id: str,
    revision: int,
    raw_credential: object,
) -> None:
    from lib.agent_provider_catalog import get_preset
    from lib.db.models.agent_credential import AgentAnthropicCredential
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    result = await session.execute(
        select(AgentAnthropicCredential).where(
            AgentAnthropicCredential.user_id == user_id,
            AgentAnthropicCredential.management_source == "arcreel_cloud",
        )
    )
    managed = result.scalar_one_or_none()
    repo = AgentCredentialRepository(session)
    if raw_credential is None:
        if managed is not None:
            was_active = managed.is_active
            managed.is_active = False
            await session.flush()
            await session.delete(managed)
            await session.flush()
            if was_active:
                remaining = await repo.list_for_user(user_id)
                if remaining:
                    await repo.set_active(remaining[0].id, user_id)
        return
    if not isinstance(raw_credential, dict):
        raise ValueError("Agent 供应商配置格式无效")
    preset_id = str(raw_credential.get("preset_id") or "").strip()
    preset = get_preset(preset_id)
    is_custom = preset_id == "__custom__"
    if preset is None and not is_custom:
        raise ValueError(f"不支持的 Agent 供应商：{preset_id}")
    api_key = _optional_secret(raw_credential.get("api_key"))
    base_url = _optional_secret(raw_credential.get("base_url")) or (preset.messages_url if preset else None)
    if not api_key:
        raise ValueError("Agent 供应商 API Key 不能为空")
    if not base_url:
        raise ValueError("自定义 Agent 供应商服务地址不能为空")
    values = {
        "preset_id": preset_id,
        "display_name": str(raw_credential.get("display_name") or (preset.display_name if preset else "自定义供应商"))[
            :128
        ],
        "base_url": base_url,
        "api_key": api_key,
        "model": _optional_secret(raw_credential.get("model")) or (preset.default_model if preset else None) or None,
        "haiku_model": _optional_secret(raw_credential.get("haiku_model")),
        "sonnet_model": _optional_secret(raw_credential.get("sonnet_model")),
        "opus_model": _optional_secret(raw_credential.get("opus_model")),
        "subagent_model": _optional_secret(raw_credential.get("subagent_model")),
        "management_revision": revision,
    }
    if managed is None:
        managed = await repo.create(
            user_id=user_id,
            management_source="arcreel_cloud",
            **values,
        )
    else:
        managed = await repo.update(managed.id, user_id=user_id, **values)
    if managed is None:
        raise ValueError("Agent 供应商配置保存失败")
    managed.management_source = "arcreel_cloud"
    await repo.set_active(managed.id, user_id)


async def _apply_character_catalog(
    session: AsyncSession,
    revision: int,
    raw_global_configs: object,
) -> None:
    from lib.character_catalog import validate_character_catalog_url
    from lib.config.repository import SystemSettingRepository

    repo = SystemSettingRepository(session)
    source_key = "croco_characters_management_source"
    revision_key = "croco_characters_management_revision"
    character_catalog = raw_global_configs.get("character_catalog") if isinstance(raw_global_configs, dict) else None
    if character_catalog is None:
        if await repo.get(source_key) == "arcreel_cloud":
            for key in (
                "croco_characters_api_url",
                "croco_characters_api_token",
                source_key,
                revision_key,
            ):
                await repo.delete(key)
        return
    if not isinstance(character_catalog, dict):
        raise ValueError("人物资产渠道配置格式无效")
    api_url = validate_character_catalog_url(str(character_catalog.get("api_url") or "").strip())
    api_token = str(character_catalog.get("api_token") or "").strip()
    if not api_token:
        raise ValueError("人物资产渠道 Token 不能为空")
    await repo.set("croco_characters_api_url", api_url)
    await repo.set("croco_characters_api_token", api_token)
    await repo.set(source_key, "arcreel_cloud")
    await repo.set(revision_key, str(revision))


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


def _provider_config_values(
    required_keys: list[str], optional_keys: list[str], item: dict[object, object]
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in (*required_keys, *optional_keys):
        if key in _CREDENTIAL_FIELDS:
            continue
        value = _optional_secret(item.get(key))
        if value is not None:
            values[key] = value
    return values


def _provider_field_type(key: str) -> str:
    if key.endswith("_url"):
        return "url"
    if key.endswith("_rpm") or key.endswith("_max_workers") or key == "request_gap":
        return "number"
    return "text"


def _validate_managed_provider_config(provider_id: str, values: dict[str, str]) -> None:
    for key in ("image_max_workers", "video_max_workers", "audio_max_workers"):
        if key in values:
            try:
                parsed = int(values[key])
            except ValueError as exc:
                raise ValueError(f"供应商 {provider_id} 的 {key} 必须是正整数") from exc
            if parsed < 1:
                raise ValueError(f"供应商 {provider_id} 的 {key} 必须是正整数")
            values[key] = str(parsed)
    for key in ("image_rpm", "video_rpm", "request_gap"):
        if key in values:
            try:
                parsed = float(values[key])
            except ValueError as exc:
                raise ValueError(f"供应商 {provider_id} 的 {key} 必须是非负数") from exc
            if parsed < 0:
                raise ValueError(f"供应商 {provider_id} 的 {key} 必须是非负数")


def _managed_vertex_directory(user_id: str, management_source: str):
    identity = hashlib.sha256(f"{management_source}:{user_id}".encode()).hexdigest()
    return app_data_dir().parent / "vertex_keys" / "managed" / identity


def _materialize_vertex_credentials(user_id: str, management_source: str, raw_json: str) -> str:
    encoded = raw_json.encode("utf-8")
    if len(encoded) > _MAX_VERTEX_CREDENTIALS_BYTES:
        raise ValueError("Vertex 服务账号 JSON 不能超过 1 MiB")
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Vertex 服务账号 JSON 格式无效") from exc
    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise ValueError("Vertex 服务账号 JSON 缺少 project_id")
    directory = _managed_vertex_directory(user_id, management_source)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "service-account.json"
    temporary = directory / f"service-account.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if os.name == "posix":
        os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    if os.name == "posix":
        os.chmod(destination, 0o600)
    return str(destination)


def _remove_managed_vertex_credentials(raw_path: str, user_id: str, management_source: str) -> None:
    expected_directory = _managed_vertex_directory(user_id, management_source).resolve()
    path = os.path.abspath(raw_path)
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(expected_directory)
    except (OSError, ValueError):
        logger.warning("拒绝删除托管目录之外的 Vertex 凭据文件：%s", raw_path)
        return
    try:
        resolved.unlink(missing_ok=True)
    except OSError:
        logger.warning("无法删除已失效的托管 Vertex 凭据文件：%s", raw_path, exc_info=True)


def _center_error(response: httpx.Response, fallback: str, fallback_code: str) -> tuple[str, str]:
    try:
        detail = response.json().get("error") or response.json().get("detail") or {}
    except ValueError:
        detail = {}
    if isinstance(detail, dict):
        return str(detail.get("message") or fallback), str(detail.get("code") or fallback_code)
    return fallback, fallback_code
