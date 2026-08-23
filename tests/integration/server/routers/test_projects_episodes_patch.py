"""Tests for projects_episodes_patch (split from test_projects_router.py)."""

import json

from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.i18n.zh import errors as zh_errors
from lib.project_manager import ProjectManager
from tests.integration.server.routers.conftest import (
    _client,
    _FakePM,
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
                        "transition_to_next": "cut",
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

        with _client(monkeypatch, pm) as client:
            response = client.patch(
                "/api/v1/projects/demo",
                json={"episodes": [{"episode": 1, "script_file": "scripts/new.json"}]},
            )

        assert response.status_code == 200, response.text
        assert pm.load_project("demo")["episodes"][0]["script_file"] == "scripts/new.json"
        assert all(adapter.get_entry(key) is None for key in old_keys)
        assert adapter.get_entry(unrelated) == unrelated_entry

    def test_patch_project_episodes_updates_script_file(self, tmp_path, monkeypatch):
        """PATCH /projects/{name} with episodes[] 只更新匹配集的白名单字段。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
            {"episode": 2, "title": "第二集", "script_file": "scripts/ep2.json"},
        ]

        client = _client(monkeypatch, fake_pm)
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
            # 第二集不受影响
            assert ep2["script_file"] == "scripts/ep2.json"

    def test_patch_project_episodes_has_no_route_field(self, tmp_path, monkeypatch):
        """生成模式按项目定轴：集级 PATCH 模型结构上无 generation_mode，出现即被静默丢弃、不写盘。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
        ]

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 1, "generation_mode": "reference_video"}]},
            )
            assert resp.status_code == 200
            ep1 = fake_pm.project_data["ready"]["episodes"][0]
            assert "generation_mode" not in ep1

    def test_patch_project_episodes_strips_computed_fields(self, tmp_path, monkeypatch):
        """PATCH 不得把项目摘要读时注入的每集统计字段写回 project.json；title 也不可经此端点写入。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "原标题", "script_file": "scripts/ep1.json"},
        ]

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "episodes": [
                        {
                            "episode": 1,
                            "script_file": "scripts/ep1_v2.json",  # 合法白名单字段
                            "title": "新标题",  # title 不再可经 PATCH /projects 写入
                            # 以下为项目摘要读时注入的统计字段，不应写入磁盘
                            "item_count": 999,
                            "scenes_count": 999,  # 已退场的旧字段，同样不得落盘
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
            # 合法字段应被写入
            assert ep1["script_file"] == "scripts/ep1_v2.json"
            # title 不可经此端点改写，保持原值（改名走 PATCH /episodes/{episode}）
            assert ep1["title"] == "原标题"
            # 计算字段不得写入
            assert "item_count" not in ep1
            assert "scenes_count" not in ep1
            assert "status" not in ep1
            assert "storyboards" not in ep1
            assert "videos" not in ep1
            assert "script_status" not in ep1
            assert "duration_seconds" not in ep1

    def test_patch_project_episodes_skips_unknown_episode(self, tmp_path, monkeypatch):
        """PATCH 传入未知 episode 编号时，静默跳过，不改变已有 episodes。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
            {"episode": 2, "title": "第二集", "script_file": "scripts/ep2.json"},
        ]

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 999, "script_file": "scripts/ep999.json"}]},
            )
            assert resp.status_code == 200
            episodes = fake_pm.project_data["ready"]["episodes"]
            # 集数不变
            assert len(episodes) == 2
            # 已有字段不受影响
            assert episodes[0]["script_file"] == "scripts/ep1.json"
            assert episodes[1]["script_file"] == "scripts/ep2.json"

    def test_update_episode_title_renames_script_and_mirror(self, tmp_path, monkeypatch):
        """PATCH /episodes/{episode}：剧本顶层 title 与 project.json 镜像都反映新值，标题首尾空白被裁剪。"""
        fake_pm = _FakePM(tmp_path)
        # 剧本带 episode 字段，触发 _apply_episode_sync 镜像（与真实生成剧本一致）
        fake_pm.scripts[("ready", "episode_1.json")]["episode"] = 1

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "  新集名  "})
            assert resp.status_code == 200
            assert resp.json()["episode"]["title"] == "新集名"
            # 剧本顶层 title 落盘
            assert fake_pm.scripts[("ready", "episode_1.json")]["title"] == "新集名"
            # project.json 镜像同步
            ep = next(e for e in fake_pm.project_data["ready"]["episodes"] if e["episode"] == 1)
            assert ep["title"] == "新集名"

    def test_update_episode_title_empty_rejected(self, tmp_path, monkeypatch):
        """空/纯空白标题被拒（422），不进锁。"""
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            for blank in ("", "   "):
                resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": blank})
                assert resp.status_code == 422

    def test_update_episode_missing_episode_404(self, tmp_path, monkeypatch):
        """不存在的 episode → 404。"""
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/99", json={"title": "x"})
            assert resp.status_code == 404

    def test_update_episode_missing_project_404(self, tmp_path, monkeypatch):
        """项目不存在 → 404，不退化成 500。

        锁内抛出的 NotFoundError 不是 HTTPException 子类，外层 except 阶梯必须
        同时放行 ApiError，否则被兜底分支吞成 internal_server_error。
        """
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.patch("/api/v1/projects/nope/episodes/1", json={"title": "x"})
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["project_not_found"].format(name="nope")

    def test_update_episode_stale_script_binding_404(self, tmp_path, monkeypatch):
        """项目在但 project.json 指向的剧本文件已丢失（stale 绑定）→ 404 而非 500。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"][0]["script_file"] = "scripts/gone.json"

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "x"})
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["ref_script_missing"]
