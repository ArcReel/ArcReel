"""providers 路由的无项目 video-capabilities 查询。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.i18n.zh import errors as zh_errors
from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import projects, providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

#: registry 里声明了「1080p 只剩 8 秒」的型号，用来观察真实 resolver 算出的收窄结果。
VEO = "gemini-aistudio/veo-3.1-generate-preview"


def _app() -> FastAPI:
    app = FastAPI()
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


def _client(monkeypatch, resolver_instance) -> TestClient:
    """错误映射用：让 resolver 抛出指定异常，断言路由把它翻成哪个状态码与文案。"""
    resolver_instance.resolve_video_backend = AsyncMock()
    monkeypatch.setattr(providers, "ConfigResolver", lambda _factory: resolver_instance)
    return TestClient(_app())


@pytest.fixture
def real_resolver_client(db_engine, monkeypatch) -> TestClient:
    """真实 ConfigResolver + 内存 DB：成功路径不替换仓库内协作者。"""
    monkeypatch.setattr(providers, "async_session_factory", async_sessionmaker(db_engine, expire_on_commit=False))
    return TestClient(_app())


class TestGetModelVideoCapabilities:
    """GET /providers/video-capabilities"""

    def test_video_backend_is_required(self, monkeypatch):
        with _client(monkeypatch, MagicMock()) as client:
            resp = client.get("/api/v1/providers/video-capabilities")
        assert resp.status_code == 422

    def test_malformed_video_backend_returns_400(self, monkeypatch):
        with _client(monkeypatch, MagicMock()) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "no-such-provider"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_backend_malformed"].format(value="no-such-provider")

    def test_resolver_value_error_returns_localized_422(self, monkeypatch):
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(side_effect=ValueError("model not found"))
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": "grok/unknown"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "model not found" not in detail
        assert detail == zh_errors.MESSAGES["video_model_capabilities_unresolved"].format(value="grok/unknown")

    def test_capability_bucket_error_returns_localized_400(self, monkeypatch):
        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(
            side_effect=VideoBucketCapabilityError(
                code="video_capability_missing_r2v",
                generation_type="r2v",
                provider_id="kling",
                model_id="kling-v3",
                message="lacks r2v",
            )
        )
        with _client(monkeypatch, resolver_instance) as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": "kling/kling-v3", "uses_reference_images": "true"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_missing_r2v"].format(
            provider="kling", model="kling-v3"
        )


class TestRealResolverResponse:
    """成功路径走真实 ConfigResolver + registry，覆盖响应体经 JSON 序列化后的实际形状。"""

    def test_returns_narrowed_durations_with_exclusion_reasons(self, real_resolver_client):
        """声明全集原样回传，收窄结果与成因由 duration_constraints 表达。"""
        with real_resolver_client as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": VEO, "resolution": "1080p"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider_id"] == "gemini-aistudio"
        assert body["model"] == "veo-3.1-generate-preview"
        assert body["supported_durations"] == [4, 6, 8]
        # excluded 在 Python 侧是 int 键；经 JSON 后只能是字符串键，前端按 String(duration) 查表。
        assert body["duration_constraints"] == {
            "resolution": "1080p",
            "uses_reference_images": False,
            "allowed": [8],
            "excluded": {"4": "resolution", "6": "resolution"},
        }

    def test_candidate_missing_from_registry_reports_bucket_failure(self, real_resolver_client):
        with real_resolver_client as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": "gemini-aistudio/deleted-model"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_reference_unavailable"].format(
            provider="gemini-aistudio", model="deleted-model"
        )

    def test_candidate_without_reference_capability_reports_bucket_failure(self, real_resolver_client):
        with real_resolver_client as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": "kling/kling-v3", "uses_reference_images": "true"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_missing_r2v"].format(
            provider="kling", model="kling-v3"
        )

    def test_reference_path_narrows_by_the_r2v_bucket(self, real_resolver_client):
        """参考图路径按 r2v 桶求值。

        取 720p：该档位本身不收窄时长，剔除成因只来自参考图约束。
        """
        with real_resolver_client as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": VEO, "resolution": "720p", "uses_reference_images": "true"},
            )
        assert resp.status_code == 200
        constraints = resp.json()["duration_constraints"]
        assert constraints["uses_reference_images"] is True
        assert constraints["resolution"] == "720p"
        assert constraints["allowed"] == [8]
        assert constraints["excluded"] == {"4": "reference", "6": "reference"}

    def test_reference_path_without_resolution_applies_no_resolution_constraint(self, real_resolver_client):
        """参考图路径未选档位时请求不携带分辨率：只剩参考图约束。"""
        with real_resolver_client as client:
            resp = client.get(
                "/api/v1/providers/video-capabilities",
                params={"video_backend": VEO, "uses_reference_images": "true"},
            )
        assert resp.status_code == 200
        constraints = resp.json()["duration_constraints"]
        assert constraints["resolution"] is None
        assert constraints["allowed"] == [8]
        assert constraints["excluded"] == {"4": "reference", "6": "reference"}

    def test_no_project_preferences_are_null(self, real_resolver_client):
        """无项目上下文：项目偏好字段为 None，不借用任何项目的已保存档位。"""
        with real_resolver_client as client:
            resp = client.get("/api/v1/providers/video-capabilities", params={"video_backend": VEO})
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_duration"] is None
        assert body["generation_mode"] is None
        assert body["duration_constraints"]["resolution"] is None

    @pytest.mark.parametrize("candidate", ["openai/sora-2", "openai"])
    def test_candidate_identity_and_default_constraints(self, real_resolver_client, candidate):
        with real_resolver_client as client:
            response = client.get("/api/v1/providers/video-capabilities", params={"video_backend": candidate})
        assert response.status_code == 200
        body = response.json()
        assert body["provider_id"] == "openai"
        assert body["model"] == "sora-2"
        constraints = body["duration_constraints"]
        assert constraints["resolution"] is None
        assert constraints["uses_reference_images"] is False
        assert constraints["allowed"] == body["supported_durations"]


@pytest.mark.parametrize("uses_reference_images", [False, True], ids=["i2v", "r2v"])
@pytest.mark.parametrize("resolution", [None, "720p", "1080p"])
async def test_duration_constraints_equal_facts_of_the_saved_candidate(
    db_engine, monkeypatch, uses_reference_images, resolution
):
    """创建向导预览的收窄结果，等于把候选模型与分辨率存进项目后该桶视频请求事实的结果。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr(providers, "async_session_factory", factory)
    params = {"video_backend": VEO, "uses_reference_images": str(uses_reference_images).lower()}
    if resolution is not None:
        params["resolution"] = resolution
    with TestClient(_app()) as client:
        response = client.get("/api/v1/providers/video-capabilities", params=params)

    generation_type = "r2v" if uses_reference_images else "i2v"
    saved: dict = {f"video_provider_{generation_type}": VEO}
    if resolution is not None:
        saved["model_settings"] = {VEO: {"resolution": resolution}}
    facts = await evaluate_video_request_facts(
        saved,
        route="reference_video" if uses_reference_images else "storyboard",
        generation_type=generation_type,
        identity=CONFIGURED_VIDEO_IDENTITY,
        resolver=ConfigResolver(factory),
    )
    assert isinstance(facts, VideoRequestFacts)
    assert response.status_code == 200, response.text
    constraints = response.json()["duration_constraints"]
    assert constraints["resolution"] == facts.resolution == resolution
    assert constraints["uses_reference_images"] is uses_reference_images
    assert constraints["allowed"] == list(facts.allowed_durations)
    assert constraints["excluded"] == {str(d): reason for d, reason in facts.excluded_durations}


