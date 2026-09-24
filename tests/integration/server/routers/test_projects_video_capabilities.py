"""projects 路由的 video-capabilities 查询。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.i18n.zh import errors as zh_errors
from server.routers import projects
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestGetVideoCapabilities:
    """GET /projects/{name}/video-capabilities"""

    def _patch_resolver(self, monkeypatch, side_effect=None, return_value=None):
        """用 MagicMock 替换 ConfigResolver 类，让其 instance.video_capabilities() 返回指定行为。"""
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        if side_effect is not None:
            resolver_instance.video_capabilities = AsyncMock(side_effect=side_effect)
        else:
            resolver_instance.video_capabilities = AsyncMock(return_value=return_value)
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)
        return resolver_instance

    def test_malformed_video_backend_returns_400(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, return_value={})
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "no-slash"},
            )
        assert resp.status_code == 400

    def test_unknown_project_returns_404(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, side_effect=FileNotFoundError("项目 'nonexistent' 不存在"))
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/nonexistent/video-capabilities")
            assert resp.status_code == 404

    def test_resolver_value_error_returns_422(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, side_effect=ValueError("model not found: grok/unknown"))
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
            assert resp.status_code == 422
            detail = resp.json()["detail"]
            # 异常原文只进日志，不进用户可见响应（en/vi 界面不能混入未译英文原文）
            assert "model not found" not in detail
            assert detail == zh_errors.MESSAGES["video_capabilities_unresolved"].format(name="ready")

    def test_capability_bucket_error_returns_localized_400(self, tmp_path, monkeypatch):
        """任务类型桶解析闸的报错转成结构化 400，带上修复指引，不被通用 422 文案吞掉。"""
        from lib.config.resolver import VideoBucketCapabilityError

        self._patch_resolver(
            monkeypatch,
            side_effect=VideoBucketCapabilityError(
                code="video_capability_missing_r2v",
                generation_type="r2v",
                provider_id="kling",
                model_id="kling-v3",
                message="video model kling/kling-v3 lacks the capability required by the r2v bucket",
            ),
        )
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_missing_r2v"].format(
                provider="kling", model="kling-v3"
            )


class TestRealResolverResponse:
    """成功路径走真实 ConfigResolver + registry + 内存 DB，覆盖响应体经 JSON 序列化后的实际形状。"""

    #: registry 里声明了「1080p 只剩 8 秒」「参考图路径只剩 8 秒」的型号。
    VEO = "gemini-aistudio/veo-3.1-generate-preview"

    @pytest.fixture
    def client(self, tmp_path, db_engine, monkeypatch) -> TestClient:
        monkeypatch.setattr(projects, "async_session_factory", async_sessionmaker(db_engine, expire_on_commit=False))
        pm = _FakePM(tmp_path)
        pm.project_data["ready"]["content_mode"] = "narration"
        pm.project_data["ready"]["video_backend"] = self.VEO
        pm.project_data["ready"]["model_settings"] = {self.VEO: {"resolution": "1080p"}}
        # resolver 走自己 import 的 get_project_manager，与路由那份是两个绑定。
        monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
        return build_projects_client(monkeypatch, pm)

    def test_saved_resolution_narrows_durations_with_reasons(self, client):
        """缺省上下文按项目已保存档位收窄；supported_durations 仍是型号声明全集。"""
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == "veo-3.1-generate-preview"
        assert body["supported_durations"] == [4, 6, 8]
        # excluded 在 Python 侧是 int 键；经 JSON 后只能是字符串键，前端按 String(duration) 查表。
        assert body["duration_constraints"] == {
            "resolution": "1080p",
            "uses_reference_images": False,
            "allowed": [8],
            "allowed_without_reference_images": [8],
            "excluded": {"4": "resolution", "6": "resolution"},
        }

    def test_candidate_missing_from_registry_reports_bucket_failure(self, client):
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "gemini-aistudio/deleted-model"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["video_capability_reference_unavailable"].format(
            provider="gemini-aistudio", model="deleted-model"
        )

    def test_explicit_auto_resolution_does_not_fall_back_to_saved(self, client):
        """``resolution`` 传空串是表单里的「自动」：不回退到已保存的 1080p，全集都可选。"""
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities", params={"resolution": ""})
        assert resp.status_code == 200
        constraints = resp.json()["duration_constraints"]
        assert constraints["resolution"] is None
        assert constraints["allowed"] == [4, 6, 8]
        assert constraints["excluded"] == {}

    def test_reference_context_overrides_project_generation_mode(self, client):
        """显式 ``uses_reference_images`` 压过项目生成模式，成因报 reference。"""
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"resolution": "720p", "uses_reference_images": "true"},
            )
        assert resp.status_code == 200
        constraints = resp.json()["duration_constraints"]
        assert constraints["allowed"] == [8]
        assert constraints["allowed_without_reference_images"] == [4, 6, 8]
        assert constraints["excluded"] == {"4": "reference", "6": "reference"}

    @pytest.mark.parametrize("candidate", ["openai/sora-2", "openai"])
    def test_candidate_identity_and_project_preferences(self, client, candidate):
        with client:
            response = client.get("/api/v1/projects/ready/video-capabilities", params={"video_backend": candidate})
        assert response.status_code == 200
        body = response.json()
        assert body["provider_id"] == "openai"
        assert body["model"] == "sora-2"
        assert body["generation_mode"] == "storyboard"
        assert body["content_mode"] == "narration"

    @pytest.mark.parametrize(("uses_reference_images", "allowed"), [(False, [4, 6, 8]), (True, [8])])
    def test_candidate_explicit_auto_resolution_and_bucket(self, client, uses_reference_images, allowed):
        with client:
            response = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={
                    "video_backend": self.VEO,
                    "resolution": "",
                    "uses_reference_images": str(uses_reference_images).lower(),
                },
            )
        assert response.status_code == 200
        constraints = response.json()["duration_constraints"]
        assert constraints["resolution"] is None
        assert constraints["uses_reference_images"] is uses_reference_images
        assert constraints["allowed"] == allowed

    def test_reference_no_image_tiers_use_i2v_facts_for_saved_and_candidate_queries(
        self, tmp_path, db_engine, monkeypatch
    ):
        """The no-image tier and exclusion reasons follow the configured i2v model in both endpoint variants."""
        pm = _FakePM(tmp_path)
        pm.project_data["ready"].update(
            {
                "generation_mode": "reference_video",
                "video_provider_r2v": self.VEO,
                "video_provider_i2v": "ark/doubao-seedance-2-0-260128",
            }
        )
        monkeypatch.setattr(projects, "async_session_factory", async_sessionmaker(db_engine, expire_on_commit=False))
        monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
        client = build_projects_client(monkeypatch, pm)
        with client:
            saved = client.get("/api/v1/projects/ready/video-capabilities")
            candidate = client.get("/api/v1/projects/ready/video-capabilities", params={"video_backend": self.VEO})
        for response in (saved, candidate):
            assert response.status_code == 200
            constraints = response.json()["duration_constraints"]
            assert constraints["allowed"] == [8]
            assert 5 in constraints["allowed_without_reference_images"]
            assert constraints["excluded_without_reference_images"] == {}
            assert constraints["without_reference_problem"] is None

    @pytest.mark.parametrize("candidate", [False, True])
    def test_reference_no_image_failure_is_structured(self, tmp_path, db_engine, monkeypatch, candidate):
        pm = _FakePM(tmp_path)
        pm.project_data["ready"].update({"generation_mode": "reference_video", "video_provider_r2v": self.VEO})
        monkeypatch.setattr(projects, "async_session_factory", async_sessionmaker(db_engine, expire_on_commit=False))
        monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
        client = build_projects_client(monkeypatch, pm)
        with client:
            response = client.get(
                "/api/v1/projects/ready/video-capabilities", params={"video_backend": self.VEO} if candidate else {}
            )
        assert response.status_code == 200
        constraints = response.json()["duration_constraints"]
        assert constraints["allowed_without_reference_images"] is None
        assert constraints["without_reference_problem"] == {
            "code": "reference_capability_unavailable",
            "params": {"capability": "i2v"},
            "action": "configure_video_model",
        }

    @pytest.mark.parametrize("candidate", [False, True])
    def test_reference_no_image_exclusions_follow_i2v_resolution(self, tmp_path, db_engine, monkeypatch, candidate):
        pm = _FakePM(tmp_path)
        pm.project_data["ready"].update(
            {
                "generation_mode": "reference_video",
                "video_provider_r2v": "ark/doubao-seedance-2-0-260128",
                "video_provider_i2v": self.VEO,
                "model_settings": {self.VEO: {"resolution": "1080p"}},
            }
        )
        monkeypatch.setattr(projects, "async_session_factory", async_sessionmaker(db_engine, expire_on_commit=False))
        monkeypatch.setattr("lib.config.resolver.get_project_manager", lambda: pm)
        client = build_projects_client(monkeypatch, pm)
        with client:
            response = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "ark/doubao-seedance-2-0-260128"} if candidate else {},
            )
        assert response.status_code == 200
        constraints = response.json()["duration_constraints"]
        assert constraints["allowed_without_reference_images"] == [8]
        assert constraints["excluded_without_reference_images"] == {"4": "resolution", "6": "resolution"}
