from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.character_catalog import CROCO_CATALOG_SOURCE, sync_character_catalog
from lib.config.service import ConfigService
from lib.db.repositories.asset_alias_repo import AssetAliasRepository
from lib.db.repositories.asset_repo import AssetRepository
from lib.db.repositories.asset_resource_repo import AssetResourceRepository
from lib.project_manager import ProjectManager


class _CatalogClient:
    def __init__(self, catalog: dict[str, Any], files: dict[str, bytes]) -> None:
        self.catalog = catalog
        self.files = files
        self.requested_urls: list[str] = []

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        self.requested_urls.append(url)
        request = httpx.Request("GET", url)
        if url == "https://catalog.example.test/export":
            return httpx.Response(200, json=self.catalog, request=request)
        if url in self.files:
            return httpx.Response(200, content=self.files[url], request=request)
        return httpx.Response(404, request=request)


def _remote_asset(key: str, filename: str, url: str, data: bytes, mime_type: str) -> dict[str, Any]:
    return {
        "key": key,
        "relativePath": filename,
        "url": url,
        "sourceFields": [key],
        "revision": "1",
        "sha256": hashlib.sha256(data).hexdigest(),
        "mimeType": mime_type,
        "byteSize": len(data),
    }


def _catalog(
    assets: list[dict[str, Any]],
    *,
    voice_id: str = "voice-v1",
    name: str = "Croco Dad",
    chinese_name: str = "鳄鱼爸爸",
    subtitle: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "publishVersion": {
            "id": "publish-1",
            "name": "Published",
            "activatedAt": "2026-08-21T00:00:00Z",
        },
        "characters": [
            {
                "id": "croco-dad",
                "name": name,
                "chineseName": chinese_name,
                "subtitle": subtitle,
                "summary": "远端角色描述",
                "voice": {"ttsVoiceId": voice_id},
                "assets": assets,
            }
        ],
    }


