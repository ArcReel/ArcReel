"""Tests for projects_list_and_status."""

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
    _FakeSummaries,
)


class TestProjectsRouter:
    def test_list_projects_shares_script_preload_with_status(self, tmp_path, monkeypatch):
        """list_projects 一次性加载 episode scripts，传给项目摘要投影，去除 cover + status 双重 I/O。"""
        fake_pm = _FakePM(tmp_path)
        # 统计 load_script 调用次数：共享预加载后，ready 项目应只触发一次。
        orig_load_script = fake_pm.load_script
        calls: list[tuple[str, str]] = []

        def _counting_load(name, script_file):
            calls.append((name, script_file))
            return orig_load_script(name, script_file)

        fake_pm.load_script = _counting_load

        fake_summaries = _FakeSummaries()
        client = _client(monkeypatch, fake_pm, fake_summaries)
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200

        # ready 只有 1 集 script_file="scripts/episode_1.json"：预加载一次。
        # 若 cover + status 各自独立加载，这里会是 2 次。
        ready_calls = [c for c in calls if c[0] == "ready"]
        assert len(ready_calls) == 1, f"expected 1 shared load, got {ready_calls}"

        # 预加载 map 被传给项目摘要投影
        assert fake_summaries.last_preloaded_scripts is not None
        assert "scripts/episode_1.json" in fake_summaries.last_preloaded_scripts

    def test_list_projects_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """列表页的阶段与计数一律来自项目摘要：四值阶段在，五值 current_phase 不在。"""
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects")

        assert resp.status_code == 200
        status = next(item for item in resp.json()["projects"] if item["name"] == "ready")["status"]
        assert status["phase"] == "production"
        assert "current_phase" not in status
        assert status["assets"]["character"] == {"total": 1, "available": 0, "stale": 0}
        # 每集明细归项目详情端点，不驮在 N 个项目的列表里
        assert "episodes" not in status

    def test_get_project_status_comes_from_the_project_summary(self, tmp_path, monkeypatch):
        """全局头读的项目级状态与列表同源。"""
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        status = resp.json()["project"]["status"]
        assert status["phase"] == "production"
        assert status["needs_repair"] is False
        assert "current_phase" not in status

    def test_get_project_episode_fields_come_from_the_project_summary(self, tmp_path, monkeypatch):
        """剧集卡 / 剧集头读的每集字段由项目摘要提供，project.json 侧字段原样保留。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
        ]
        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        episode = resp.json()["project"]["episodes"][0]
        # project.json 侧字段不被覆盖
        assert (episode["title"], episode["script_file"]) == ("第一集", "scripts/ep1.json")
        # 摘要侧字段按产物清单口径注入：可用 = current ∪ stale，stale 另计
        assert episode["script_status"] == "generated"
        assert episode["status"] == "in_production"
        assert episode["item_count"] == 1
        # 响应不包含退役的总量字段
        assert "scenes_count" not in episode
        assert episode["duration_seconds"] == 8
        assert episode["storyboards"] == {"total": 1, "available": 1, "stale": 0}
        assert episode["videos"] == {"total": 1, "available": 0, "stale": 0}
        # 每集明细只走 episodes[]，项目级 status 不重复驮一份
        assert "episodes" not in resp.json()["project"]["status"]

    def test_get_project_scripts_carry_no_derived_totals(self, tmp_path, monkeypatch):
        """剧本响应不再注入条目数 / 总时长 / 出场名单：这些派生值只有项目摘要一个来源。"""
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.get("/api/v1/projects/ready")

        assert resp.status_code == 200
        script = next(iter(resp.json()["scripts"].values()))
        assert "duration_seconds" not in script
        assert "characters_in_episode" not in script
        assert "total_scenes" not in script.get("metadata", {})
        assert "estimated_duration_seconds" not in script.get("metadata", {})

    def test_list_projects_returns_style_image_field(self, tmp_path, monkeypatch):
        """列表端点需返回 style_image：否则前端无法区分"自定义风格"与"未设置"。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        # 互斥：自定义图情况下 style_template_id 应为空
        fake_pm.project_data["ready"].pop("style_template_id", None)
        fake_pm.project_data["ready"]["style"] = ""

        client = _client(monkeypatch, fake_pm)
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200
            ready = next(p for p in resp.json()["projects"] if p["name"] == "ready")
            assert ready["style_image"] == "style_reference.png"
            assert ready.get("style_template_id") is None

    def test_get_project_includes_asset_fingerprints(self, tmp_path, monkeypatch):
        """项目 API 应返回 asset_fingerprints 字段"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    # ---------------------------------------------------------------------------
