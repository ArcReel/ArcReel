"""供应商模型接口：列出、启用与停用、按 ID 添加与移除、Sheet 的供应商设置与模型信息，绑内存库。"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_catalog_core import EntryKey
from lib.catalog import query as catalog
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
    assert (model["media_type"], model["display_name"], model["flags"]) == ("text", "Gemini 3 Flash", [])
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
        "Gemini 3 Flash",
        True,
        "openai-chat",
        [],
    )
    house = models["house-model"]
    assert (house["display_name"], house["flags"], house["price"]) == ("自家模型", ["unpriced"], None)

    resp = models_client.put(
        f"/api/v1/providers/{provider_id}/models/enabled",
        json={"model_id": "anthropic/gemini-3-flash-preview", "enabled": False},
    )
    assert (resp.json()["enabled"], resp.json()["is_default"]) == (False, False)
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


async def test_custom_model_rename_and_remove_clear_global_references(
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
        assert await ConfigService(session).get_setting("default_text_backend", "") == ""

    assert models_client.delete(f"/api/v1/providers/{provider_id}/models/spare").status_code == 204
    assert set(_models(models_client, provider_id)) == {"house-v2"}


# ---------------------------------------------------------------------------
# 供应商模型 Sheet：模型信息（用户修改层）
# ---------------------------------------------------------------------------

_FLASH = {"maker": "google", "name": "gemini-3-flash-preview", "media_type": "text"}
_FLASH_URL = "/api/v1/providers/gemini-aistudio/models/gemini-3-flash-preview"


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

    entry = models_client.get(_FLASH_URL).json()["entry"]
    assert (entry["source"], entry["official"], entry["display_name"]) == ("official", None, "Gemini 3 Flash")
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
        "display_name": "Gemini 3 Flash",
        "facts": {"image_input": True, "structured_output": True, "max_output_tokens": 65536},
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
        "Gemini 3 Flash",
        65536,
    )
    vertex = _models(models_client, "gemini-vertex")["gemini-3-flash-preview"]
    assert (vertex["display_name"], "modified" in vertex["flags"]) == ("Gemini 3 Flash", False)
    runtime = catalog.get_model("gemini-vertex", "gemini-3-flash-preview")
    assert runtime is not None
    assert runtime.max_output_tokens == 65536


def test_entry_change_is_saved_atomically_with_provider_settings(models_client: TestClient) -> None:
    # 官方收录的模型不接受倍率：整个请求被拒，模型信息也不写入
    resp = models_client.patch(_FLASH_URL, json={"multiplier": 2, "entry": {**_FLASH, "display_name": "My Flash"}})

    assert resp.status_code == 422
    entry = models_client.get(_FLASH_URL).json()["entry"]
    assert (entry["source"], entry["display_name"]) == ("official", "Gemini 3 Flash")


def test_entry_edit_is_validated_and_must_target_the_mapped_entry(models_client: TestClient) -> None:
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "facts": {"max_output_tokens": 0}}})
    assert resp.status_code == 422

    other = {**_FLASH, "name": "gemini-3.1-pro-preview"}
    resp = models_client.patch(_FLASH_URL, json={"entry": {**other, "display_name": "x"}})
    assert resp.status_code == 409

    # 与当前条目相同的修改不写副本，条目照旧跟随官方
    resp = models_client.patch(_FLASH_URL, json={"entry": {**_FLASH, "display_name": "Gemini 3 Flash"}})
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
    assert (
        models_client.post(
            "/api/v1/providers/ark/models", json={"model_id": "x", "endpoint": "openai-chat"}
        ).status_code
        == 422
    )
