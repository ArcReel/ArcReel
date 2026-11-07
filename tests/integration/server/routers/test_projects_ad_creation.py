"""Tests for projects_ad_creation."""

from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestProjectsRouter:
    def test_create_ad_project(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            # Defaults: omitted target_duration → 60; brief may empty; one episode always; no default_duration.
            created = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "ad-default",
                    "title": "Ad",
                    "content_mode": "ad",
                    "aspect_ratio": "9:16",
                },
            )
            assert created.status_code == 200
            project = created.json()["project"]
            assert project["content_mode"] == "ad"
            assert project["target_duration"] == 60
            assert project["brief"] == ""
            assert project["episodes"] == [
                {"episode": 1, "title": "", "script_file": "scripts/episode_1.json", "source_origin": "none"}
            ]
            assert "default_duration" not in project

            # Data layers do not hard-enumerate durations; any positive integer seconds valid.
            custom = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "ad-custom",
                    "content_mode": "ad",
                    "target_duration": 47,
                    "brief": "卖点",
                },
            )
            assert custom.status_code == 200
            assert custom.json()["project"]["target_duration"] == 47
            assert custom.json()["project"]["brief"] == "卖点"

    def test_create_ad_project_rejects_incompatible_fields(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            # ad omits default_duration.
            with_default = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "ad-a", "content_mode": "ad", "default_duration": 8},
            )
            assert with_default.status_code == 400

            # ad omits episode_target_duration; target_duration budgets episode volume.
            with_episode_target = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "ad-etd",
                    "content_mode": "ad",
                    "episode_target_duration": 120,
                },
            )
            assert with_episode_target.status_code == 400

            # ad forbids grids.
            with_grid = client.post(
                "/api/v1/projects",
                json={"name": "ad-b", "content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": True},
            )
            assert with_grid.status_code == 400

            # Request models reject nonpositive/noninteger target_duration.
            bad_duration = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "ad-c", "content_mode": "ad", "target_duration": 0},
            )
            assert bad_duration.status_code == 422

            # target_duration/brief are ad-only.
            narration_with_td = client.post(
                "/api/v1/projects",
                json={
                    "generation_mode": "storyboard",
                    "name": "n-a",
                    "content_mode": "narration",
                    "target_duration": 60,
                },
            )
            assert narration_with_td.status_code == 400
            narration_with_brief = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "n-b", "content_mode": "narration", "brief": "x"},
            )
            assert narration_with_brief.status_code == 400

    def test_patch_ad_project_fields(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            ignored_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "ad"},
            )
            assert ignored_mode.status_code == 200
            assert "content_mode" not in fake_pm.project_data["ready"]

            # ad target_duration accepts any positive integer seconds.
            updated = client.patch(
                "/api/v1/projects/ad-ready",
                json={"target_duration": 23},
            )
            assert updated.status_code == 200
            assert updated.json()["project"]["target_duration"] == 23

            # brief edits/clears to empty strings.
            brief_set = client.patch(
                "/api/v1/projects/ad-ready",
                json={"brief": "新卖点"},
            )
            assert brief_set.status_code == 200
            assert brief_set.json()["project"]["brief"] == "新卖点"
            brief_clear = client.patch(
                "/api/v1/projects/ad-ready",
                json={"brief": None},
            )
            assert brief_clear.status_code == 200
            assert brief_clear.json()["project"]["brief"] == ""

            # ad has no default_duration/grids; target_duration cannot clear.
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": 8}).status_code == 400
            # Reject field presence including null, which otherwise silently clears with 200, violating forbidden-write contracts.
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": None}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"episode_target_duration": 120}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"episode_target_duration": None}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"grid_storyboard": True}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"target_duration": None}).status_code == 400

            # Nonads reject target_duration/brief.
            assert client.patch("/api/v1/projects/ready", json={"target_duration": 60}).status_code == 400
            assert client.patch("/api/v1/projects/ready", json={"brief": "x"}).status_code == 400
