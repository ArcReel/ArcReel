"""Public behavior contracts: Agent installation guides, writable OpenAPI fields, enum semantics."""

import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from lib import PROJECT_ROOT
from lib.agent.profile_manifest import ContentMode
from lib.episode.source_kinds import SourceKind

# ---------------------------------------------------------------------------
# Agent installation-guide content.
# ---------------------------------------------------------------------------


class TestAgentInstallationGuide:
    """Public guides contain only external-Agent installation/wiring steps."""

    @pytest.fixture(autouse=True)
    def _load_template(self):
        path = PROJECT_ROOT / "public" / "agent-installation-guide.md"
        self.guide = path.read_text(encoding="utf-8")

    def test_covers_installation_and_connection(self):
        assert "arc-" in self.guide
        assert "npx skills add ArcReel/skills" in self.guide
        assert "setup-arcreel-skills" in self.guide
        assert "video-workflow" in self.guide
        assert "{{BASE_URL}}/mcp" in self.guide
        assert "host/workspace local conventions" in self.guide

    def test_excludes_rest_workflow_reference(self):
        assert "/api/v1/" not in self.guide
        assert "curl " not in self.guide
        assert "agent/chat" not in self.guide


# ---------------------------------------------------------------------------
# Agent installation-guide endpoint behavior.
# ---------------------------------------------------------------------------


def _installation_guide_app() -> FastAPI:
    """Mount the real installation-guide handler in a mini app instead of copying its implementation."""
    from server.app import serve_agent_installation_guide

    app = FastAPI()
    app.add_api_route("/agent-installation-guide.md", serve_agent_installation_guide, methods=["GET"])
    return app


class TestAgentInstallationGuideEndpoint:
    """Verify installation-endpoint response semantics."""

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

        paths = {route.path for route in iter_route_contexts(app.routes)}
        assert "/api/v1/projects" in paths
        assert "/skill.md" not in paths
        assert "/api/v1/agent/chat" not in paths


# ---------------------------------------------------------------------------
# OpenAPI write model: unwritable fields stay out of UpdateProjectRequest.
# ---------------------------------------------------------------------------


class TestUpdateProjectWritableFields:
    """UpdateProjectRequest must not expose fields the server always rejects."""

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
# Enum semantics: content_mode / generation_mode / source_kind.
# ---------------------------------------------------------------------------


class TestEnumSemantics:
    """Enum value sets agree with GLOSSARY.md."""

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
