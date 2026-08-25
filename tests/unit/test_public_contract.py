"""公开契约行为测试：Agent 安装指引、OpenAPI 可写字段与枚举语义。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib import PROJECT_ROOT
from lib.profile_manifest import ContentMode
from lib.project_manager import SourceKind

# ---------------------------------------------------------------------------
# Agent 安装指引内容验证
# ---------------------------------------------------------------------------


class TestAgentInstallationGuide:
    """验证公开指引只承载外部 Agent 的安装与接线步骤。"""

    @pytest.fixture(autouse=True)
    def _load_template(self):
        path = PROJECT_ROOT / "public" / "agent-installation-guide.md"
        self.guide = path.read_text(encoding="utf-8")

    def test_covers_installation_and_connection(self):
        assert "arc-" in self.guide
        assert "npx skills add ArcReel/ArcReel@setup-arcreel-skills" in self.guide
        assert "npx skills add ArcReel/ArcReel@video-workflow" in self.guide
        assert "{{BASE_URL}}/mcp" in self.guide
        assert "验证" in self.guide
        assert "tool_timeout_sec" in self.guide

    def test_excludes_rest_workflow_reference(self):
        assert "/api/v1/" not in self.guide
        assert "curl " not in self.guide
        assert "agent/chat" not in self.guide


# ---------------------------------------------------------------------------
# Agent 安装指引端点行为
# ---------------------------------------------------------------------------


def _installation_guide_app() -> FastAPI:
    """把真实的安装指引处理函数挂到 mini app 上，避免测试复制实现。"""
    from server.app import serve_agent_installation_guide

    app = FastAPI()
    app.add_api_route("/agent-installation-guide.md", serve_agent_installation_guide, methods=["GET"])
    return app


class TestAgentInstallationGuideEndpoint:
    """验证安装指引端点的响应语义。"""

    def test_returns_text_not_json(self):
        client = TestClient(_installation_guide_app())
        with client:
            resp = client.get("/agent-installation-guide.md")
        assert resp.status_code == 200
        ct = resp.headers["content-type"]
        assert "text/markdown" in ct
        assert "application/json" not in ct

    def test_base_url_substitution(self):
        client = TestClient(_installation_guide_app())
        with client:
            resp = client.get("/agent-installation-guide.md")
        body = resp.text
        assert "{{BASE_URL}}" not in body
        assert "http://testserver" in body

    def test_legacy_endpoints_are_not_registered(self):
        from server.app import app

        paths = {path for route in app.routes if (path := getattr(route, "path", None))}
        assert "/skill.md" not in paths
        assert "/api/v1/agent/chat" not in paths


# ---------------------------------------------------------------------------
# OpenAPI 写模型：不可写字段不在 UpdateProjectRequest 中
# ---------------------------------------------------------------------------


class TestUpdateProjectWritableFields:
    """验证 UpdateProjectRequest 不暴露服务端必然拒绝的字段。"""

    def test_content_mode_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "content_mode" not in UpdateProjectRequest.model_fields

    def test_source_kind_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "source_kind" not in UpdateProjectRequest.model_fields

    def test_image_backend_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "image_backend" not in UpdateProjectRequest.model_fields


# ---------------------------------------------------------------------------
# 枚举语义：content_mode / generation_mode / source_kind
# ---------------------------------------------------------------------------


class TestEnumSemantics:
    """验证枚举类型的取值集合与 CONTEXT.md 一致。"""

    def test_content_mode_values(self):
        from typing import get_args

        values = set(get_args(ContentMode))
        assert values == {"drama", "narration", "ad"}

    def test_source_kind_values(self):
        from typing import get_args

        values = set(get_args(SourceKind))
        assert values == {"novel", "screenplay"}

    def test_generation_mode_create_rejects_invalid(self):
        from pydantic import ValidationError

        from server.routers.projects import CreateProjectRequest

        with pytest.raises(ValidationError):
            CreateProjectRequest(name="x", title="X", generation_mode="grid")

    def test_generation_mode_create_accepts_valid(self):
        from server.routers.projects import CreateProjectRequest

        req = CreateProjectRequest(name="x", title="X", generation_mode="storyboard")
        assert req.generation_mode == "storyboard"

        req2 = CreateProjectRequest(name="x", title="X", generation_mode="reference_video")
        assert req2.generation_mode == "reference_video"
