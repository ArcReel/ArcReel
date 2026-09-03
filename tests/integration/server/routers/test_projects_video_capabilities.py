"""projects 路由的 video-capabilities 查询。"""

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

    def test_returns_capabilities_json(self, tmp_path, monkeypatch):
        fake_caps = {
            "provider_id": "grok",
            "model": "grok-imagine-video",
            "supported_durations": list(range(1, 16)),
            "max_duration": 15,
            "max_reference_images": 7,
            "source": "registry",
            "default_duration": None,
            "content_mode": "narration",
            "generation_mode": "reference_video",
        }
        self._patch_resolver(monkeypatch, return_value=fake_caps)
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
            assert resp.status_code == 200
            assert resp.json() == fake_caps

    def test_video_backend_param_resolves_candidate_model(self, tmp_path, monkeypatch):
        """带 video_backend 时按候选模型解析，而不是按已落盘配置。

        设置表单里用户改了下拉但尚未保存，若仍按落盘配置解析，voice_consistency 等二维派生值
        会停留在上一次保存的模型上，界面显示的档位与用户当前选择不符。
        """
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        resolver_instance.video_capabilities = AsyncMock(return_value={"model": "saved-model"})
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={"model": "candidate"})
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)

        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "openai/sora-2"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"model": "candidate"}
        resolver_instance.video_capabilities.assert_not_awaited()
        assert resolver_instance.video_capabilities_for_model.await_args.args[:2] == ("openai", "sora-2")

    def test_capabilities_resolve_by_project_route_without_episode(self, tmp_path, monkeypatch):
        """能力按项目生成模式定轴：端点不接受集号，解析只带项目（与候选模型）。"""
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        resolver_instance.video_capabilities = AsyncMock(return_value={"model": "saved-model"})
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={"model": "candidate"})
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)

        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            assert client.get("/api/v1/projects/ready/video-capabilities").status_code == 200
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "openai/sora-2"},
            )
        assert resp.status_code == 200
        assert resolver_instance.video_capabilities.await_args.args == ("ready",)
        # 候选模型解析拿到的第三个入参必须是该项目的已加载数据（含项目生成模式），只断言参数个数的话
        # 路由传 None 或传错项目都照样通过。
        passed_project = resolver_instance.video_capabilities_for_model.await_args.args[2]
        assert passed_project["title"] == "Ready"
        assert passed_project["generation_mode"] == "storyboard"

    def test_constraint_context_is_forwarded_to_resolver(self, tmp_path, monkeypatch):
        """``resolution`` / ``uses_reference_images`` 原样交给 resolver：收窄规则只在后端一处，路由不解读。"""
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        resolver_instance.video_capabilities = AsyncMock(return_value={"model": "saved-model"})
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={"model": "candidate"})
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)

        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"resolution": "1080p", "uses_reference_images": "true"},
            )
            assert resp.status_code == 200
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "openai/sora-2", "resolution": "", "uses_reference_images": "false"},
            )
            assert resp.status_code == 200
        assert resolver_instance.video_capabilities.await_args.kwargs == {
            "resolution": "1080p",
            "uses_reference_images": True,
        }
        # 空串是表单里显式的「自动」档位，必须原样送达而不是被折叠成缺省（缺省会回退到已保存档位）
        assert resolver_instance.video_capabilities_for_model.await_args.kwargs == {
            "resolution": "",
            "uses_reference_images": False,
        }

    def test_constraint_context_defaults_to_none(self, tmp_path, monkeypatch):
        """不带约束参数时两项都是 None，由 resolver 按项目已保存档位与生成模式求值。"""
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        resolver_instance.video_capabilities = AsyncMock(return_value={"model": "saved-model"})
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)

        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            assert client.get("/api/v1/projects/ready/video-capabilities").status_code == 200
        assert resolver_instance.video_capabilities.await_args.kwargs == {
            "resolution": None,
            "uses_reference_images": None,
        }

    def test_malformed_video_backend_returns_400(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, return_value={})
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "no-slash"},
            )
        assert resp.status_code == 400

    def test_bare_provider_video_backend_resolves_default_model(self, tmp_path, monkeypatch):
        """裸 provider（无 "/"）按 registry 默认视频 model 补全，不再被判定为格式错误。

        存量项目的 video_backend 可以是裸 provider 覆盖（见 `_parse_project_provider`），设置
        表单未改选时原样带上，回归会让这类项目的能力查询恒 400。
        """
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        resolver_instance.video_capabilities_for_model = AsyncMock(return_value={"model": "candidate"})
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)

        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get(
                "/api/v1/projects/ready/video-capabilities",
                params={"video_backend": "openai"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"model": "candidate"}
        provider_id, model_id = resolver_instance.video_capabilities_for_model.await_args.args[:2]
        assert provider_id == "openai"
        assert model_id

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
                capability="r2v",
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
