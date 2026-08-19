"""公开契约行为测试：skill.md 模板、OpenAPI 可写字段与非 JSON 响应例外。"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from lib import PROJECT_ROOT
from lib.profile_manifest import ContentMode
from lib.project_manager import SourceKind

# ---------------------------------------------------------------------------
# skill.md 模板内容验证
# ---------------------------------------------------------------------------


class TestSkillMdTemplate:
    """验证 skill.md.template 描述了全部取值与语义。"""

    @pytest.fixture(autouse=True)
    def _load_template(self):
        path = PROJECT_ROOT / "public" / "skill.md.template"
        self.template = path.read_text(encoding="utf-8")

    @pytest.mark.unit
    def test_content_mode_all_values_described(self):
        assert "`drama`" in self.template
        assert "`narration`" in self.template
        assert "`ad`" in self.template
        assert "content_mode" in self.template

    @pytest.mark.unit
    def test_source_kind_all_values_described(self):
        assert "`novel`" in self.template
        assert "`screenplay`" in self.template
        assert "source_kind" in self.template

    @pytest.mark.unit
    def test_generation_mode_all_values_described(self):
        assert "`storyboard`" in self.template
        assert "`reference_video`" in self.template
        assert "generation_mode" in self.template

    @pytest.mark.unit
    def test_grid_storyboard_described(self):
        assert "grid_storyboard" in self.template

    @pytest.mark.unit
    def test_no_all_json_claim(self):
        assert "所有 API 响应均为 JSON" not in self.template

    @pytest.mark.unit
    def test_non_json_exceptions_listed(self):
        assert "text/event-stream" in self.template
        assert "/skill.md" in self.template
        assert "application/zip" in self.template

    @pytest.mark.unit
    def test_no_old_product_names(self):
        assert "分镜板" not in self.template
        assert "分镜板（宫格）" not in self.template


# ---------------------------------------------------------------------------
# skill.md 端点行为
# ---------------------------------------------------------------------------


def _skill_md_app() -> FastAPI:
    """构造包含 /skill.md 端点的 mini app。"""
    import asyncio

    app = FastAPI()

    @app.get("/skill.md")
    async def serve_skill_md(request: Request) -> Response:
        from starlette.responses import PlainTextResponse

        template_path = PROJECT_ROOT / "public" / "skill.md.template"

        def _read() -> tuple[bool, str]:
            if not template_path.exists():
                return False, ""
            return True, template_path.read_text(encoding="utf-8")

        exists, template = await asyncio.to_thread(_read)
        if not exists:
            return PlainTextResponse("skill.md 模板不存在", status_code=404)

        forwarded_proto = request.headers.get("x-forwarded-proto")
        scheme = forwarded_proto or request.url.scheme or "http"
        host = request.url.netloc
        base_url = f"{scheme}://{host}"
        content = template.replace("{{BASE_URL}}", base_url)
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")

    return app


class TestSkillMdEndpoint:
    """验证 /skill.md 端点的响应语义。"""

    @pytest.mark.unit
    def test_returns_text_not_json(self):
        client = TestClient(_skill_md_app())
        with client:
            resp = client.get("/skill.md")
        assert resp.status_code == 200
        ct = resp.headers["content-type"]
        assert "text/markdown" in ct
        assert "application/json" not in ct

    @pytest.mark.unit
    def test_base_url_substitution(self):
        client = TestClient(_skill_md_app())
        with client:
            resp = client.get("/skill.md")
        body = resp.text
        assert "{{BASE_URL}}" not in body
        assert "http://testserver" in body


# ---------------------------------------------------------------------------
# OpenAPI 写模型：不可写字段不在 UpdateProjectRequest 中
# ---------------------------------------------------------------------------


class TestUpdateProjectWritableFields:
    """验证 UpdateProjectRequest 不暴露服务端必然拒绝的字段。"""

    @pytest.mark.unit
    def test_content_mode_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "content_mode" not in UpdateProjectRequest.model_fields

    @pytest.mark.unit
    def test_source_kind_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "source_kind" not in UpdateProjectRequest.model_fields

    @pytest.mark.unit
    def test_image_backend_not_in_update_model(self):
        from server.routers.projects import UpdateProjectRequest

        assert "image_backend" not in UpdateProjectRequest.model_fields


# ---------------------------------------------------------------------------
# 枚举语义：content_mode / generation_mode / source_kind
# ---------------------------------------------------------------------------


class TestEnumSemantics:
    """验证枚举类型的取值集合与 CONTEXT.md 一致。"""

    @pytest.mark.unit
    def test_content_mode_values(self):
        from typing import get_args

        values = set(get_args(ContentMode))
        assert values == {"drama", "narration", "ad"}

    @pytest.mark.unit
    def test_source_kind_values(self):
        from typing import get_args

        values = set(get_args(SourceKind))
        assert values == {"novel", "screenplay"}

    @pytest.mark.unit
    def test_generation_mode_create_rejects_invalid(self):
        from server.routers.projects import CreateProjectRequest

        with pytest.raises(Exception):
            CreateProjectRequest(name="x", title="X", generation_mode="grid")

    @pytest.mark.unit
    def test_generation_mode_create_accepts_valid(self):
        from server.routers.projects import CreateProjectRequest

        req = CreateProjectRequest(name="x", title="X", generation_mode="storyboard")
        assert req.generation_mode == "storyboard"

        req2 = CreateProjectRequest(name="x", title="X", generation_mode="reference_video")
        assert req2.generation_mode == "reference_video"