def test_facts_failure_reports_its_problem_code(real_resolver_client, set_video_request_facts):
    """候选所在桶的视频请求事实解析不出时，按问题码返回本地化 422。"""
    set_video_request_facts(
        VideoRequestFactsFailure(
            "reference_supported_durations_incompatible",
            (("provider", "gemini-aistudio"), ("model", "veo-3.1-generate-preview"), ("resolution", "4k")),
        )
    )
    with real_resolver_client as client:
        response = client.get(
            "/api/v1/providers/video-capabilities",
            params={"video_backend": VEO, "resolution": "4k", "uses_reference_images": "true"},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == zh_errors.MESSAGES["reference_supported_durations_incompatible"].format(
        provider="gemini-aistudio", model="veo-3.1-generate-preview"
    )


@pytest.mark.parametrize("project_route", [False, True])
@pytest.mark.parametrize("disable_after_gate", [False, True])
async def test_disabled_candidate_never_returns_default_model(
    db_factory, tmp_path, monkeypatch, project_route, disable_after_gate
):
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="Candidate", discovery_format="openai", base_url="https://example.test", api_key="k"
        )
        session.add(provider)
        await session.flush()
        for model_id, default in (("selected", False), ("default", True)):
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id=model_id,
                    display_name=model_id,
                    endpoint="newapi-video",
                    supported_durations="[5]",
                    is_default=default,
                    is_enabled=default or disable_after_gate,
                    capability_overrides={"first_frame": True, "max_reference_images": 4},
                )
            )
        await session.commit()
        provider_id = make_provider_id(provider.id)

    changed = False

    @asynccontextmanager
    async def changing_sessions():
        nonlocal changed
        async with db_factory() as session:
            yield session
        if disable_after_gate and not changed:
            changed = True
            async with db_factory() as session:
                await session.execute(
                    update(CustomProviderModel)
                    .where(CustomProviderModel.model_id == "selected")
                    .values(is_enabled=False)
                )
                await session.commit()

    module = projects if project_route else providers
    monkeypatch.setattr(module, "async_session_factory", changing_sessions)
    params = {
        "_t": lambda key, **kwargs: key,
        "video_backend": f"{provider_id}/selected",
        "resolution": None,
        "uses_reference_images": True,
    }
    if project_route:
        manager = ProjectManager(tmp_path)
        manager.create_project("candidate")
        monkeypatch.setattr(projects, "get_project_manager", lambda: manager)
        request = projects.get_video_capabilities(name="candidate", **params)
    else:
        request = providers.get_model_video_capabilities(**params)
    with pytest.raises(BadRequestError) as error:
        await request
    assert error.value.key == "video_capability_reference_unavailable"
    assert error.value.params == {"provider": provider_id, "model": "selected"}
