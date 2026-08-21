"""Croco 发布角色目录→ArcReel 全局人物资产的增量同步。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.asset_types import validate_asset_name
from lib.config.service import ConfigService
from lib.db.models.asset import AssetResource
from lib.db.repositories.asset_repo import AssetRepository
from lib.db.repositories.asset_resource_repo import AssetResourceRepository
from lib.httpx_shared import get_http_client
from lib.project_manager import get_project_manager

CROCO_CATALOG_SOURCE = "croco-character-catalog"
CROCO_CHARACTERS_API_URL_SETTING = "croco_characters_api_url"
CROCO_CHARACTERS_API_TOKEN_SETTING = "croco_characters_api_token"

_MAX_ASSET_BYTES = 200 * 1024 * 1024
_MEDIA_BY_SUFFIX = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".flac": "audio",
    ".ogg": "audio",
}
_SUFFIX_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
}
_IMAGE_KEY_RANK = {
    "avatarUrl": 0,
    "fullBodyImageUrl": 1,
    "halfBodyImageUrl": 2,
    "chestImageUrl": 3,
}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


class CharacterCatalogSyncError(RuntimeError):
    """已归类的同步失败；router 只翻译 code，不回显令牌或远端正文。"""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _CatalogVoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tts_voice_id: str | None = Field(default=None, alias="ttsVoiceId")


class _CatalogAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1, max_length=300)
    relative_path: str = Field(alias="relativePath", min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=4000)
    source_fields: list[str] = Field(default_factory=list, alias="sourceFields")
    revision: str | int | None = None
    sha256: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    byte_size: int | None = Field(default=None, alias="byteSize", ge=0, le=_MAX_ASSET_BYTES)


class _CatalogCharacter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    chinese_name: str | None = Field(default=None, alias="chineseName", max_length=200)
    subtitle: str | None = Field(default=None, max_length=1000)
    summary: str | None = Field(default=None, max_length=20_000)
    voice: _CatalogVoice | None = None
    assets: list[_CatalogAsset] = Field(default_factory=list, max_length=200)


class _PublishVersion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    activated_at: str = Field(alias="activatedAt")


class _CharacterCatalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(alias="schemaVersion")
    publish_version: _PublishVersion = Field(alias="publishVersion")
    characters: list[_CatalogCharacter] = Field(max_length=1000)


def _validated_https_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CharacterCatalogSyncError("character_catalog_invalid_url")
    return parsed.geturl()


def validate_character_catalog_url(value: str) -> str:
    """WebUI 入库前与运行时共用的 HTTPS URL 校验。"""

    return _validated_https_url(value)


def _safe_relative_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise CharacterCatalogSyncError("character_catalog_invalid_payload")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise CharacterCatalogSyncError("character_catalog_invalid_payload")
    return path


def _media_type(asset: _CatalogAsset, relative_path: PurePosixPath) -> str | None:
    if relative_path.suffix.lower() in _VIDEO_SUFFIXES:
        return None
    mime = (asset.mime_type or "").lower()
    for prefix in ("image", "audio"):
        if mime.startswith(f"{prefix}/"):
            return prefix
    return _MEDIA_BY_SUFFIX.get(relative_path.suffix.lower())


def _suffix(asset: _CatalogAsset, relative_path: PurePosixPath) -> str:
    suffix = relative_path.suffix.lower()
    if suffix in _MEDIA_BY_SUFFIX:
        return suffix
    return _SUFFIX_BY_MIME.get((asset.mime_type or "").lower(), "")


def _safe_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise CharacterCatalogSyncError("character_catalog_invalid_payload")
    return normalized


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def _download_verified(client: httpx.AsyncClient, asset: _CatalogAsset) -> tuple[bytes, str]:
    url = _validated_https_url(asset.url)
    try:
        response = await client.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CharacterCatalogSyncError("character_catalog_asset_download_failed") from exc

    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_ASSET_BYTES:
                raise CharacterCatalogSyncError("character_catalog_asset_too_large")
        except ValueError:
            pass
    data = response.content
    if len(data) > _MAX_ASSET_BYTES:
        raise CharacterCatalogSyncError("character_catalog_asset_too_large")
    if asset.byte_size is not None and len(data) != asset.byte_size:
        raise CharacterCatalogSyncError("character_catalog_asset_integrity_failed")
    digest = hashlib.sha256(data).hexdigest()
    expected = _safe_sha256(asset.sha256)
    if expected is not None and digest != expected:
        raise CharacterCatalogSyncError("character_catalog_asset_integrity_failed")
    return data, digest


def _resource_is_unchanged(resource: AssetResource, asset: _CatalogAsset) -> bool:
    if resource.source_url != asset.url:
        return False
    expected_sha256 = _safe_sha256(asset.sha256)
    if expected_sha256 is not None and resource.sha256 != expected_sha256:
        return False
    if asset.revision is not None and resource.revision != str(asset.revision):
        return False
    try:
        info = (get_project_manager().projects_root / resource.path).stat()
    except OSError:
        return False
    return asset.byte_size is None or info.st_size == asset.byte_size


async def _available_name(repo: AssetRepository, preferred: str, external_id: str) -> str:
    try:
        base = validate_asset_name(preferred)
    except ValueError:
        base = validate_asset_name(f"Croco {external_id[:12]}")
    if not await repo.exists("character", base):
        return base
    candidate = f"{base} (Croco)"
    index = 2
    while await repo.exists("character", candidate):
        candidate = f"{base} (Croco {index})"
        index += 1
    return validate_asset_name(candidate)


def _primary_resource(resources: list[AssetResource], media_type: str) -> AssetResource | None:
    candidates = [resource for resource in resources if resource.media_type == media_type]
    if not candidates:
        return None
    if media_type == "image":
        return min(candidates, key=lambda item: (_IMAGE_KEY_RANK.get(item.resource_key, 100), item.sort_order))
    return min(candidates, key=lambda item: item.sort_order)


async def _fetch_catalog(client: httpx.AsyncClient, api_url: str, token: str) -> _CharacterCatalog:
    try:
        response = await client.get(
            _validated_https_url(api_url),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CharacterCatalogSyncError("character_catalog_request_failed", status=exc.response.status_code) from exc
    except httpx.HTTPError as exc:
        raise CharacterCatalogSyncError("character_catalog_request_failed") from exc
    try:
        catalog = _CharacterCatalog.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise CharacterCatalogSyncError("character_catalog_invalid_payload") from exc
    if catalog.schema_version != 1:
        raise CharacterCatalogSyncError("character_catalog_invalid_payload")
    return catalog


async def sync_character_catalog(session: AsyncSession) -> dict[str, Any]:
    """从 DB 读取配置并增量同步当前发布目录。

    远端缺席的本地角色不删除；只有本次回包中的角色会按当前版本更新。
    """

    settings = await ConfigService(session).get_all_settings()
    api_url = settings.get(CROCO_CHARACTERS_API_URL_SETTING, "").strip()
    token = settings.get(CROCO_CHARACTERS_API_TOKEN_SETTING, "").strip()
    if not api_url or not token:
        raise CharacterCatalogSyncError("character_catalog_config_missing")

    client = get_http_client()
    catalog = await _fetch_catalog(client, api_url, token)
    asset_repo = AssetRepository(session)
    resource_repo = AssetResourceRepository(session)
    projects_root = get_project_manager().projects_root
    resource_root = get_project_manager().get_global_assets_root() / "character" / "catalog"

    result = {"added": 0, "updated": 0, "unchanged": 0, "assetsDownloaded": 0}
    created_paths: set[str] = set()
    obsolete_paths: set[str] = set()
    live_paths: set[str] = set()

    try:
        for character in catalog.characters:
            existing = await asset_repo.get_by_external_identity(CROCO_CATALOG_SOURCE, character.id)
            is_new = existing is None
            changed = is_new
            if existing is None:
                name = await _available_name(asset_repo, character.chinese_name or character.name, character.id)
                existing = await asset_repo.create(
                    type="character",
                    name=name,
                    description=character.summary or character.subtitle or "",
                    voice_style="",
                    external_source=CROCO_CATALOG_SOURCE,
                    external_id=character.id,
                    voice_id=character.voice.tts_voice_id if character.voice else None,
                )
            else:
                patch = {
                    "voice_id": character.voice.tts_voice_id if character.voice else None,
                }
                if any(getattr(existing, key) != value for key, value in patch.items()):
                    await asset_repo.update(existing.id, **patch)
                    changed = True

            # 新建 Asset 尚未通过 selectin 查询装载 relationship；在 AsyncSession 中
            # 直接访问会触发不受支持的隐式 I/O。新角色显然没有历史资源。
            previous_resources = [] if is_new else list(existing.resources)
            selected_image_id = next(
                (resource.id for resource in previous_resources if resource.path == existing.image_path),
                None,
            )
            selected_audio_id = next(
                (resource.id for resource in previous_resources if resource.path == existing.audio_path),
                None,
            )
            previous_by_key = {
                resource.resource_key: resource for resource in previous_resources if resource.origin == "catalog"
            }
            active_resources = [resource for resource in previous_resources if resource.origin != "catalog"]
            live_paths.update(resource.path for resource in active_resources)
            seen_keys: set[str] = set()
            remote_keys: set[str] = set()
            for order, remote in enumerate(character.assets):
                if remote.key in seen_keys:
                    raise CharacterCatalogSyncError("character_catalog_invalid_payload")
                seen_keys.add(remote.key)
                relative = _safe_relative_path(remote.relative_path)
                media_type = _media_type(remote, relative)
                if media_type is None:
                    continue
                remote_keys.add(remote.key)
                current = previous_by_key.get(remote.key)
                source_fields_json = json.dumps(remote.source_fields, ensure_ascii=False, separators=(",", ":"))
                if current is not None and _resource_is_unchanged(current, remote):
                    metadata_patch = {
                        "origin": "catalog",
                        "media_type": media_type,
                        "mime_type": remote.mime_type,
                        "sort_order": order,
                        "source_fields_json": source_fields_json,
                    }
                    if any(getattr(current, key) != value for key, value in metadata_patch.items()):
                        await resource_repo.update(current, **metadata_patch)
                        changed = True
                    active_resources.append(current)
                    live_paths.add(current.path)
                    continue

                data, digest = await _download_verified(client, remote)
                key_digest = hashlib.sha256(remote.key.encode("utf-8")).hexdigest()[:18]
                target = resource_root / existing.id / f"{key_digest}-{digest[:16]}{_suffix(remote, relative)}"
                rel_target = target.relative_to(projects_root).as_posix()
                target_existed = target.exists()
                await asyncio.to_thread(_atomic_write, target, data)
                if not target_existed:
                    created_paths.add(rel_target)
                live_paths.add(rel_target)
                resource_fields = {
                    "media_type": media_type,
                    "mime_type": remote.mime_type,
                    "path": rel_target,
                    "source_url": remote.url,
                    "sha256": digest,
                    "byte_size": len(data),
                    "revision": str(remote.revision) if remote.revision is not None else None,
                    "sort_order": order,
                    "source_fields_json": source_fields_json,
                }
                if current is None:
                    current = await resource_repo.create(
                        asset_id=existing.id,
                        resource_key=remote.key,
                        origin="catalog",
                        **resource_fields,
                    )
                else:
                    if current.path != rel_target:
                        obsolete_paths.add(current.path)
                    await resource_repo.update(current, **resource_fields)
                active_resources.append(current)
                changed = True
                result["assetsDownloaded"] += 1

            for key, resource in previous_by_key.items():
                if key not in remote_keys:
                    obsolete_paths.add(resource.path)
                    await resource_repo.delete(resource)
                    changed = True

            selected_image = next(
                (
                    resource
                    for resource in active_resources
                    if resource.id == selected_image_id and resource.media_type == "image"
                ),
                None,
            )
            selected_audio = next(
                (
                    resource
                    for resource in active_resources
                    if resource.id == selected_audio_id and resource.media_type == "audio"
                ),
                None,
            )
            primary_image = selected_image or _primary_resource(active_resources, "image")
            primary_audio = selected_audio or _primary_resource(active_resources, "audio")
            primary_patch = {
                "image_path": primary_image.path if primary_image else None,
                "audio_path": primary_audio.path if primary_audio else None,
            }
            if any(getattr(existing, key) != value for key, value in primary_patch.items()):
                await asset_repo.update(existing.id, **primary_patch)
                changed = True

            if is_new:
                result["added"] += 1
            elif changed:
                result["updated"] += 1
            else:
                result["unchanged"] += 1

        await session.commit()
    except Exception:
        await session.rollback()
        for path in created_paths:
            try:
                (projects_root / path).unlink()
            except FileNotFoundError:
                pass
        raise

    for path in obsolete_paths - live_paths:
        try:
            (projects_root / path).unlink()
        except FileNotFoundError:
            pass

    return {
        "publishVersion": catalog.publish_version.model_dump(by_alias=True),
        "remoteCharacters": len(catalog.characters),
        **result,
    }
