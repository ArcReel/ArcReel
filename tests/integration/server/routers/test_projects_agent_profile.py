"""Tests for projects_agent_profile."""

from lib.i18n.zh import errors as zh_errors
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestProjectsRouter:
    def test_agent_profile_status_and_explicit_reset(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            status = client.get("/api/v1/projects/ready/agent-profile")
            assert status.status_code == 200
            assert status.json() == {
                "customized": True,
                "customized_files": ["CLAUDE.md", ".claude/agents/legacy.md"],
            }

            reset = client.post("/api/v1/projects/ready/agent-profile/reset")
            assert reset.status_code == 200
            assert reset.json() == {"customized": False, "customized_files": []}
            assert fake_pm.profile_reset_calls == ["ready"]

    def test_agent_profile_endpoints_reject_invalid_project_name(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            status = client.get("/api/v1/projects/illegal-name/agent-profile")
            reset = client.post("/api/v1/projects/illegal-name/agent-profile/reset")

        assert status.status_code == 400
        assert status.json()["detail"] == zh_errors.MESSAGES["invalid_project_name"].format(name="illegal-name")
        assert reset.status_code == 400
        assert reset.json()["detail"] == zh_errors.MESSAGES["invalid_project_name"].format(name="illegal-name")