@pytest.mark.unit
async def test_sync_keeps_all_images_and_audio_but_ignores_video(
    async_session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_a = b"avatar-image"
    image_b = b"full-body-image"
    audio = b"reference-audio"
    video = b"video-must-not-download"
    assets = [
        _remote_asset("avatarUrl", "avatar.png", "https://cdn.example.test/avatar.png", image_a, "image/png"),
        _remote_asset(
            "fullBodyImageUrl", "full-body.png", "https://cdn.example.test/full-body.png", image_b, "image/png"
        ),
        _remote_asset("voiceSample1", "voice.wav", "https://cdn.example.test/voice.wav", audio, "audio/wav"),
        _remote_asset("introVideoUrl", "intro.mp4", "https://cdn.example.test/intro.mp4", video, "video/mp4"),
    ]
    client = _CatalogClient(
        _catalog(assets),
        {
            "https://cdn.example.test/avatar.png": image_a,
            "https://cdn.example.test/full-body.png": image_b,
            "https://cdn.example.test/voice.wav": audio,
        },
    )
    manager = ProjectManager(tmp_path)
    monkeypatch.setattr("lib.character_catalog.get_http_client", lambda: client)
    monkeypatch.setattr("lib.character_catalog.get_project_manager", lambda: manager)
    settings = ConfigService(async_session)
    await settings.set_setting("croco_characters_api_url", "https://catalog.example.test/export")
    await settings.set_setting("croco_characters_api_token", "test-secret")

    progress: list[tuple[int, int]] = []

    async def on_progress(current: int, total: int) -> None:
        progress.append((current, total))

    result = await sync_character_catalog(async_session, progress_callback=on_progress)

    assert result["added"] == 1
    assert result["assetsDownloaded"] == 3
    assert progress == [(0, 1), (1, 1)]
    assert "https://cdn.example.test/intro.mp4" not in client.requested_urls
    async_session.expire_all()
    asset = await AssetRepository(async_session).get_by_external_identity(CROCO_CATALOG_SOURCE, "croco-dad")
    assert asset is not None
    assert asset.voice_id == "voice-v1"
    assert asset.image_path is not None and asset.image_path.endswith(".png")
    assert asset.audio_path is not None and asset.audio_path.endswith(".wav")
    assert [resource.media_type for resource in asset.resources].count("image") == 2
    assert [resource.media_type for resource in asset.resources].count("audio") == 1
    assert all(resource.media_type != "video" for resource in asset.resources)
    assert [(alias.alias, alias.origin) for alias in asset.aliases] == [("Croco Dad", "catalog")]


@pytest.mark.unit
async def test_resync_preserves_user_fields_local_resources_primary_selection_and_remote_absences(
    async_session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_image = b"first-image"
    first_audio = b"first-audio"
    first_assets = [
        _remote_asset("avatarUrl", "avatar.png", "https://cdn.example.test/avatar.png", first_image, "image/png"),
        _remote_asset("voiceSample1", "voice.wav", "https://cdn.example.test/voice.wav", first_audio, "audio/wav"),
    ]
    manager = ProjectManager(tmp_path)
    first_client = _CatalogClient(
        _catalog(first_assets),
        {
            "https://cdn.example.test/avatar.png": first_image,
            "https://cdn.example.test/voice.wav": first_audio,
        },
    )
    monkeypatch.setattr("lib.character_catalog.get_http_client", lambda: first_client)
    monkeypatch.setattr("lib.character_catalog.get_project_manager", lambda: manager)
    settings = ConfigService(async_session)
    await settings.set_setting("croco_characters_api_url", "https://catalog.example.test/export")
    await settings.set_setting("croco_characters_api_token", "test-secret")
    await sync_character_catalog(async_session)

    asset_repo = AssetRepository(async_session)
    resource_repo = AssetResourceRepository(async_session)
    asset = await asset_repo.get_by_external_identity(CROCO_CATALOG_SOURCE, "croco-dad")
    assert asset is not None
    local_path = "_global_assets/character/local-user-image.png"
    (tmp_path / local_path).write_bytes(b"local-user-image")
    local_resource = await resource_repo.create(
        asset_id=asset.id,
        resource_key="local:user-image",
        origin="local",
        media_type="image",
        mime_type="image/png",
        path=local_path,
    )
    local_alias = await AssetAliasRepository(async_session).create(
        asset_id=asset.id,
        alias="用户自定义旧称",
        origin="local",
    )
    await asset_repo.update(
        asset.id,
        name="用户改名",
        description="用户自己的描述",
        voice_style="用户自己的声音风格",
        image_path=local_path,
    )
    absent = await asset_repo.create(
        type="character",
        name="远端已下架但本地保留",
        external_source=CROCO_CATALOG_SOURCE,
        external_id="remote-absent",
    )
    absent_id = absent.id
    await async_session.commit()
    # 模拟下一次 HTTP 同步请求使用新 session，从数据库重新装载资源关系。
    async_session.expire_all()

    updated_image = b"updated-image"
    updated_audio = b"updated-audio"
    second_assets = [
        _remote_asset(
            "fullBodyImageUrl",
            "full-body.png",
            "https://cdn.example.test/full-body-v2.png",
            updated_image,
            "image/png",
        ),
        _remote_asset(
            "voiceSample1", "voice-v2.wav", "https://cdn.example.test/voice-v2.wav", updated_audio, "audio/wav"
        ),
    ]
    second_client = _CatalogClient(
        _catalog(
            second_assets,
            voice_id="voice-v2",
            name="Benny Stone",
            chinese_name="布爸",
            subtitle="鳄鱼爸爸",
        ),
        {
            "https://cdn.example.test/full-body-v2.png": updated_image,
            "https://cdn.example.test/voice-v2.wav": updated_audio,
        },
    )
    monkeypatch.setattr("lib.character_catalog.get_http_client", lambda: second_client)

    result = await sync_character_catalog(async_session)

    assert result["updated"] == 1
    async_session.expire_all()
    refreshed = await asset_repo.get_by_external_identity(CROCO_CATALOG_SOURCE, "croco-dad")
    assert refreshed is not None
    assert refreshed.name == "用户改名"
    assert refreshed.description == "用户自己的描述"
    assert refreshed.voice_style == "用户自己的声音风格"
    assert refreshed.voice_id == "voice-v2"
    assert refreshed.image_path == local_path
    assert any(resource.id == local_resource.id and resource.origin == "local" for resource in refreshed.resources)
    assert {(alias.alias, alias.origin) for alias in refreshed.aliases} == {
        ("用户自定义旧称", "local"),
        ("布爸", "catalog"),
        ("Benny Stone", "catalog"),
        ("鳄鱼爸爸", "catalog"),
    }
    assert any(alias.id == local_alias.id for alias in refreshed.aliases)
    assert (tmp_path / local_path).read_bytes() == b"local-user-image"
    assert await asset_repo.get_by_id(absent_id) is not None
