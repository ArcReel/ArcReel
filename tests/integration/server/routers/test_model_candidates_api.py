"""任务类型桶候选模型过滤 API：GET /system/config/model-candidates。

只断言外部行为——给定 ready 供应商与自定义供应商模型，断言各桶候选列表的成员关系。
桶归属的真相源判定（registry 图片能力声明 / backend 视频能力 / endpoint 系统判定 ⊕ 覆盖）
在 lib.capability_buckets 层单独覆盖。

dashscope 被选作内置侧的样本供应商：它同时提供 i2v-only、t2v-only、r2v 三类视频模型与
t2i+i2i、i2i-only 两类图片模型，一个 ready 供应商就能把四个桶的过滤差异全部区分出来。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.capability_buckets import builtin_model_buckets, custom_model_buckets
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService, ProviderStatus
from lib.db import get_async_session
from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.routers import system_config as system_config_router
from tests.auth_deps import AUTH_DEPENDENCIES

CANDIDATES_URL = "/api/v1/system/config/model-candidates"


def _custom_option(body: dict, model_id: str) -> str:
    """从响应里取自定义供应商那一项的 "provider/model"。

    provider_names 同时含内置 ready 供应商，故按 custom- 前缀挑，不能取第一个键。
    """
    return next(pid for pid in body["provider_names"] if pid.startswith("custom-")) + f"/{model_id}"


# dashscope 视频模型（视频能力值取自 backend 声明，见模块 docstring）
DS_I2V_ONLY = "dashscope/wan2.7-i2v"  # i2v ✓ / r2v ✗
DS_T2V_ONLY = "dashscope/wan2.7-t2v"  # i2v ✗ / r2v ✗
DS_R2V = "dashscope/wan2.7-r2v"  # i2v ✓ / r2v ✓
# dashscope 图片模型
DS_IMAGE_BOTH = "dashscope/qwen-image-2.0"
DS_IMAGE_I2I_ONLY = "dashscope/qwen-image-edit-plus"
# 无桶维度的媒体类型样本（audio 不设桶）
DS_AUDIO = "dashscope/qwen3-tts-flash"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_svc(ready_providers: list[str]) -> ConfigService:
    ready = set(ready_providers)
    svc = MagicMock(spec=ConfigService)

    async def _get_all_providers_status() -> list[ProviderStatus]:
        return [
            ProviderStatus(
                name=name,
                display_name=meta.display_name,
                description=meta.description,
                status="ready" if name in ready else "unconfigured",
                media_types=list(meta.media_types),
                capabilities=list(meta.capabilities),
                required_keys=list(meta.required_keys),
                configured_keys=list(meta.required_keys) if name in ready else [],
                missing_keys=[] if name in ready else list(meta.required_keys),
            )
            for name, meta in PROVIDER_REGISTRY.items()
        ]

    svc.get_all_providers_status = AsyncMock(side_effect=_get_all_providers_status)
    return svc


@pytest.fixture()
def session_factory_and_engine(db_engine, db_factory):
    return db_factory, db_engine


def _make_app(mock_svc: ConfigService, engine, factory) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(lifespan=_lifespan)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.dependency_overrides[get_config_service] = lambda: mock_svc

    async def _override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.include_router(system_config_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


@pytest.fixture()
def make_client(session_factory_and_engine):
    factory, engine = session_factory_and_engine

    def _make(ready_providers: list[str] | None = None) -> TestClient:
        return TestClient(_make_app(_make_mock_svc(ready_providers or []), engine, factory))

    return _make


async def _seed_custom_models(factory, models: list[dict]) -> None:
    async with factory() as session:
        repo = CustomProviderRepository(session)
        await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=models,
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 内置模型的桶过滤
# ---------------------------------------------------------------------------


class TestBuiltinBucketFiltering:
    def test_r2v_only_lists_models_with_reference_image_slots(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        r2v = body["video"]["buckets"]["r2v"]
        assert DS_R2V in r2v
        assert DS_I2V_ONLY not in r2v
        assert DS_T2V_ONLY not in r2v

    def test_i2v_excludes_text_only_video_models(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        i2v = body["video"]["buckets"]["i2v"]
        assert DS_I2V_ONLY in i2v
        assert DS_R2V in i2v
        assert DS_T2V_ONLY not in i2v

    def test_image_buckets_split_by_declared_capabilities(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_IMAGE_BOTH in body["image"]["buckets"]["t2i"]
        assert DS_IMAGE_BOTH in body["image"]["buckets"]["i2i"]
        assert DS_IMAGE_I2I_ONLY in body["image"]["buckets"]["i2i"]
        assert DS_IMAGE_I2I_ONLY not in body["image"]["buckets"]["t2i"]

    def test_default_tier_is_unfiltered(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        # 默认层不承诺能力：无 i2v、无 r2v 的模型照样在列
        assert DS_T2V_ONLY in body["video"]["default"]
        assert DS_IMAGE_I2I_ONLY in body["image"]["default"]

    def test_unconfigured_providers_absent(self, make_client):
        with make_client([]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert body["video"]["default"] == []
        assert body["image"]["default"] == []
        assert body["video"]["buckets"]["r2v"] == []

    def test_media_types_do_not_leak_into_each_other(self, make_client):
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_R2V not in body["image"]["default"]
        assert DS_IMAGE_BOTH not in body["video"]["default"]

    def test_hidden_models_absent_from_every_list(self, make_client):
        meta = PROVIDER_REGISTRY["dashscope"]
        hidden_id = DS_R2V.split("/", 1)[1]
        hidden_models = {**meta.models, hidden_id: replace(meta.models[hidden_id], hidden=True)}
        with patch.dict(meta.models, hidden_models, clear=True), make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_R2V not in body["video"]["default"]
        assert DS_R2V not in body["video"]["buckets"]["r2v"]
        assert DS_R2V not in body["video"]["buckets"]["i2v"]


# ---------------------------------------------------------------------------
# 自定义供应商模型的桶过滤
# ---------------------------------------------------------------------------


class TestCustomProviderBucketFiltering:
    async def test_custom_video_model_enters_buckets_by_endpoint_judgement(
        self, make_client, session_factory_and_engine
    ):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            # openai-video 系统判定 max_reference_images=1、first_frame=True
            await _seed_custom_models(
                factory,
                [{"model_id": "sora-2", "display_name": "Sora 2", "endpoint": "openai-video", "is_enabled": True}],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "sora-2")
        assert option in body["video"]["buckets"]["r2v"]
        assert option in body["video"]["buckets"]["i2v"]
        assert option in body["video"]["default"]

    async def test_capability_override_removes_model_from_r2v(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "sora-2",
                        "display_name": "Sora 2",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "capability_overrides": {"max_reference_images": 0},
                    }
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "sora-2")
        assert option not in body["video"]["buckets"]["r2v"]
        # 覆盖只动了参考图维度，i2v 与默认层不受影响
        assert option in body["video"]["buckets"]["i2v"]
        assert option in body["video"]["default"]

    async def test_custom_image_model_filtered_by_endpoint_capabilities(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            # openai-images-edits 只声明 image_to_image
            await _seed_custom_models(
                factory,
                [
                    {
                        "model_id": "gpt-image-1",
                        "display_name": "GPT Image",
                        "endpoint": "openai-images-edits",
                        "is_enabled": True,
                    }
                ],
            )
            body = client.get(CANDIDATES_URL).json()
        option = _custom_option(body, "gpt-image-1")
        assert option in body["image"]["buckets"]["i2i"]
        assert option not in body["image"]["buckets"]["t2i"]
        assert option in body["image"]["default"]

    async def test_disabled_custom_models_absent(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [{"model_id": "sora-2", "display_name": "Sora 2", "endpoint": "openai-video", "is_enabled": False}],
            )
            body = client.get(CANDIDATES_URL).json()
        assert body["video"]["default"] == []


# ---------------------------------------------------------------------------
# 目录译名（与 /providers 同一条 translate_or 路径）
# ---------------------------------------------------------------------------


class TestCatalogDisplayNames:
    def test_builtin_provider_and_model_names_follow_accept_language(self, make_client):
        with make_client(["ark"]) as client:
            zh = client.get(CANDIDATES_URL, headers={"Accept-Language": "zh"}).json()
            en = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()
        assert zh["provider_names"]["ark"] == "火山方舟"
        assert en["provider_names"]["ark"] == "Volcengine Ark"
        assert zh["model_names"]["ark/doubao-seed-2-0-pro-260215"] == "豆包 Seed 2.0 Pro"
        assert en["model_names"]["ark/doubao-seed-2-0-pro-260215"] == "Doubao Seed 2.0 Pro"

    def test_model_names_cover_media_types_without_buckets(self, make_client):
        """model_names 不按 image/video 收窄——文本 / 音频下拉共用同一份译名表。"""
        with make_client(["dashscope"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert DS_AUDIO in body["model_names"]

    def test_unregistered_model_falls_back_to_registry_name(self, make_client):
        """无译名条目的模型返回 registry 里的原名，而不是裸键。"""
        with make_client(["openai"]) as client:
            names = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()["model_names"]
        assert names["openai/sora-2"] == PROVIDER_REGISTRY["openai"].models["sora-2"].display_name

    async def test_custom_model_name_is_the_user_authored_one(self, make_client, session_factory_and_engine):
        factory, _engine = session_factory_and_engine
        with make_client([]) as client:
            await _seed_custom_models(
                factory,
                [{"model_id": "sora-2", "display_name": "我的 Sora", "endpoint": "openai-video", "is_enabled": True}],
            )
            body = client.get(CANDIDATES_URL, headers={"Accept-Language": "en"}).json()
        assert body["model_names"][_custom_option(body, "sora-2")] == "我的 Sora"

    def test_unconfigured_provider_absent_from_names(self, make_client):
        with make_client(["ark"]) as client:
            body = client.get(CANDIDATES_URL).json()
        assert "openai" not in body["provider_names"]
        assert not any(k.startswith("openai/") for k in body["model_names"])


# ---------------------------------------------------------------------------
# 桶归属判定（真相源直读，不经 HTTP）
# ---------------------------------------------------------------------------


class TestBucketJudgement:
    def test_builtin_audio_model_has_no_buckets(self):
        provider_id, model_id = DS_AUDIO.split("/", 1)
        model_info = PROVIDER_REGISTRY[provider_id].models[model_id]
        assert builtin_model_buckets(provider_id, model_id, model_info) == frozenset()

    def test_builtin_video_r2v_reads_backend_declaration(self):
        """r2v 归属只看 backend 的 max_reference_images，registry ModelInfo 不参与。

        viduq3-pro 不在 Vidu 的 /reference2video 端点白名单内，backend 据此声明
        max_reference_images=0；同 provider 的 viduq3 在白名单内、声明 7。两者的 registry
        条目都不带能力位，桶归属仍按 backend 分开。
        """
        meta = PROVIDER_REGISTRY["vidu"]
        assert "r2v" not in builtin_model_buckets("vidu", "viduq3-pro", meta.models["viduq3-pro"])
        assert "r2v" in builtin_model_buckets("vidu", "viduq3", meta.models["viduq3"])

    @pytest.mark.parametrize(
        ("provider_id", "model_id"),
        [
            ("vidu", "viduq3"),  # 只在 /reference2video 白名单内，提交首帧会在构造请求时报错
            ("dashscope", "happyhorse-1.0-r2v"),
            ("minimax", "S2V-01"),  # 单脸 subject_reference 驱动，不接受 first_frame_image
        ],
    )
    def test_builtin_video_i2v_reads_backend_declaration(self, provider_id: str, model_id: str):
        """i2v 归属只看 backend 的 first_frame，registry ModelInfo 不参与。

        这三个 model 的 backend first_frame=False 与请求构造同源，执行期不接首帧——放进 i2v 桶
        就等于让用户配出必败的组合。
        """
        model_info = PROVIDER_REGISTRY[provider_id].models[model_id]
        assert "i2v" not in builtin_model_buckets(provider_id, model_id, model_info)

    def test_unknown_endpoint_yields_no_buckets(self):
        assert custom_model_buckets(endpoint="nope-not-real", model_id="m") == frozenset()

    def test_text_endpoint_yields_no_buckets(self):
        assert custom_model_buckets(endpoint="openai-chat", model_id="gpt-4o") == frozenset()
