"""能力覆盖的 API 面与展示层同源。

覆盖三件事：配置 API 读写覆盖（含白名单 4xx 与整体替换语义）、resolver 返回生效能力、
展示层与执行层出自同一个合成函数。API 侧沿用 test_custom_providers_api.py 的内存 SQLite +
TestClient 范式，同源侧沿用 test_custom_provider_loader.py 的真 repo 装载范式。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.custom_provider import make_provider_id
from lib.custom_provider.capabilities import synthesize_video_capabilities, system_video_capabilities
from lib.custom_provider.loader import load_custom_backend
from lib.db import get_async_session
from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_providers

# 系统判定 last_frame=False、reference_images=True/max=1 —— 覆盖前后差异可断言
VIDEO_ENDPOINT = "openai-video"
VIDEO_MODEL = "sora-2"

# openai-video 的 delegate 不序列化 end_image（见 _check_capability_overrides 的
# end_image_capable 门槛），last_frame 覆盖为 True 的用例须换一个真正支持尾帧的 endpoint。
# ark-seedance 非 seedance-2 系列模型系统判定同样是 last_frame=False，可类比断言覆盖前后差异。
LAST_FRAME_ENDPOINT = "ark-seedance"
LAST_FRAME_MODEL = "seedance-1-pro"


@pytest.fixture()
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture()
def app(session_factory) -> FastAPI:
    _app = FastAPI()

    async def _override_session():
        async with session_factory() as session:
            yield session

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    _app.include_router(custom_providers.router, prefix="/api/v1")
    register_error_handlers(_app)
    return _app


@pytest.fixture()
async def session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture()
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def _create_provider(client: TestClient, models: list[dict]) -> int:
    resp = client.post(
        "/api/v1/custom-providers",
        json={
            "display_name": "Relay",
            "discovery_format": "openai",
            "base_url": "https://relay.test/v1",
            "api_key": "sk-relay",
            "models": models,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _video_model(**overrides) -> dict:
    model = {
        "model_id": VIDEO_MODEL,
        "display_name": "Sora 2",
        "endpoint": VIDEO_ENDPOINT,
        "is_default": True,
        "is_enabled": True,
    }
    model.update(overrides)
    return model


class TestModelListExposesCapabilities:
    """models 列表同时给出系统判定与用户覆盖，设置页平凡合并即可展示。"""

    @pytest.mark.integration
    def test_video_model_reports_system_capabilities(self, client: TestClient):
        pid = _create_provider(client, [_video_model()])

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] == {
            "first_frame": True,
            "last_frame": False,
            "reference_images": True,
            "max_reference_images": 1,
        }
        assert models[0]["capability_overrides"] is None

    @pytest.mark.integration
    def test_system_capabilities_matches_synthesis_source(self, client: TestClient):
        """判定值不是 API 里另写一份，而是合成函数的系统判定分支。"""
        pid = _create_provider(client, [_video_model()])

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        expected = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert models[0]["system_capabilities"] == {
            "first_frame": expected.first_frame,
            "last_frame": expected.last_frame,
            "reference_images": expected.reference_images,
            "max_reference_images": expected.max_reference_images,
        }

    @pytest.mark.integration
    def test_non_video_model_has_no_system_capabilities(self, client: TestClient):
        pid = _create_provider(
            client,
            [{"model_id": "dall-e-3", "display_name": "D3", "endpoint": "openai-images", "is_enabled": True}],
        )

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["system_capabilities"] is None

    @pytest.mark.integration
    def test_overrides_round_trip_through_create(self, client: TestClient):
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
                )
            ],
        )

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}
        # 判定值不受覆盖影响：设置页要能同时显示"判定 False / 生效 True"
        assert models[0]["system_capabilities"]["last_frame"] is False

    @pytest.mark.integration
    async def test_stale_incompatible_override_filtered_from_response(self, client: TestClient, session_factory):
        """存量行 / 非 API 写入可能留下已不兼容的覆盖（如 openai-video 上的 last_frame=True，
        endpoint 不 end_image_capable）：写入侧白名单挡不住这条已落库的数据，回显前须过滤，
        不能让界面呈现"覆盖已生效"而执行层其实静默忽略——原样回显还会让下次普通保存被
        写入校验拒为 422，堵住与该覆盖无关的编辑。"""
        async with session_factory() as session:
            repo = CustomProviderRepository(session)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": VIDEO_MODEL,
                        "display_name": "Sora 2",
                        "endpoint": VIDEO_ENDPOINT,
                        "is_enabled": True,
                        "is_default": True,
                        "capability_overrides": {"last_frame": True},
                    }
                ],
            )
            await session.commit()
            pid = provider.id

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] is None

    @pytest.mark.integration
    async def test_corrupted_non_dict_override_does_not_500_response(self, client: TestClient, session_factory):
        """存量行 / 手工 SQL 可能让 JSON 列存了非字典值（字符串、列表等）：执行层的
        synthesize_video_capabilities 按容错设计忽略它，响应边界须同样容错，不能把原值
        直接塞进只接受 dict | None 的 ModelResponse 触发 Pydantic 校验错误，让整个列表/详情
        请求 500——用户也就无法进入设置页清理这条坏值。"""
        async with session_factory() as session:
            repo = CustomProviderRepository(session)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": VIDEO_MODEL,
                        "display_name": "Sora 2",
                        "endpoint": VIDEO_ENDPOINT,
                        "is_enabled": True,
                        "is_default": True,
                        "capability_overrides": "last_frame",
                    }
                ],
            )
            await session.commit()
            pid = provider.id

        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None

    @pytest.mark.integration
    async def test_retired_endpoint_override_does_not_500_response(self, client: TestClient, session_factory):
        """endpoint 已从注册表下线（升级移除）时，get_endpoint_spec 会抛 ValueError；响应边界
        过滤 last_frame 覆盖时须容错这条查表失败，不能让存量脏配置把列表/详情请求也炸成 500。"""
        async with session_factory() as session:
            repo = CustomProviderRepository(session)
            provider = await repo.create_provider(
                display_name="Relay",
                discovery_format="openai",
                base_url="https://relay.test/v1",
                api_key="sk-relay",
                models=[
                    {
                        "model_id": "retired-model",
                        "display_name": "Retired",
                        "endpoint": "no-such-retired-endpoint",
                        "is_enabled": True,
                        "is_default": True,
                        "capability_overrides": {"last_frame": True},
                    }
                ],
            )
            await session.commit()
            pid = provider.id

        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        assert resp.json()["models"][0]["capability_overrides"] is None


class TestPatchCapabilityOverrides:
    """PATCH 携带完整覆盖字典，整体替换存量。"""

    @staticmethod
    def _patch(client: TestClient, pid: int, payload: dict, model_id: str = VIDEO_MODEL):
        return client.patch(
            f"/api/v1/custom-providers/{pid}/models/{model_id}/capability-overrides",
            json=payload,
        )

    @pytest.mark.integration
    def test_write_override_and_read_back(self, client: TestClient):
        pid = _create_provider(client, [_video_model(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL)])

        resp = self._patch(client, pid, {"capability_overrides": {"last_frame": True}}, model_id=LAST_FRAME_MODEL)
        assert resp.status_code == 200, resp.text
        assert resp.json()["capability_overrides"] == {"last_frame": True}

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}

    @pytest.mark.integration
    def test_empty_dict_clears_existing_overrides(self, client: TestClient):
        """整体替换而非逐键 merge：空字典即回到全部跟随系统判定。"""
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = self._patch(client, pid, {"capability_overrides": {}}, model_id=LAST_FRAME_MODEL)
        assert resp.status_code == 200
        assert resp.json()["capability_overrides"] is None

    @pytest.mark.integration
    def test_null_clears_existing_overrides(self, client: TestClient):
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = self._patch(client, pid, {"capability_overrides": None}, model_id=LAST_FRAME_MODEL)
        assert resp.status_code == 200
        assert resp.json()["capability_overrides"] is None

    @pytest.mark.integration
    def test_unknown_key_rejected(self, client: TestClient):
        pid = _create_provider(client, [_video_model()])

        resp = self._patch(client, pid, {"capability_overrides": {"no_such_capability": True}})
        assert resp.status_code == 422
        assert "no_such_capability" in resp.json()["detail"]

    @pytest.mark.integration
    def test_known_but_unallowlisted_key_rejected(self, client: TestClient):
        """first_frame 是合法 VideoCapabilities 字段，但首批未开放覆盖。"""
        pid = _create_provider(client, [_video_model()])

        resp = self._patch(client, pid, {"capability_overrides": {"first_frame": False}})
        assert resp.status_code == 422
        assert "first_frame" in resp.json()["detail"]

    @pytest.mark.integration
    @pytest.mark.parametrize("bad_value", ["true", 1, 0, None, [], {}])
    def test_wrong_value_type_rejected(self, client: TestClient, bad_value):
        pid = _create_provider(client, [_video_model()])

        resp = self._patch(client, pid, {"capability_overrides": {"last_frame": bad_value}})
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_last_frame_rejected_on_endpoint_without_end_image_support(self, client: TestClient):
        """openai-video 的 delegate 不下传 end_image：开启覆盖只会让 UI 宣称支持、执行层仍静默丢帧。"""
        pid = _create_provider(client, [_video_model()])

        resp = self._patch(client, pid, {"capability_overrides": {"last_frame": True}})
        assert resp.status_code == 422
        assert "last_frame" in resp.json()["detail"]

    @pytest.mark.integration
    def test_non_video_endpoint_rejected(self, client: TestClient):
        pid = _create_provider(
            client,
            [{"model_id": "dall-e-3", "display_name": "D3", "endpoint": "openai-images", "is_enabled": True}],
        )

        resp = self._patch(client, pid, {"capability_overrides": {"last_frame": True}}, model_id="dall-e-3")
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_missing_model_returns_404(self, client: TestClient):
        pid = _create_provider(client, [_video_model()])

        resp = self._patch(client, pid, {"capability_overrides": {"last_frame": True}}, model_id="ghost")
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_rejected_write_leaves_stored_overrides_intact(self, client: TestClient):
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        assert (
            self._patch(
                client, pid, {"capability_overrides": {"first_frame": False}}, model_id=LAST_FRAME_MODEL
            ).status_code
            == 422
        )

        models = client.get(f"/api/v1/custom-providers/{pid}").json()["models"]
        assert models[0]["capability_overrides"] == {"last_frame": True}


class TestReplaceModelsOverrideSemantics:
    """保存模型列表是整体替换：覆盖必须随列表回传，否则被清空。"""

    @pytest.mark.integration
    def test_overrides_survive_when_resubmitted(self, client: TestClient):
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    _video_model(
                        capability_overrides={"last_frame": True},
                        endpoint=LAST_FRAME_ENDPOINT,
                        model_id=LAST_FRAME_MODEL,
                    )
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()[0]["capability_overrides"] == {"last_frame": True}

    @pytest.mark.integration
    def test_overrides_dropped_when_omitted(self, client: TestClient):
        """整体替换语义的直接后果，前端保存模型列表时必须回传覆盖字段。"""
        pid = _create_provider(
            client,
            [
                _video_model(
                    capability_overrides={"last_frame": True},
                    endpoint=LAST_FRAME_ENDPOINT,
                    model_id=LAST_FRAME_MODEL,
                )
            ],
        )

        resp = client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={"models": [_video_model(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL)]},
        )
        assert resp.status_code == 200
        assert resp.json()[0]["capability_overrides"] is None

    @pytest.mark.integration
    def test_invalid_override_rejected_on_replace(self, client: TestClient):
        pid = _create_provider(client, [_video_model()])

        resp = client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={"models": [_video_model(capability_overrides={"first_frame": False})]},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_invalid_override_rejected_on_create(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Relay",
                "discovery_format": "openai",
                "base_url": "https://relay.test/v1",
                "api_key": "sk-relay",
                "models": [_video_model(capability_overrides={"nope": True})],
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_invalid_override_rejected_on_full_update(self, client: TestClient):
        pid = _create_provider(client, [_video_model()])

        resp = client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "Relay",
                "base_url": "https://relay.test/v1",
                "models": [_video_model(capability_overrides={"last_frame": "yes"})],
            },
        )
        assert resp.status_code == 422


class TestResolverReturnsEffectiveCapabilities:
    """/video-capabilities 走的 resolver 必须回生效能力，且与执行层同源。"""

    @staticmethod
    async def _seed(
        session: AsyncSession, *, overrides: object | None, endpoint: str = VIDEO_ENDPOINT, model_id: str = VIDEO_MODEL
    ) -> str:
        repo = CustomProviderRepository(session)
        provider = await repo.create_provider(
            display_name="Relay",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key="sk-relay",
            models=[
                {
                    "model_id": model_id,
                    "display_name": "Sora 2",
                    "endpoint": endpoint,
                    "is_enabled": True,
                    "is_default": True,
                    "supported_durations": "[5, 10]",
                    "capability_overrides": overrides,
                }
            ],
        )
        await session.commit()
        return make_provider_id(provider.id)

    @staticmethod
    async def _resolve(session: AsyncSession, provider_id: str, model_id: str = VIDEO_MODEL) -> dict:
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService

        factory = async_sessionmaker(bind=session.get_bind(), class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
        resolver = ConfigResolver(factory, _bound_session=session)
        return await resolver._resolve_video_caps_for_model(
            ConfigService(session), session, provider_id, model_id, None
        )

    @pytest.mark.integration
    async def test_custom_model_without_overrides_follows_system(self, session: AsyncSession):
        pid = await self._seed(session, overrides=None)

        caps = await self._resolve(session, pid)
        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert caps["last_frame"] is system.last_frame
        assert caps["first_frame"] is system.first_frame
        assert caps["max_reference_images"] == system.max_reference_images

    @pytest.mark.integration
    async def test_override_changes_resolver_output(self, session: AsyncSession):
        """AC：对自定义模型写入覆盖后，该接口返回值随之变化。"""
        pid = await self._seed(
            session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(session, pid, model_id=LAST_FRAME_MODEL)
        assert system_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert caps["last_frame"] is True

    @pytest.mark.integration
    async def test_override_ignored_when_endpoint_lacks_end_image_support(self, session: AsyncSession):
        """openai-video 的 delegate 不下传 end_image：即便存量行/非 API 写入把 last_frame 写成 True，
        resolver 也须回退系统判定，而不是把「合成层宣称支持、执行层静默丢帧」的错误状态当作生效。"""
        pid = await self._seed(session, overrides={"last_frame": True})

        caps = await self._resolve(session, pid)
        assert caps["last_frame"] is False

    @pytest.mark.integration
    @patch("lib.custom_provider.endpoints.ArkVideoBackend")
    async def test_resolver_matches_execution_layer(self, _mock_cls, session: AsyncSession):
        """展示层与执行层出自同一合成函数：逐字段比对 resolver 与装载出的 backend。"""
        pid = await self._seed(
            session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
        )

        caps = await self._resolve(session, pid, model_id=LAST_FRAME_MODEL)
        session.expunge_all()
        backend = await load_custom_backend(
            session=session, provider_id=pid, model_id=LAST_FRAME_MODEL, media_type="video"
        )

        executed = backend.video_capabilities
        assert caps["first_frame"] is executed.first_frame
        assert caps["last_frame"] is executed.last_frame
        assert caps["max_reference_images"] == executed.max_reference_images
        # 同源的判据：两侧都等于合成函数在同一输入下的返回值
        expected = synthesize_video_capabilities(
            endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL, overrides={"last_frame": True}
        )
        assert executed == expected

    @pytest.mark.integration
    async def test_builtin_boolean_caps_come_from_backend(self, session: AsyncSession):
        """内置分支的布尔位来自 backend 纯函数，注册表不存第二份。"""
        from lib.backend_assembly.specs import get_provider_spec
        from lib.config.resolver import ConfigResolver
        from lib.config.service import ConfigService
        from lib.video_backends.registry import video_capabilities_for_model

        factory = async_sessionmaker(bind=session.get_bind(), class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
        resolver = ConfigResolver(factory, _bound_session=session)
        caps = await resolver._resolve_video_caps_for_model(ConfigService(session), session, "openai", "sora-2", None)

        spec = get_provider_spec("openai", "video")
        backend_caps = video_capabilities_for_model(spec.registry_backend, "sora-2")
        assert caps["source"] == "registry"
        assert caps["first_frame"] is backend_caps.first_frame
        assert caps["last_frame"] is backend_caps.last_frame


class TestBuiltinBackendsDeclareCapabilityFunction:
    """每个能承载视频模型的内置 provider 都要能被纯函数问出布尔能力位。"""

    @pytest.mark.unit
    def test_every_builtin_video_provider_resolvable(self):
        from lib.backend_assembly.specs import get_provider_spec
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.video_backends.base import VideoCapabilities
        from lib.video_backends.registry import video_capabilities_for_model

        for provider_id, meta in PROVIDER_REGISTRY.items():
            video_models = [mid for mid, mi in meta.models.items() if mi.media_type == "video"]
            if not video_models:
                continue
            spec = get_provider_spec(provider_id, "video")
            for model_id in video_models:
                caps = video_capabilities_for_model(spec.registry_backend, model_id)
                assert isinstance(caps, VideoCapabilities), f"{provider_id}/{model_id}"

    @pytest.mark.unit
    def test_unknown_backend_name_fails_loud(self):
        from lib.video_backends.registry import video_capabilities_for_model

        with pytest.raises(ValueError, match="Unknown video backend"):
            video_capabilities_for_model("no-such-backend", "m")


class TestVideoCapabilitiesEndpoint:
    """GET /projects/{name}/video-capabilities 的响应形状与覆盖联动。

    其余同源测试打在 resolver 私有方法上，这里补住 HTTP 这一层：新增的两个布尔位真的
    出现在接口响应里，且写入覆盖后接口返回值随之变化（不只是 resolver 内部变了）。
    """

    @staticmethod
    def _client(monkeypatch, session_factory, provider_id: str, model_id: str = VIDEO_MODEL) -> TestClient:
        from fastapi import FastAPI

        from lib.config import resolver as resolver_mod
        from server.routers import projects as projects_mod

        class _FakePM:
            def load_project(self, name: str) -> dict:
                return {"name": name, "video_backend": f"{provider_id}/{model_id}"}

        monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)
        monkeypatch.setattr(resolver_mod, "get_project_manager", lambda: _FakePM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="t", sub="t", role="admin")
        app.include_router(projects_mod.router, prefix="/api/v1")
        register_error_handlers(app)
        return TestClient(app)

    @pytest.mark.integration
    async def test_endpoint_returns_effective_boolean_caps(self, session_factory, monkeypatch):
        async with session_factory() as session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(session, overrides=None)

        with self._client(monkeypatch, session_factory, pid) as client:
            body = client.get("/api/v1/projects/demo/video-capabilities").json()

        system = system_video_capabilities(endpoint=VIDEO_ENDPOINT, model_id=VIDEO_MODEL)
        assert body["first_frame"] is system.first_frame
        assert body["last_frame"] is system.last_frame is False

    @pytest.mark.integration
    async def test_endpoint_follows_written_override(self, session_factory, monkeypatch):
        """AC：对自定义模型写入覆盖后，该接口返回值随之变化。"""
        async with session_factory() as session:
            pid = await TestResolverReturnsEffectiveCapabilities._seed(
                session, overrides={"last_frame": True}, endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL
            )

        with self._client(monkeypatch, session_factory, pid, model_id=LAST_FRAME_MODEL) as client:
            body = client.get("/api/v1/projects/demo/video-capabilities").json()

        assert system_video_capabilities(endpoint=LAST_FRAME_ENDPOINT, model_id=LAST_FRAME_MODEL).last_frame is False
        assert body["last_frame"] is True
