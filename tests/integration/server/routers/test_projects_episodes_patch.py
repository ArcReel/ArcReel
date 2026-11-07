"""Tests for projects_episodes_patch."""

import json

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.episode.episode_management import create_episode
from lib.i18n.zh import errors as zh_errors
from lib.project.project_manager import ProjectManager
from lib.script.blank_script import start_blank_script
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
)


class TestProjectsRouter:
    # Episodes PATCH tests
    # ---------------------------------------------------------------------------

    def test_patch_project_episode_rebinding_forgets_unbound_resource_claims(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        project_dir = pm.get_project_path("demo")

        def _script(resource_id: str) -> dict:
            return {
                "episode": 1,
                "title": "Episode 1",
                "content_mode": "narration",
                "duration_seconds": 4,
                "summary": "",
                "novel": {"title": "Demo", "chapter": "Chapter 1"},
                "segments": [
                    {
                        "segment_id": resource_id,
                        "duration_seconds": 4,
                        "segment_break": False,
                        "novel_text": "text",
                        "characters_in_segment": [],
                        "scenes": [],
                        "props": [],
                        "image_prompt": "image",
                        "video_prompt": "video",
                        "generated_assets": {},
                    }
                ],
            }

        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "old.json").write_text(json.dumps(_script("E1S01")), encoding="utf-8")
        (scripts_dir / "new.json").write_text(json.dumps(_script("E1S02")), encoding="utf-8")
        pm.update_project(
            "demo",
            lambda project: project.__setitem__(
                "episodes",
                [{"episode": 1, "title": "Episode 1", "script_file": "scripts/old.json"}],
            ),
        )

        adapter = ProjectArtifactManifestAdapter(project_dir)
        old_keys = ArtifactKey.episode_resource_artifacts(1, "E1S01")
        for index, key in enumerate(old_keys):
            adapter.put_entry(
                key,
                ArtifactManifestEntry(
                    artifact_path=f"formal/old-{index}.bin",
                    basis_digest=f"sha256-v1:{index:064x}",
                ),
            )
        unrelated = ArtifactKey.asset_sheet("character", "Unrelated")
        unrelated_entry = ArtifactManifestEntry(
            artifact_path="characters/Unrelated.png",
            basis_digest=f"sha256-v1:{99:064x}",
        )
        adapter.put_entry(unrelated, unrelated_entry)

        with build_projects_client(monkeypatch, pm) as client:
            response = client.patch(
                "/api/v1/projects/demo",
                json={"episodes": [{"episode": 1, "script_file": "scripts/new.json"}]},
            )

        assert response.status_code == 200, response.text
        assert pm.load_project("demo")["episodes"][0]["script_file"] == "scripts/new.json"
        assert all(adapter.get_entry(key) is None for key in old_keys)
        assert adapter.get_entry(unrelated) == unrelated_entry

    def test_patch_project_episodes_updates_script_file(self, tmp_path, monkeypatch):
        """PATCH /projects/{name} episodes[] updates matching episodes' whitelisted fields only."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
            {"episode": 2, "title": "第二集", "script_file": "scripts/ep2.json"},
        ]

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 1, "script_file": "scripts/ep1_v2.json"}]},
            )
            assert resp.status_code == 200
            episodes = fake_pm.project_data["ready"]["episodes"]
            ep1 = next(e for e in episodes if e["episode"] == 1)
            ep2 = next(e for e in episodes if e["episode"] == 2)
            assert ep1["script_file"] == "scripts/ep1_v2.json"
            # Episode 2 unaffected.
            assert ep2["script_file"] == "scripts/ep2.json"

    def test_patch_project_episodes_has_no_route_field(self, tmp_path, monkeypatch):
        """Project-level modes: episode PATCH structurally excludes generation_mode, silently discarding presence without writes."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
        ]

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 1, "generation_mode": "reference_video"}]},
            )
            assert resp.status_code == 200
            ep1 = fake_pm.project_data["ready"]["episodes"][0]
            assert "generation_mode" not in ep1

    def test_patch_project_episodes_strips_computed_fields(self, tmp_path, monkeypatch):
        """PATCH never writes read-injected summary statistics or titles back into project.json."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "原标题", "script_file": "scripts/ep1.json"},
        ]

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "episodes": [
                        {
                            "episode": 1,
                            "script_file": "scripts/ep1_v2.json",  # Valid whitelisted fields
                            "title": "新标题",  # PATCH /projects no longer writes title.
                            # Following fields are read-injected per-episode summary statistics, never persisted.
                            "item_count": 999,
                            "scenes_count": 999,  # Retired fields likewise never persist.
                            "status": "completed",
                            "storyboards": {"total": 5, "available": 3, "stale": 0},
                            "videos": {"total": 5, "available": 5, "stale": 1},
                            "script_status": "segmented",
                            "duration_seconds": 120,
                        }
                    ]
                },
            )
            assert resp.status_code == 200
            ep1 = fake_pm.project_data["ready"]["episodes"][0]
            # Valid fields persist.
            assert ep1["script_file"] == "scripts/ep1_v2.json"
            # This endpoint retains title; rename through PATCH /episodes/{episode}.
            assert ep1["title"] == "原标题"
            # Computed fields never persist.
            assert "item_count" not in ep1
            assert "scenes_count" not in ep1
            assert "status" not in ep1
            assert "storyboards" not in ep1
            assert "videos" not in ep1
            assert "script_status" not in ep1
            assert "duration_seconds" not in ep1

    def test_patch_project_episodes_skips_unknown_episode(self, tmp_path, monkeypatch):
        """Unknown PATCH episode numbers silently skip without changing existing episodes."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
            {"episode": 2, "title": "第二集", "script_file": "scripts/ep2.json"},
        ]

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 999, "script_file": "scripts/ep999.json"}]},
            )
            assert resp.status_code == 200
            episodes = fake_pm.project_data["ready"]["episodes"]
            # Episode counts unchanged.
            assert len(episodes) == 2
            # Existing fields unaffected.
            assert episodes[0]["script_file"] == "scripts/ep1.json"
            assert episodes[1]["script_file"] == "scripts/ep2.json"

    def test_update_episode_title_renames_script_and_mirror(self, tmp_path, monkeypatch):
        """PATCH /episodes/{episode} updates script titles/project mirrors, trimming outer whitespace."""
        fake_pm = _FakePM(tmp_path)
        # Integer script episode triggers _apply_episode_sync mirrors, matching generated scripts.
        fake_pm.scripts[("ready", "episode_1.json")]["episode"] = 1

        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "  新集名  "})
            assert resp.status_code == 200
            assert resp.json()["episode"]["title"] == "新集名"
            # Persist top-level script title.
            assert fake_pm.scripts[("ready", "episode_1.json")]["title"] == "新集名"
            # Synchronize project.json mirrors.
            ep = next(e for e in fake_pm.project_data["ready"]["episodes"] if e["episode"] == 1)
            assert ep["title"] == "新集名"

    def test_update_episode_title_cleared_to_empty(self, tmp_path, monkeypatch):
        """Empty/whitespace titles clear script/ledger values; presentation derives names by playback positions."""
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")]["episode"] = 1
        client = build_projects_client(monkeypatch, fake_pm)
        with client:
            for blank in ("", "   "):
                resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": blank})
                assert resp.status_code == 200
                assert resp.json()["episode"]["title"] == ""
                assert fake_pm.scripts[("ready", "episode_1.json")]["title"] == ""
                ep = next(e for e in fake_pm.project_data["ready"]["episodes"] if e["episode"] == 1)
                assert ep["title"] == ""

    def test_update_episode_missing_episode_404(self, tmp_path, monkeypatch):
        """Missing episodes → 404."""
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/99", json={"title": "x"})
            assert resp.status_code == 404

    def test_update_episode_missing_project_404(self, tmp_path, monkeypatch):
        """Missing projects → 404, never fallback 500.

        Locked NotFoundError is not HTTPException; outer except chains must
        also pass ApiError, avoiding internal_server_error fallback.
        """
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch("/api/v1/projects/nope/episodes/1", json={"title": "x"})
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["project_not_found"].format(name="nope")

    def test_update_episode_title_without_script_is_kept_on_the_ledger_entry(self, tmp_path, monkeypatch):
        """Scriptless episode titles persist in ledger entries, initializing later blank-created scripts."""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        episode = create_episode(pm, "demo")

        with build_projects_client(monkeypatch, pm) as client:
            resp = client.patch(f"/api/v1/projects/demo/episodes/{episode}", json={"title": "番外：雪夜"})

        assert resp.status_code == 200, resp.text
        assert pm.load_project("demo")["episodes"][0]["title"] == "番外：雪夜"
        filename = start_blank_script(pm, "demo", episode)
        assert pm.load_script("demo", filename)["title"] == "番外：雪夜"
