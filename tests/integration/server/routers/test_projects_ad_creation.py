"""Tests for projects_ad_creation (split from test_projects_router.py)."""

from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


class TestProjectsRouter:
    def test_create_ad_project(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 默认档位：不传 target_duration → 60；brief 可空；episodes 恒单条；无 default_duration
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
            assert project["episodes"] == [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}]
            assert "default_duration" not in project

            # 数据层不硬枚举：任意正整数秒合法
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
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # ad 不暴露 default_duration
            with_default = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "ad-a", "content_mode": "ad", "default_duration": 8},
            )
            assert with_default.status_code == 400

            # ad 不开放宫格分镜
            with_grid = client.post(
                "/api/v1/projects",
                json={"name": "ad-b", "content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": True},
            )
            assert with_grid.status_code == 400

            # 非正整数 target_duration 被请求模型拒绝
            bad_duration = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "ad-c", "content_mode": "ad", "target_duration": 0},
            )
            assert bad_duration.status_code == 422

            # target_duration / brief 仅 ad 可用
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

    def test_create_requires_binary_generation_mode(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 缺失 generation_mode：必填无默认值，不被悄悄锁进某种生成模式
            missing = client.post(
                "/api/v1/projects",
                json={"name": "no-mode", "title": "X", "content_mode": "narration"},
            )
            assert missing.status_code == 422

            # 旧三值 grid 不再是合法创建值
            legacy_grid = client.post(
                "/api/v1/projects",
                json={"name": "old-grid", "title": "X", "content_mode": "narration", "generation_mode": "grid"},
            )
            assert legacy_grid.status_code == 422

            # 两种生成模式均可创建
            for mode in ("storyboard", "reference_video"):
                created = client.post(
                    "/api/v1/projects",
                    json={"name": f"m-{mode.replace('_', '-')}", "title": "X", "generation_mode": mode},
                )
                assert created.status_code == 200, created.text
                assert created.json()["project"]["generation_mode"] == mode

    def test_create_persists_grid_storyboard(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            # 缺省 false 也落盘为显式值
            default_off = client.post(
                "/api/v1/projects",
                json={"name": "grid-off", "title": "X", "generation_mode": "storyboard"},
            )
            assert default_off.status_code == 200
            assert default_off.json()["project"]["grid_storyboard"] is False

            enabled = client.post(
                "/api/v1/projects",
                json={"name": "grid-on", "title": "X", "generation_mode": "storyboard", "grid_storyboard": True},
            )
            assert enabled.status_code == 200
            assert enabled.json()["project"]["grid_storyboard"] is True

    def test_patch_toggles_grid_storyboard_but_not_route(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["generation_mode"] = "storyboard"
        client = _client(monkeypatch, fake_pm)
        with client:
            # 宫格开关创建后可随时切换
            on = client.patch("/api/v1/projects/ready", json={"grid_storyboard": True})
            assert on.status_code == 200
            assert fake_pm.project_data["ready"]["grid_storyboard"] is True

            off = client.patch("/api/v1/projects/ready", json={"grid_storyboard": False})
            assert off.status_code == 200
            assert fake_pm.project_data["ready"]["grid_storyboard"] is False

            # 生成模式创建即定：项目 PATCH 模型结构上无 generation_mode，出现即被静默丢弃、不写盘
            route = client.patch("/api/v1/projects/ready", json={"generation_mode": "reference_video"})
            assert route.status_code == 200
            assert fake_pm.project_data["ready"]["generation_mode"] == "storyboard"

    def test_patch_ad_project_fields(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm)
        with client:
            ignored_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "ad"},
            )
            assert ignored_mode.status_code == 200
            assert "content_mode" not in fake_pm.project_data["ready"]

            # ad 项目 target_duration 接受任意正整数秒
            updated = client.patch(
                "/api/v1/projects/ad-ready",
                json={"target_duration": 23},
            )
            assert updated.status_code == 200
            assert updated.json()["project"]["target_duration"] == 23

            # brief 可改可清（清为空字符串）
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

            # ad 项目不持有 default_duration / 不开放宫格分镜 / target_duration 不可清空
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": 8}).status_code == 400
            # 字段出现即拒绝:null 也不允许(否则会静默删除返回 200,与禁写契约不一致)
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": None}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"grid_storyboard": True}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"target_duration": None}).status_code == 400

            # 非 ad 项目不接受 target_duration / brief
            assert client.patch("/api/v1/projects/ready", json={"target_duration": 60}).status_code == 400
            assert client.patch("/api/v1/projects/ready", json={"brief": "x"}).status_code == 400
