"""供应商模型接口：列出、启用与停用、按 ID 添加与移除、Sheet 的供应商设置与模型信息，绑内存库。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_catalog_core import EntryKey
from lib.catalog import query as catalog
from lib.config.retired_model_ids import GLOBAL_MODEL_SETTING_KEYS
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.routers import catalog as catalog_router
from server.routers import provider_models
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.catalog_support import install_catalog
from tests.http_capture import capture_http


@pytest.fixture
def models_client(db_factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    app = FastAPI()

    async def _override_session():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    override_auth(app)
    app.include_router(provider_models.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(catalog_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    with TestClient(app) as test_client:
        yield test_client
    # 添加与移除、修改模型信息会换掉目录查询里进程级的用户添加模型与用户条目副本
    catalog.install_user_added_models(())
    catalog.install_user_entries(())


def _models(client: TestClient, provider_id: str) -> dict[str, dict]:
    resp = client.get(f"/api/v1/providers/{provider_id}/models")
    assert resp.status_code == 200
    return {item["model_id"]: item for item in resp.json()["models"]}


async def _disabled(db_factory: async_sessionmaker[AsyncSession], provider_id: str) -> frozenset[str]:
    async with db_factory() as session:
        statuses = await ConfigService(session).get_all_providers_status()
    return next(s.disabled_models for s in statuses if s.name == provider_id)


def test_builtin_list_has_state_flags_and_price_summary(models_client: TestClient) -> None:
    models = _models(models_client, "agnes")

    default = models["agnes-3.0-flash"]
    assert (default["enabled"], default["is_default"], default["official"], default["flags"]) == (
        True,
        True,
        True,
        [],
    )
    assert default["price"] == {
        "currency": "USD",
        "items": [
            {"meter": "uncached_input_tokens", "min_price": 0.05, "max_price": 0.05, "per": 1_000_000},
            {"meter": "output_tokens", "min_price": 0.15, "max_price": 0.15, "per": 1_000_000},
        ],
    }
    # 条目已下架的官方收录模型：没有状态行时为停用
    legacy = models["agnes-2.0-flash"]
    assert (legacy["enabled"], legacy["is_default"]) == (False, False)
    assert "discontinued" in legacy["flags"]
    assert models_client.get("/api/v1/providers/nope/models").status_code == 404


async def test_disabling_default_model_takes_effect_immediately(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    resp = models_client.put(
        "/api/v1/providers/agnes/models/enabled", json={"model_id": "agnes-3.0-flash", "enabled": False}
    )

    assert resp.status_code == 200
    assert (resp.json()["enabled"], resp.json()["is_default"]) == (False, False)
    assert "agnes-3.0-flash" in await _disabled(db_factory, "agnes")

    models_client.put("/api/v1/providers/agnes/models/enabled", json={"model_id": "agnes-2.0-flash", "enabled": True})
    models = _models(models_client, "agnes")
    assert models["agnes-2.0-flash"]["enabled"] is True
    assert "agnes-2.0-flash" not in await _disabled(db_factory, "agnes")
    missing = models_client.put("/api/v1/providers/agnes/models/enabled", json={"model_id": "nope", "enabled": True})
    assert missing.status_code == 404


def test_add_by_id_matches_listing_exactly_and_by_media_type(models_client: TestClient) -> None:
    # 两栖模型：同一 API 模型名有图片与视频两个供应商模型，要给媒体类型
    assert models_client.post("/api/v1/providers/kling/models", json={"model_id": "kling-v3-omni"}).status_code == 422

    models_client.put(
        "/api/v1/providers/kling/models/enabled", json={"model_id": "kling-v3-omni-image", "enabled": False}
    )
    resp = models_client.post(
        "/api/v1/providers/kling/models", json={"model_id": "kling-v3-omni", "media_type": "image"}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["official"] is True
    assert (body["model"]["model_id"], body["model"]["enabled"]) == ("kling-v3-omni-image", True)


def test_add_from_catalog_enables_the_official_model_whose_api_name_differs_from_the_entry(
    models_client: TestClient,
) -> None:
    """「模型目录」里选的条目对应这个供应商官方收录的模型（API 模型名与官方模型名不同）：启用它，不另建用户添加的模型。"""
    models_client.put(
        "/api/v1/providers/ark/models/enabled", json={"model_id": "doubao-seedance-2-0-260128", "enabled": False}
    )

    resp = models_client.post(
        "/api/v1/providers/ark/models",
        json={
            "model_id": "doubao-seedance-2.0",
            "media_type": "video",
            "entry_binding": "bytedance/doubao-seedance-2.0",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["official"] is True
    assert (body["model"]["model_id"], body["model"]["enabled"]) == ("doubao-seedance-2-0-260128", True)
    assert "doubao-seedance-2.0" not in _models(models_client, "ark")


def test_add_unlisted_model_as_user_added_then_remove(models_client: TestClient) -> None:
    resp = models_client.post("/api/v1/providers/ark/models", json={"model_id": "my-private-ep", "media_type": "text"})

    assert resp.status_code == 201
    added = resp.json()
    assert added["official"] is False
    assert (added["model"]["display_name"], added["model"]["flags"]) == (None, ["unpriced"])
    # 用户添加的模型并进目录查询：设置里引用它可以解析
    assert catalog.get_model("ark", "my-private-ep", media_type="text") is not None
    assert models_client.post("/api/v1/providers/ark/models", json={"model_id": "my-private-ep"}).status_code == 422

    assert models_client.delete("/api/v1/providers/ark/models/my-private-ep").status_code == 204
    assert "my-private-ep" not in _models(models_client, "ark")
    assert catalog.get_model("ark", "my-private-ep") is None


def test_user_added_model_matches_entry_by_name(models_client: TestClient) -> None:
    resp = models_client.post("/api/v1/providers/grok/models", json={"model_id": "google/gemini-3-flash-preview"})

    assert resp.status_code == 201
    model = resp.json()["model"]
    assert (model["media_type"], model["display_name"], model["flags"]) == ("text", _flash_name(), [])
    assert model["price"]["currency"] == "USD"


def test_add_by_id_infers_media_type_by_name_when_no_entry_matches(models_client: TestClient) -> None:
    resp = models_client.post("/api/v1/providers/ark/models", json={"model_id": "acme-video-x1"})

    assert resp.status_code == 201
    assert (resp.json()["model"]["media_type"], resp.json()["official"]) == ("video", False)


def test_official_model_cannot_be_removed_and_unsupported_media_type_is_rejected(models_client: TestClient) -> None:
    assert models_client.delete("/api/v1/providers/agnes/models/agnes-3.0-flash").status_code == 422
    assert models_client.delete("/api/v1/providers/agnes/models/nope").status_code == 404
    # 供应商不提供这类模型
    resp = models_client.post(
        "/api/v1/providers/openai/models", json={"model_id": "mystery-clip", "media_type": "video"}
    )
    assert resp.status_code == 422


async def test_custom_provider_list_and_disable_clears_default(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="k",
            models=[
                {
                    "model_id": "anthropic/gemini-3-flash-preview",
                    "display_name": "anthropic/gemini-3-flash-preview",
                    "endpoint": "openai-chat",
                    "is_default": True,
                    "is_enabled": True,
                },
                {"model_id": "house-model", "display_name": "自家模型", "endpoint": "openai-chat", "is_enabled": True},
            ],
        )
        await session.commit()
        provider_id = f"custom-{provider.id}"

    models = _models(models_client, provider_id)
    matched = models["anthropic/gemini-3-flash-preview"]
    assert (matched["display_name"], matched["is_default"], matched["endpoint"], matched["flags"]) == (
        _flash_name(),
        True,
        "openai-chat",
        [],
    )
    house = models["house-model"]
    assert (house["display_name"], house["flags"], house["price"]) == ("自家模型", ["unpriced"], None)

    # 启停改变这个供应商开放的媒体类型，worker 的容量表要随之刷新
    worker = SimpleNamespace(reload_limits=AsyncMock())
    cast("FastAPI", models_client.app).state.generation_worker = worker
    resp = models_client.put(
        f"/api/v1/providers/{provider_id}/models/enabled",
        json={"model_id": "anthropic/gemini-3-flash-preview", "enabled": False},
    )
    assert (resp.json()["enabled"], resp.json()["is_default"]) == (False, False)
    worker.reload_limits.assert_awaited_once()
    async with db_factory() as session:
        row = await CustomProviderRepository(session).get_model_by_ids(provider.id, "anthropic/gemini-3-flash-preview")
    assert row is not None
    assert (row.is_enabled, row.is_default) == (False, False)


# ---------------------------------------------------------------------------
# 供应商模型 Sheet：读取单个、保存供应商设置、移除前查引用
# ---------------------------------------------------------------------------


def _add_user_model(client: TestClient, provider_id: str, model_id: str, media_type: str) -> None:
    resp = client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": model_id, "media_type": media_type})
    assert resp.status_code == 201


def test_user_added_video_model_overrides_take_effect_at_runtime(models_client: TestClient) -> None:
    from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

    _add_user_model(models_client, "ark", "my-video-ep", "video")

    detail = models_client.get("/api/v1/providers/ark/models/my-video-ep").json()
    caps = detail["capabilities"]
    # 对应不上条目：可选时长取按名字推断的预设，分辨率不加约束；通道事实取调用通道的声明
    assert (caps["base"]["durations"], caps["base"]["resolutions"], caps["base"]["audio_track"]) == (
        [4, 8],
        None,
        "controllable",
    )
    assert {"durations", "duration_constraints", "resolutions", "audio_track"} <= set(caps["editable"])
    assert (detail["multiplier"], detail["entry"]) == (1.0, None)

    resp = models_client.patch(
        "/api/v1/providers/ark/models/my-video-ep",
        json={
            "multiplier": 0.5,
            "capability_overrides": {
                "durations": [10, 5],
                "resolutions": ["720p", "1080p"],
                "duration_constraints": {"by_resolution": {"1080P": [5]}},
                "audio_track": "always_off",
            },
        },
    )

    assert resp.status_code == 200
    saved = resp.json()
    assert saved["multiplier"] == 0.5
    assert saved["capabilities"]["effective"]["durations"] == [5, 10]
    assert saved["capabilities"]["base"]["durations"] == [4, 8]
    # 运行时：模型事实经目录查询、通道事实经调用通道一侧的能力声明、倍率经计价依据，都按覆盖生效
    model = catalog.get_model("ark", "my-video-ep", media_type="video")
    assert model is not None
    assert (model.supported_durations, model.resolutions, model.duration_resolution_constraints) == (
        [5, 10],
        ["720p", "1080p"],
        {"1080p": [5]},
    )
    assert builtin_video_capabilities_for_model("ark", "my-video-ep").audio_track.value == "always_off"
    price = catalog.provider_model_price("ark", "my-video-ep", "video")
    assert price is not None
    assert price.multiplier == 0.5

    # 清除覆盖即回到原值
    models_client.patch("/api/v1/providers/ark/models/my-video-ep", json={"capability_overrides": None})
    cleared = catalog.get_model("ark", "my-video-ep", media_type="video")
    assert cleared is not None
    assert cleared.supported_durations == [4, 8]


def test_official_model_settings_only_enable(models_client: TestClient) -> None:
    detail = models_client.get("/api/v1/providers/agnes/models/agnes-3.0-flash").json()
    assert (detail["multiplier"], detail["capabilities"]["editable"]) == (None, [])
    assert detail["entry"]["display_name"]

    for body in ({"multiplier": 2}, {"capability_overrides": {"image_input": False}}, {"model_id": "renamed"}):
        assert models_client.patch("/api/v1/providers/agnes/models/agnes-3.0-flash", json=body).status_code == 422
    # 内置供应商没有默认与调用端点这类设置
    resp = models_client.patch("/api/v1/providers/agnes/models/agnes-3.0-flash", json={"is_default": True})
    assert resp.status_code == 422

    resp = models_client.patch("/api/v1/providers/agnes/models/agnes-3.0-flash", json={"enabled": False})
    assert (resp.status_code, resp.json()["model"]["enabled"]) == (200, False)


def test_user_added_overrides_are_validated_by_media_type(models_client: TestClient) -> None:
    _add_user_model(models_client, "ark", "my-text-ep", "text")
    url = "/api/v1/providers/ark/models/my-text-ep"

    assert models_client.patch(url, json={"capability_overrides": {"durations": [5]}}).status_code == 422
    assert models_client.patch(url, json={"capability_overrides": {"max_output_tokens": 0}}).status_code == 422

    resp = models_client.patch(url, json={"capability_overrides": {"image_input": True, "max_output_tokens": 4096}})
    assert resp.status_code == 200
    model = catalog.get_model("ark", "my-text-ep", media_type="text")
    assert model is not None
    assert ("vision" in model.capabilities, model.max_output_tokens) == (True, 4096)


def test_user_added_model_rename(models_client: TestClient) -> None:
    _add_user_model(models_client, "ark", "my-text-ep", "text")

    resp = models_client.patch("/api/v1/providers/ark/models/my-text-ep", json={"model_id": "my-text-ep-v2"})

    assert (resp.status_code, resp.json()["model"]["model_id"]) == (200, "my-text-ep-v2")
    assert models_client.get("/api/v1/providers/ark/models/my-text-ep").status_code == 404
    assert catalog.get_model("ark", "my-text-ep-v2", media_type="text") is not None
    # 改成官方收录的模型名被拒绝
    official = next(m for m in _models(models_client, "ark").values() if m["official"])
    resp = models_client.patch(
        "/api/v1/providers/ark/models/my-text-ep-v2", json={"model_id": official["api_model_name"]}
    )
    assert resp.status_code == 422


async def _custom_provider(db_factory: async_sessionmaker[AsyncSession], models: list[dict]) -> int:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="k",
            models=models,
        )
        await session.commit()
        return provider.id


async def test_custom_unmatched_video_model_detail_and_settings(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    db_id = await _custom_provider(
        db_factory,
        [
            {"model_id": "house-video", "display_name": "house-video", "endpoint": "newapi-video", "is_enabled": True},
            {
                "model_id": "other-video",
                "display_name": "other-video",
                "endpoint": "newapi-video",
                "is_enabled": True,
                "is_default": True,
            },
        ],
    )
    url = f"/api/v1/providers/custom-{db_id}/models/house-video"

    detail = models_client.get(url).json()
    # 媒体类型取调用端点，原值为运行时回落的结果
    caps = detail["capabilities"]
    assert (caps["media_type"], caps["base"]["durations"], detail["endpoint"]) == ("video", [4, 8], "newapi-video")
    assert "durations" in caps["editable"]
    assert detail["requests"]

    resp = models_client.patch(
        url,
        json={"is_default": True, "multiplier": 2, "capability_overrides": {"durations": [6, 12]}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert (body["model"]["is_default"], body["multiplier"]) == (True, 2.0)
    assert body["capabilities"]["effective"]["durations"] == [6, 12]
    # 设为默认时，同一默认分组里原来的默认被取消
    models = _models(models_client, f"custom-{db_id}")
    assert models["other-video"]["is_default"] is False

    resp = models_client.patch(url, json={"capability_overrides": {"image_input": True}})
    assert resp.status_code == 422


async def test_custom_model_rename_rewrites_and_remove_clears_references(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    db_id = await _custom_provider(
        db_factory,
        [
            {"model_id": "house-model", "display_name": "house-model", "endpoint": "openai-chat", "is_enabled": True},
            {"model_id": "spare", "display_name": "spare", "endpoint": "openai-chat", "is_enabled": True},
        ],
    )
    provider_id = f"custom-{db_id}"
    async with db_factory() as session:
        await ConfigService(session).set_setting("default_text_backend", f"{provider_id}/house-model")
        await session.commit()
    from lib.project.project_manager import get_project_manager

    pm = get_project_manager()
    pm.create_project("demo")
    pm.create_project_metadata("demo", title="演示")
    pm.update_project("demo", lambda p: p.update({"text_backend_simple": f"{provider_id}/house-model"}))

    refs = models_client.get(f"/api/v1/providers/{provider_id}/model-references", params={"model_id": "house-model"})
    assert refs.json() == {"settings": ["default_text_backend"], "projects": [{"name": "demo", "title": "演示"}]}

    resp = models_client.patch(f"/api/v1/providers/{provider_id}/models/house-model", json={"model_id": "house-v2"})
    # 显示名跟着模型 ID 走的行，改名后仍显示新的模型 ID
    assert (resp.status_code, resp.json()["model"]["model_id"], resp.json()["model"]["display_name"]) == (
        200,
        "house-v2",
        None,
    )
    assert (
        models_client.patch(f"/api/v1/providers/{provider_id}/models/house-v2", json={"model_id": "spare"}).status_code
        == 422
    )
    async with db_factory() as session:
        assert await ConfigService(session).get_setting("default_text_backend", "") == f"{provider_id}/house-v2"
    assert pm.load_project("demo")["text_backend_simple"] == f"{provider_id}/house-v2"

    assert models_client.delete(f"/api/v1/providers/{provider_id}/models/house-v2").status_code == 204
    async with db_factory() as session:
        assert await ConfigService(session).get_setting("default_text_backend", "") == ""
    assert "text_backend_simple" not in pm.load_project("demo")

    assert models_client.delete(f"/api/v1/providers/{provider_id}/models/spare").status_code == 204
    assert set(_models(models_client, provider_id)) == set()


# ---------------------------------------------------------------------------
# 供应商模型 Sheet：模型信息（用户修改层）
# ---------------------------------------------------------------------------

_FLASH = {"maker": "google", "name": "gemini-3-flash-preview", "media_type": "text"}
_FLASH_URL = "/api/v1/providers/gemini-aistudio/models/gemini-3-flash-preview"


def _flash():
    """官方数据里的 Gemini 3 Flash 条目。期望值从它读，出厂快照随目录数据更新时测试不必改。"""
    entry = catalog.get_entry(EntryKey("google", "gemini-3-flash-preview", "text"))
    assert entry is not None
    return entry


def _flash_name() -> str:
    return _flash().display_name


async def _share_flash(db_factory: async_sessionmaker[AsyncSession]) -> str:
    """让 Vertex AI（配好密钥）与一个中转站也使用 Gemini 3 Flash 的条目，返回中转站的供应商 ID。"""
    async with db_factory() as session:
        await CredentialRepository(session).create("gemini-vertex", "k", api_key="k")
        await session.commit()
    db_id = await _custom_provider(
        db_factory,
        [
            {
                "model_id": "anthropic/gemini-3-flash-preview",
                "display_name": "anthropic/gemini-3-flash-preview",
                "endpoint": "openai-chat",
                "is_enabled": True,
            }
        ],
    )
    return f"custom-{db_id}"


async def test_modify_official_entry_then_restore_official_data(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    relay = await _share_flash(db_factory)
    official = _flash()  # 修改之后目录查询读到的是修改层，先取官方值

    entry = models_client.get(_FLASH_URL).json()["entry"]
    assert (entry["source"], entry["official"], entry["display_name"]) == ("official", None, official.display_name)
    assert entry["shared_with"] == [
        {"provider_id": "gemini-vertex", "display_name": "Vertex AI"},
        {"provider_id": relay, "display_name": "中转站"},
    ]

    # 供应商设置与模型信息在同一个请求里保存
    resp = models_client.patch(
        _FLASH_URL,
        json={
            "enabled": False,
            "entry": {**_FLASH, "display_name": "My Flash", "facts": {"image_input": False, "max_output_tokens": 1000}},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert (body["model"]["enabled"], body["model"]["display_name"]) == (False, "My Flash")
    assert "modified" in body["model"]["flags"]
    assert body["entry"]["source"] == "modified"
    assert body["entry"]["facts"]["max_output_tokens"] == 1000
    assert body["entry"]["official"] == {
        "display_name": official.display_name,
        "facts": {
            "image_input": official.facts.image_input,
            "structured_output": official.facts.structured_output,
            "max_output_tokens": official.facts.max_output_tokens,
        },
        "price": entry["price"],
    }
    # 停止跟随官方：共用这个条目的供应商一起变化，运行时读到的也是修改后的信息
    vertex = _models(models_client, "gemini-vertex")["gemini-3-flash-preview"]
    assert (vertex["display_name"], "modified" in vertex["flags"]) == ("My Flash", True)
    relay_model = _models(models_client, relay)["anthropic/gemini-3-flash-preview"]
    assert relay_model["display_name"] == "My Flash"
    runtime = catalog.get_model("gemini-vertex", "gemini-3-flash-preview")
    assert runtime is not None
    assert (runtime.max_output_tokens, "vision" in runtime.capabilities) == (1000, False)

    resp = models_client.post("/api/v1/catalog/entries/restore-official", json=_FLASH)

    assert resp.status_code == 204
    entry = models_client.get(_FLASH_URL).json()["entry"]
    assert (entry["source"], entry["display_name"], entry["facts"]["max_output_tokens"]) == (
        "official",
        official.display_name,
        official.facts.max_output_tokens,
    )
    vertex = _models(models_client, "gemini-vertex")["gemini-3-flash-preview"]
    assert (vertex["display_name"], "modified" in vertex["flags"]) == (official.display_name, False)
    runtime = catalog.get_model("gemini-vertex", "gemini-3-flash-preview")
    assert runtime is not None
    assert runtime.max_output_tokens == official.facts.max_output_tokens


def test_entry_change_is_saved_atomically_with_provider_settings(models_client: TestClient) -> None:
    # 官方收录的模型不接受倍率：整个请求被拒，模型信息也不写入
    resp = models_client.patch(_FLASH_URL, json={"multiplier": 2, "entry": {**_FLASH, "display_name": "My Flash"}})

    assert resp.status_code == 422
    entry = models_client.get(_FLASH_URL).json()["entry"]
    assert (entry["source"], entry["display_name"]) == ("official", _flash_name())


def test_entry_edit_is_validated_and_must_target_the_mapped_entry(models_client: TestClient) -> None:
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "facts": {"max_output_tokens": 0}}})
    assert resp.status_code == 422

    other = {**_FLASH, "name": "gemini-3.1-pro-preview"}
    resp = models_client.patch(_FLASH_URL, json={"entry": {**other, "display_name": "x"}})
    assert resp.status_code == 409

    # 与当前条目相同的修改不写副本，条目照旧跟随官方
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "display_name": _flash_name()}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["source"] == "official"


def test_entry_price_table_is_shown_edited_cleared_and_compared_with_official(models_client: TestClient) -> None:
    entry = models_client.get(_FLASH_URL).json()["entry"]
    official_price = entry["price"]
    assert official_price["currency"] == "USD"
    assert {meter["meter"] for meter in official_price["meters"]} >= {"uncached_input_tokens", "output_tokens"}

    new_price = {
        "currency": "CNY",
        "meters": [
            {
                "meter": "uncached_input_tokens",
                "dims": ["context_tier"],
                "rows": [{"when": {"context_tier": 0}, "price": 1.5}, {"when": {"context_tier": 100000}, "price": 3}],
            },
            {"meter": "output_tokens", "dims": [], "rows": [{"when": {}, "price": 9}]},
        ],
    }
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "price": new_price}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"]["source"] == "modified"
    assert body["entry"]["price"] == new_price
    assert body["entry"]["official"]["price"] == official_price
    assert body["model"]["price"]["currency"] == "CNY"
    # 价目表是整份替换，运行时计价读到的也是新价目表
    runtime_entry = catalog.get_entry(EntryKey("google", "gemini-3-flash-preview", "text"))
    assert runtime_entry is not None
    assert runtime_entry.price is not None
    assert runtime_entry.price.currency == "CNY"

    # 价格栏目没有传时不改价目表
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "display_name": "My Flash"}})
    assert resp.json()["entry"]["price"] == new_price

    # null 清除价目表：调用费用记为未定价
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "price": None}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["price"] is None
    assert "unpriced" in resp.json()["model"]["flags"]


def test_entry_price_table_is_validated(models_client: TestClient) -> None:
    overlapping = {
        "currency": "USD",
        "meters": [
            {
                "meter": "output_tokens",
                "dims": ["service_tier"],
                "rows": [{"when": {}, "price": 1}, {"when": {"service_tier": "flex"}, "price": 2}],
            }
        ],
    }

    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "price": overlapping}})

    assert resp.status_code == 422
    assert models_client.get(_FLASH_URL).json()["entry"]["source"] == "official"


def test_restore_rejects_entries_without_official_data(models_client: TestClient) -> None:
    resp = models_client.post(
        "/api/v1/catalog/entries/restore-official", json={"maker": "acme", "name": "nope", "media_type": "text"}
    )

    assert resp.status_code == 404
    # 没有副本时恢复什么都不做
    assert models_client.post("/api/v1/catalog/entries/restore-official", json=_FLASH).status_code == 204


# ---------------------------------------------------------------------------
# 获取模型与按 ID 添加
# ---------------------------------------------------------------------------


def _model_list(*model_ids: str) -> dict:
    return {
        "object": "list",
        "data": [{"id": mid, "object": "model", "created": 0, "owned_by": "relay"} for mid in model_ids],
    }


async def _create_relay(db_factory: async_sessionmaker[AsyncSession]) -> str:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转站",
            discovery_format="openai",
            base_url="https://relay.example.com/v1",
            api_key="k",
        )
        await session.commit()
        return f"custom-{provider.id}"


async def test_custom_discovery_matches_entries_and_infers_default_endpoints(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 图片条目带一个不含图片语义的模型别名：按名字会推成文本，对应上条目后应按条目的媒体类型选端点
    data = catalog.factory_snapshot()
    entries = tuple(
        replace(e, aliases=(*e.aliases, "house-painter")) if e.name == "gpt-image-2" else e for e in data.entries
    )
    install_catalog(monkeypatch, replace(data, entries=entries))
    provider_id = await _create_relay(db_factory)

    with capture_http() as http:
        http.get("https://relay.example.com/v1/models").respond(
            json=_model_list("openai/gpt-5.5", "house-painter", "acme-video-pro", "house-chat")
        )
        resp = models_client.post(f"/api/v1/providers/{provider_id}/models/discover")

    assert resp.status_code == 200
    rows = {m["model_id"]: m for m in resp.json()["models"]}
    summary = {
        mid: (m["recognized"], m["media_type"], m["endpoint"], (m["matched_entry"] or {}).get("name"))
        for mid, m in rows.items()
    }
    assert summary == {
        "openai/gpt-5.5": (True, "text", "openai-chat", "gpt-5.5"),
        "house-painter": (True, "image", "openai-images", "gpt-image-2"),
        "acme-video-pro": (False, "video", "openai-video", None),
        "house-chat": (False, "text", "openai-chat", None),
    }
    # 选了其他媒体类型的「对应模型」时换用的端点
    assert rows["house-chat"]["endpoint_by_media_type"]["video"] == "openai-video"


async def test_custom_discovery_failure_reports_reason(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    provider_id = await _create_relay(db_factory)

    with capture_http() as http:
        http.get("https://relay.example.com/v1/models").respond(401, json={"error": {"message": "bad key"}})
        resp = models_client.post(f"/api/v1/providers/{provider_id}/models/discover")

    assert resp.status_code == 502
    assert "bad key" in resp.json()["detail"]


async def test_builtin_discovery_matches_listing_exactly(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with db_factory() as session:
        await CredentialRepository(session).create(provider="dashscope", name="k", api_key="sk-test")
        await session.commit()

    with capture_http() as http:
        route = http.get("https://dashscope.aliyuncs.com/compatible-mode/v1/models").respond(
            json=_model_list("qwen3-max", "Qwen3-Max", "qwen-vl-max-latest")
        )
        resp = models_client.post("/api/v1/providers/dashscope/models/discover")

    assert route.call_count == 1
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"
    rows = {m["model_id"]: m for m in resp.json()["models"]}
    # 只按清单中的 API 模型名精确匹配：大小写不同即未识别
    assert {mid: (m["recognized"], m["model_key"], m["media_type"]) for mid, m in rows.items()} == {
        "qwen3-max": (True, "qwen3-max", "text"),
        "Qwen3-Max": (False, None, "text"),
        "qwen-vl-max-latest": (False, None, "text"),
    }


async def test_builtin_discovery_unlisted_model_returns_the_entry_its_name_maps_to(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 清单外、名字能对应条目的模型带上对应条目与是否已定价；没有价格的条目标为未定价，对应不上的没有条目
    data = catalog.factory_snapshot()
    entries = tuple(replace(e, price=None) if e.name == "qwen3-max" else e for e in data.entries)
    install_catalog(monkeypatch, replace(data, entries=entries))
    async with db_factory() as session:
        await CredentialRepository(session).create(provider="dashscope", name="k", api_key="sk-test")
        await session.commit()

    with capture_http() as http:
        http.get("https://dashscope.aliyuncs.com/compatible-mode/v1/models").respond(
            json=_model_list("gpt-5.5", "Qwen3-Max", "acme-x")
        )
        resp = models_client.post("/api/v1/providers/dashscope/models/discover")

    assert resp.status_code == 200
    rows = {m["model_id"]: m for m in resp.json()["models"]}
    assert {
        mid: (m["recognized"], (m["matched_entry"] or {}).get("name"), (m["matched_entry"] or {}).get("priced"))
        for mid, m in rows.items()
    } == {
        "gpt-5.5": (False, "gpt-5.5", True),
        "Qwen3-Max": (False, "qwen3-max", False),
        "acme-x": (False, None, None),
    }
    assert rows["gpt-5.5"]["display_name"] == rows["gpt-5.5"]["matched_entry"]["display_name"]


def test_builtin_discovery_without_list_api_or_credentials(models_client: TestClient) -> None:
    ark = models_client.post("/api/v1/providers/ark/models/discover").json()
    assert (ark["not_applicable"], ark["models"]) == (True, [])
    assert models_client.post("/api/v1/providers/openai/models/discover").status_code == 422
    listed = models_client.get("/api/v1/providers/openai/models").json()
    assert listed["discoverable"] is True
    assert models_client.get("/api/v1/providers/ark/models").json()["discoverable"] is False


async def test_custom_add_by_id_infers_endpoint_and_takes_default(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    provider_id = await _create_relay(db_factory)

    first = models_client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "openai/gpt-5.5"})
    second = models_client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "house-chat"})
    bound = models_client.post(
        f"/api/v1/providers/{provider_id}/models",
        json={"model_id": "relay-omni", "media_type": "image", "entry_binding": "kuaishou/kling-v3-omni"},
    )

    assert first.status_code == second.status_code == bound.status_code == 201
    assert (first.json()["model"]["endpoint"], first.json()["model"]["is_default"]) == ("openai-chat", True)
    # 同一媒体类型已有默认模型
    assert (second.json()["model"]["endpoint"], second.json()["model"]["is_default"]) == ("openai-chat", False)
    # 有条目绑定时在条目的媒体类型内选端点，显示名取条目
    assert (bound.json()["model"]["endpoint"], bound.json()["model"]["media_type"]) == ("openai-images", "image")
    assert bound.json()["model"]["flags"] == []
    duplicate = models_client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": "house-chat"})
    assert duplicate.status_code == 422
    # 两栖模型不限媒体类型对应不上条目时，按调用方给的媒体类型取条目，不按名字推断成视频
    omni = models_client.post(
        f"/api/v1/providers/{provider_id}/models", json={"model_id": "kling-v3-omni", "media_type": "image"}
    )
    assert omni.status_code == 201
    assert omni.json()["model"]["media_type"] == "image"
    # 显式给的调用端点与关联条目的媒体类型不一致：拒绝，不留下对不上的关联
    mismatched = models_client.post(
        f"/api/v1/providers/{provider_id}/models",
        json={"model_id": "relay-chat", "endpoint": "openai-chat", "entry_binding": "kuaishou/kling-v3-omni"},
    )
    assert mismatched.status_code == 422
    assert "kuaishou/kling-v3-omni" in mismatched.json()["detail"]
    assert (
        models_client.post(
            "/api/v1/providers/ark/models", json={"model_id": "x", "endpoint": "openai-chat"}
        ).status_code
        == 422
    )


# 供应商模型 Sheet：关联其他模型、手动填写模型信息
# ---------------------------------------------------------------------------

_HOUSE_IMAGE = {"model_id": "house-flux", "display_name": "house-flux", "endpoint": "openai-images", "is_enabled": True}
_NEW_IMAGE = {
    "maker": "acme",
    "name": "house-flux",
    "media_type": "image",
    "display_name": "House Flux",
    "facts": {"text_to_image": True, "resolutions": ["1K", "2K"]},
}


async def _house_model(db_factory: async_sessionmaker[AsyncSession], **extra: object) -> tuple[str, str]:
    db_id = await _custom_provider(db_factory, [{**_HOUSE_IMAGE, **extra}])
    return f"custom-{db_id}", f"/api/v1/providers/custom-{db_id}/models/house-flux"


async def test_manual_entry_is_created_with_the_sheet_save_and_bound(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    provider_id, url = await _house_model(db_factory)
    detail = models_client.get(url).json()
    assert (detail["entry"], detail["entry_binding"]) == (None, None)
    assert detail["model"]["flags"] == ["unpriced"]

    resp = models_client.patch(url, json={"multiplier": 2, "new_entry": _NEW_IMAGE})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["entry_binding"], body["multiplier"]) == ("acme/house-flux", 2.0)
    entry = body["entry"]
    assert (entry["source"], entry["official"], entry["display_name"]) == ("user_created", None, "House Flux")
    assert entry["facts"]["resolutions"] == ["1K", "2K"]
    # 不填价格：调用费用记为未定价
    assert body["model"]["flags"] == ["unpriced"]
    # 写入即生效，目录搜索也能找到它
    assert [
        h["name"] for h in models_client.get("/api/v1/catalog/entries", params={"q": "house-flux"}).json()["entries"]
    ] == ["house-flux"]
    assert _models(models_client, provider_id)["house-flux"]["display_name"] == "House Flux"


async def test_manual_entry_can_carry_a_price_table(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, url = await _house_model(db_factory)
    price = {
        "currency": "USD",
        "meters": [{"meter": "output_images", "dims": [], "rows": [{"when": {}, "price": 0.04}]}],
    }

    resp = models_client.patch(url, json={"new_entry": {**_NEW_IMAGE, "price": price}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["entry"]["price"] == price
    # 填了价格，调用费用不再是未定价
    assert body["model"]["flags"] == []
    assert (
        models_client.patch(
            url, json={"new_entry": {**_NEW_IMAGE, "name": "other", "price": {"currency": "XXX"}}}
        ).status_code
        == 422
    )


async def test_manual_entry_is_not_left_behind_when_the_save_is_rejected(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, url = await _house_model(db_factory)

    # 同一请求里别的设置被拒：条目也不写入
    assert models_client.patch(url, json={"multiplier": -1, "new_entry": _NEW_IMAGE}).status_code == 422
    # 媒体类型与模型的调用端点不一致
    wrong = {**_NEW_IMAGE, "media_type": "video", "facts": {"durations": [5]}}
    resp = models_client.patch(url, json={"new_entry": wrong})
    assert resp.status_code == 422
    assert "acme/house-flux" in resp.json()["detail"]

    assert models_client.get("/api/v1/catalog/entries", params={"q": "house-flux"}).json()["entries"] == []
    assert models_client.get(url).json()["entry_binding"] is None


async def test_manual_entry_rejects_a_name_the_catalog_already_has(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, url = await _house_model(db_factory)
    clash = {**_NEW_IMAGE, "maker": "my-relay", "name": "gemini-3.1-flash-image-preview"}
    assert catalog.find_entry(clash["name"], media_type="image") is not None

    resp = models_client.patch(url, json={"new_entry": clash})

    assert resp.status_code == 422
    assert resp.json()["detail"] == "模型目录中已有这个模型，请直接选择。"
    assert (
        models_client.patch(url, json={"new_entry": {**_NEW_IMAGE, "facts": {"text_to_image": "x"}}}).status_code == 422
    )


async def test_link_to_another_entry_then_unlink(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, url = await _house_model(db_factory)
    target = catalog.find_entry("gemini-3.1-flash-image-preview", media_type="image")
    assert target is not None

    resp = models_client.patch(url, json={"entry_binding": target.key.ref})

    assert resp.status_code == 200
    body = resp.json()
    assert body["entry_binding"] == target.key.ref
    assert (body["entry"]["name"], body["entry"]["source"]) == (target.name, "official")
    assert (body["model"]["display_name"], body["model"]["flags"]) == (target.display_name, [])
    assert body["model"]["price"] is not None

    # 解除绑定：改回按名字对应，名字对应不上就没有条目
    body = models_client.patch(url, json={"entry_binding": None}).json()
    assert (body["entry_binding"], body["entry"]) == (None, None)


async def test_link_is_validated_against_the_catalog_and_the_media_type(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    _, url = await _house_model(db_factory)
    text_entry = catalog.find_entry("gemini-3-flash-preview", media_type="text")
    assert text_entry is not None

    assert models_client.patch(url, json={"entry_binding": "acme/nope"}).status_code == 422
    # 条目存在，但媒体类型与模型的调用端点不一致
    assert models_client.patch(url, json={"entry_binding": text_entry.key.ref}).status_code == 422
    # 形状不对：说的是这个绑定，而不是调用端点
    malformed = models_client.patch(url, json={"entry_binding": "no-slash"})
    assert malformed.status_code == 422
    assert "no-slash" in malformed.json()["detail"]
    assert models_client.get(url).json()["entry_binding"] is None

    # 已关联的模型换到另一种媒体类型的调用端点：原绑定不再成立，要求同一次保存里重新关联或解除
    image_entry = catalog.find_entry("gemini-3.1-flash-image-preview", media_type="image")
    assert image_entry is not None
    assert models_client.patch(url, json={"entry_binding": image_entry.key.ref}).status_code == 200
    switched = models_client.patch(url, json={"endpoint": "openai-chat"})
    assert switched.status_code == 422
    assert image_entry.key.ref in switched.json()["detail"]
    assert models_client.get(url).json()["entry_binding"] == image_entry.key.ref
    relinked = models_client.patch(url, json={"endpoint": "openai-chat", "entry_binding": text_entry.key.ref})
    assert (relinked.status_code, relinked.json()["entry_binding"]) == (200, text_entry.key.ref)


def test_builtin_user_added_model_can_link_or_fill_in_but_official_cannot(models_client: TestClient) -> None:
    _add_user_model(models_client, "ark", "my-private-ep", "text")
    url = "/api/v1/providers/ark/models/my-private-ep"
    assert models_client.get(url).json()["entry_binding"] is None

    linked = models_client.patch(url, json={"entry_binding": "google/gemini-3-flash-preview"})
    assert linked.status_code == 200
    assert (linked.json()["entry_binding"], linked.json()["entry"]["name"]) == (
        "google/gemini-3-flash-preview",
        "gemini-3-flash-preview",
    )
    # 内置供应商的运行时索引读到同一个绑定
    runtime = catalog.get_model("ark", "my-private-ep", media_type="text")
    assert runtime is not None
    assert "vision" in runtime.capabilities

    manual = {"maker": "acme", "name": "private-chat", "media_type": "text", "display_name": "Private Chat"}
    body = models_client.patch(url, json={"new_entry": manual}).json()
    assert (body["entry_binding"], body["entry"]["source"]) == ("acme/private-chat", "user_created")

    # 官方收录的模型由官方维护，不能改绑定
    official = "/api/v1/providers/gemini-aistudio/models/gemini-3-flash-preview"
    assert models_client.patch(official, json={"entry_binding": "acme/private-chat"}).status_code == 422
    assert models_client.patch(official, json={"new_entry": manual}).status_code == 422


async def test_user_created_entry_switches_to_official_data_once_it_appears(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, url = await _house_model(db_factory)
    models_client.patch(url, json={"new_entry": _NEW_IMAGE})
    key = {"maker": "acme", "name": "house-flux", "media_type": "image"}
    # 官方数据还没有它：不能改用官方数据
    resp = models_client.post("/api/v1/catalog/entries/restore-official", json=key)
    assert resp.status_code == 422
    assert models_client.get(url).json()["entry"]["source"] == "user_created"

    # 日后官方数据收录了同一身份的条目
    snapshot = catalog.factory_snapshot()
    official = replace(
        next(e for e in snapshot.entries if e.media_type == "image"),
        maker="acme",
        name="house-flux",
        display_name="Official Flux",
        aliases=(),
    )
    install_catalog(monkeypatch, replace(snapshot, entries=(*snapshot.entries, official)))

    entry = models_client.get(url).json()["entry"]
    # 仍是用户新建、不跟随官方，同时给出官方的值
    assert (entry["source"], entry["display_name"], entry["official"]["display_name"]) == (
        "user_created",
        "House Flux",
        "Official Flux",
    )
    assert "modified" not in models_client.get(url).json()["model"]["flags"]

    assert models_client.post("/api/v1/catalog/entries/restore-official", json=key).status_code == 204

    entry = models_client.get(url).json()["entry"]
    assert (entry["source"], entry["display_name"], entry["official"]) == ("official", "Official Flux", None)
    assert [
        h["display_name"]
        for h in models_client.get("/api/v1/catalog/entries", params={"q": "house-flux"}).json()["entries"]
    ] == ["Official Flux"]


def test_create_entry_endpoint_and_new_entry_hints(models_client: TestClient) -> None:
    hints = models_client.get("/api/v1/catalog/new-entry-hints", params={"name": "kling-v2-pro", "media_type": "video"})
    assert hints.json() == {"existing": None, "durations": [5, 10]}
    hints = models_client.get(
        "/api/v1/catalog/new-entry-hints", params={"name": "Google/Gemini-3-Flash-Preview", "media_type": "text"}
    ).json()
    assert (hints["existing"]["name"], hints["durations"]) == ("gemini-3-flash-preview", None)

    created = models_client.post("/api/v1/catalog/entries", json=_NEW_IMAGE)

    assert created.status_code == 201
    assert (created.json()["name"], created.json()["display_name"]) == ("house-flux", "House Flux")
    assert models_client.post("/api/v1/catalog/entries", json=_NEW_IMAGE).status_code == 422
    entry = catalog.find_entry("house-flux", media_type="image")
    assert entry is not None
    assert catalog.entry_source(entry.key) == "user_created"


_DOT_SEGMENT_IDS = [".", "..", "a/../b", "a/./b"]
_PLAIN_IDS = ["a.b", "gpt-4.1", "x/y"]


@pytest.mark.parametrize("model_id", _DOT_SEGMENT_IDS)
async def test_model_id_with_dot_segment_is_rejected_on_every_entry(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession], model_id: str
) -> None:
    custom = await _create_relay(db_factory)
    for provider_id in ("ark", custom):
        added = models_client.post(f"/api/v1/providers/{provider_id}/models", json={"model_id": model_id})
        assert added.status_code == 422
        assert model_id in added.json()["detail"]
    _add_user_model(models_client, "ark", "keep-me", "text")
    renamed = models_client.patch("/api/v1/providers/ark/models/keep-me", json={"model_id": model_id})
    assert renamed.status_code == 422
    assert "keep-me" in _models(models_client, "ark")

    models_client.post(f"/api/v1/providers/{custom}/models", json={"model_id": "house-chat"})
    renamed = models_client.patch(f"/api/v1/providers/{custom}/models/house-chat", json={"model_id": model_id})
    assert renamed.status_code == 422
    assert "house-chat" in _models(models_client, custom)


@pytest.mark.parametrize("model_id", _PLAIN_IDS)
async def test_model_id_without_dot_segment_is_accepted(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession], model_id: str
) -> None:
    custom = await _create_relay(db_factory)
    for provider_id in ("ark", custom):
        resp = models_client.post(
            f"/api/v1/providers/{provider_id}/models", json={"model_id": model_id, "media_type": "text"}
        )
        assert resp.status_code == 201


async def test_discovery_drops_model_ids_with_dot_segment(
    models_client: TestClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    ids = ("a/../b", "a/./b", "..", "gpt-4.1", "x/y")
    provider_id = await _create_relay(db_factory)
    with capture_http() as http:
        http.get("https://relay.example.com/v1/models").respond(json=_model_list(*ids))
        custom = models_client.post(f"/api/v1/providers/{provider_id}/models/discover")
    async with db_factory() as session:
        await CredentialRepository(session).create(provider="dashscope", name="k", api_key="sk-test")
        await session.commit()
    with capture_http() as http:
        http.get("https://dashscope.aliyuncs.com/compatible-mode/v1/models").respond(json=_model_list(*ids))
        builtin = models_client.post("/api/v1/providers/dashscope/models/discover")

    for resp in (custom, builtin):
        assert resp.status_code == 200
        assert sorted(m["model_id"] for m in resp.json()["models"]) == ["gpt-4.1", "x/y"]


def test_model_reference_setting_keys_have_i18n_labels() -> None:
    """删除确认列出的全局引用按 `global_bucket_label_<key>` 动态取词，每个键都须有三语文案：缺文案会把
    原始 key 渲染给用户，而 zh/en/vi 三语一致性检查只比对彼此、看不见后端新增的键。"""
    i18n_dir = Path(__file__).resolve().parents[4] / "frontend" / "src" / "i18n"
    for locale in ("zh", "en", "vi"):
        content = (i18n_dir / locale / "dashboard.ts").read_text(encoding="utf-8")
        for key in GLOBAL_MODEL_SETTING_KEYS:
            assert f"'global_bucket_label_{key}'" in content, f"{locale} 缺 global_bucket_label_{key} 文案"
