import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import projects


class _FakePM:
    def __init__(self, base: Path):
        self.base = base
        self.project_data = {
            "ready": {
                "title": "Ready",
                "style": "Anime",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
                "overview": {"synopsis": "old"},
            },
            "broken": {
                "title": "Broken",
                "style": "",
                "episodes": [],
            },
        }
        self.scripts = {
            ("ready", "episode_1.json"): {
                "content_mode": "drama",
                "scenes": [{"scene_id": "001", "duration_seconds": 8}],
            },
            ("ready", "narration.json"): {
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
            },
        }
        self.created = set()
        self.generated_names = ["project-aa11bb22", "project-cc33dd44"]
        (self.base / "ready" / "storyboards").mkdir(parents=True, exist_ok=True)
        (self.base / "ready" / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        (self.base / "empty").mkdir(parents=True, exist_ok=True)
        (self.base / "remove-me").mkdir(parents=True, exist_ok=True)

    def list_projects(self):
        return ["ready", "empty", "broken"]

    def project_exists(self, name):
        return name in {"ready", "broken"}

    def load_project(self, name):
        if name == "broken":
            raise RuntimeError("broken")
        if name not in self.project_data:
            raise FileNotFoundError(name)
        return self.project_data[name]

    def get_project_path(self, name):
        path = self.base / name
        if not path.exists():
            raise FileNotFoundError(name)
        return path

    def get_project_status(self, name):
        return {"current_stage": "source_ready"}

    def create_project(self, name):
        if not name or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise ValueError("项目标识仅允许英文字母、数字和中划线")
        if name == "exists":
            raise FileExistsError(name)
        self.created.add(name)
        (self.base / name).mkdir(parents=True, exist_ok=True)

    def generate_project_name(self, title):
        return self.generated_names.pop(0)

    def create_project_metadata(
        self,
        name,
        title,
        style,
        content_mode,
        aspect_ratio="9:16",
        default_duration=None,
        style_template_id=None,
        extras=None,
    ):
        payload = {
            "title": (title or name),
            "style": style or "",
            "content_mode": content_mode,
            "aspect_ratio": aspect_ratio,
            "episodes": [],
        }
        if default_duration is not None:
            payload["default_duration"] = default_duration
        if style_template_id is not None:
            payload["style_template_id"] = style_template_id
        if extras:
            payload.update(extras)
        self.project_data[name] = payload
        return payload

    def save_project(self, name, payload):
        self.project_data[name] = payload

    def load_script(self, name, script_file):
        if script_file.startswith("scripts/"):
            script_file = script_file[len("scripts/") :]
        key = (name, script_file)
        if key not in self.scripts:
            raise FileNotFoundError(script_file)
        return self.scripts[key]

    def save_script(self, name, payload, script_file):
        self.scripts[(name, script_file)] = payload

    async def generate_overview(self, name):
        if name == "ready":
            return {"synopsis": "generated"}
        raise ValueError("source missing")


class _FakeCalc:
    def __init__(self):
        # 记录 list_projects 是否把一次性加载的 script map 传到 calculate_project_status，
        # 让针对 Task 4 的集成测试能断言两路共享预加载。
        self.last_preloaded_scripts: dict | None = None

    def calculate_project_status(self, name, project, *, preloaded_scripts=None):
        self.last_preloaded_scripts = preloaded_scripts
        return {
            "current_phase": "production",
            "phase_progress": 0.5,
            "characters": {"total": 1, "completed": 0},
            "clues": {"total": 1, "completed": 0},
            "episodes_summary": {"total": 1, "scripted": 1, "in_production": 1, "completed": 0},
        }

    def enrich_project(self, name, project):
        project = dict(project)
        project["status"] = self.calculate_project_status(name, project)
        return project

    def enrich_script(self, script):
        script = dict(script)
        script["metadata"] = {"total_scenes": 1, "estimated_duration_seconds": 8}
        return script


def _client(monkeypatch, fake_pm, fake_calc):
    monkeypatch.setattr(projects, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(projects, "get_status_calculator", lambda: fake_calc)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(projects.router, prefix="/api/v1")
    return TestClient(app)


class TestProjectsRouter:
    def test_list_and_create_and_delete(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            listed = client.get("/api/v1/projects")
            assert listed.status_code == 200
            names = [p["name"] for p in listed.json()["projects"]]
            assert names == ["ready", "empty", "broken"]
            broken = [p for p in listed.json()["projects"] if p["name"] == "broken"][0]
            assert broken["status"] == {}
            assert "error" in broken

            create_ok = client.post(
                "/api/v1/projects",
                json={"title": "New", "style": "Real", "content_mode": "narration"},
            )
            assert create_ok.status_code == 200
            assert create_ok.json()["name"] == "project-aa11bb22"
            assert create_ok.json()["project"]["title"] == "New"

            create_manual_name = client.post(
                "/api/v1/projects",
                json={"name": "manual-project", "style": "Anime", "content_mode": "narration"},
            )
            assert create_manual_name.status_code == 200
            assert create_manual_name.json()["name"] == "manual-project"
            assert create_manual_name.json()["project"]["title"] == "manual-project"

            create_exists = client.post(
                "/api/v1/projects",
                json={"name": "exists", "title": "Dup", "style": "", "content_mode": "narration"},
            )
            assert create_exists.status_code == 400

            create_invalid = client.post(
                "/api/v1/projects",
                json={"name": "bad_name", "title": "Bad", "style": "", "content_mode": "narration"},
            )
            assert create_invalid.status_code == 400

            create_missing_title = client.post(
                "/api/v1/projects",
                json={"style": "", "content_mode": "narration"},
            )
            assert create_missing_title.status_code == 400

            delete_ok = client.delete("/api/v1/projects/remove-me")
            assert delete_ok.status_code == 200

    def test_project_details_and_updates(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            detail = client.get("/api/v1/projects/ready")
            assert detail.status_code == 200
            assert "status" in detail.json()["project"]
            assert "episode_1.json" in detail.json()["scripts"]

            missing = client.get("/api/v1/projects/missing")
            assert missing.status_code == 404

            update = client.patch(
                "/api/v1/projects/ready",
                json={"title": "Updated", "style": "Noir"},
            )
            assert update.status_code == 200
            assert update.json()["project"]["title"] == "Updated"

            rejected_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "drama"},
            )
            assert rejected_mode.status_code == 400

            # aspect_ratio 现在允许修改（字符串），dict 类型将被 Pydantic 拒绝（422）
            rejected_ratio_dict = client.patch(
                "/api/v1/projects/ready",
                json={"aspect_ratio": {"videos": "16:9"}},
            )
            assert rejected_ratio_dict.status_code == 422

            # aspect_ratio 字符串更新应成功
            updated_ratio = client.patch(
                "/api/v1/projects/ready",
                json={"aspect_ratio": "16:9"},
            )
            assert updated_ratio.status_code == 200
            assert updated_ratio.json()["project"]["aspect_ratio"] == "16:9"

            get_script = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert get_script.status_code == 200

            get_script_missing = client.get("/api/v1/projects/ready/scripts/missing.json")
            assert get_script_missing.status_code == 404

    def test_scene_segment_and_overview_endpoints(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [{"scene_id": "001", "duration_seconds": 8, "image_prompt": {}, "video_prompt": {}}],
        }
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            patch_scene = client.patch(
                "/api/v1/projects/ready/scenes/001",
                json={"script_file": "episode_1.json", "updates": {"duration_seconds": 6, "segment_break": True}},
            )
            assert patch_scene.status_code == 200
            assert patch_scene.json()["scene"]["duration_seconds"] == 6

            patch_scene_missing = client.patch(
                "/api/v1/projects/ready/scenes/404",
                json={"script_file": "episode_1.json", "updates": {}},
            )
            assert patch_scene_missing.status_code == 404

            patch_segment = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 8, "segment_break": True},
            )
            assert patch_segment.status_code == 200

            not_narration = client.patch(
                "/api/v1/projects/ready/segments/001",
                json={"script_file": "episode_1.json", "duration_seconds": 8},
            )
            assert not_narration.status_code == 400

            segment_missing = client.patch(
                "/api/v1/projects/ready/segments/E9S99",
                json={"script_file": "narration.json", "duration_seconds": 8},
            )
            assert segment_missing.status_code == 404

            gen_overview_ok = client.post("/api/v1/projects/ready/generate-overview")
            assert gen_overview_ok.status_code == 200

            gen_overview_bad = client.post("/api/v1/projects/bad/generate-overview")
            assert gen_overview_bad.status_code == 400

            update_overview = client.patch(
                "/api/v1/projects/ready/overview",
                json={"synopsis": "new synopsis", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            )
            assert update_overview.status_code == 200
            assert update_overview.json()["overview"]["synopsis"] == "new synopsis"

    def test_get_project_includes_asset_fingerprints(self, tmp_path, monkeypatch):
        """项目 API 应返回 asset_fingerprints 字段"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    def test_create_project_with_style_template_id_expands_prompt(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "title": "模版项目",
                    "name": "tpl-1",
                    "style_template_id": "live_premium_drama",
                    "content_mode": "drama",
                    "aspect_ratio": "9:16",
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["tpl-1"]
            assert data["style_template_id"] == "live_premium_drama"
            assert "真人电视剧" in data["style"] or "精品短剧" in data["style"]

    def test_create_project_with_unknown_template_id_returns_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "title": "坏模版",
                    "name": "bad-1",
                    "style_template_id": "no_such",
                },
            )
            assert resp.status_code == 400

    def test_create_project_with_model_fields_persists(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "title": "模型项目",
                    "name": "m-1",
                    "video_backend": "gemini-aistudio/veo-3",
                    "image_backend": "gemini-aistudio/nano-banana",
                    "text_backend_script": "gemini-aistudio/gemini-2.5",
                    "default_duration": 8,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["m-1"]
            assert data["video_backend"] == "gemini-aistudio/veo-3"
            assert data["image_backend"] == "gemini-aistudio/nano-banana"
            assert data["text_backend_script"] == "gemini-aistudio/gemini-2.5"
            assert data["default_duration"] == 8

    def test_create_project_empty_model_fields_not_written(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "title": "空字段项目",
                    "name": "e-1",
                    "video_backend": "",
                    "image_backend": None,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["e-1"]
            assert "video_backend" not in data
            assert "image_backend" not in data

    def test_create_project_with_invalid_backend_returns_400(self, tmp_path, monkeypatch):
        """非法 backend 字符串应被校验器拒绝。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "title": "Bad Backend",
                    "name": "bad-bk",
                    "video_backend": "garbage",  # 无 "/"，且不在 _LEGACY_PROVIDER_NAMES/PROVIDER_REGISTRY
                },
            )
            assert resp.status_code == 400

    def test_update_project_with_style_template_id_expands_and_clears_image(self, tmp_path, monkeypatch):
        """PATCH style_template_id：写入 id + 展开 prompt 到 style，并清掉 style_image/description。"""
        fake_pm = _FakePM(tmp_path)
        # 预置一个带参考图的项目
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "old desc"

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "live_zhang_yimou"},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["style_template_id"] == "live_zhang_yimou"
            assert "张艺谋" in data["style"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_update_project_with_unknown_template_id_returns_400(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": "no_such_template"},
            )
            assert resp.status_code == 400

    def test_update_project_clear_style_template(self, tmp_path, monkeypatch):
        """PATCH style_template_id=null：同时清掉 id 与派生的 style 长文本。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_template_id"] = "live_premium_drama"
        fake_pm.project_data["ready"]["style"] = "画风：真人电视剧风格，精品短剧画风，大师级构图"

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": None},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_template_id" not in data
            assert data["style"] == ""

    def test_update_project_clear_style_image(self, tmp_path, monkeypatch):
        """PATCH clear_style_image=true：清掉 style_image 与 style_description。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "some desc"

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"clear_style_image": True},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_image" not in data
            assert "style_description" not in data

    def test_list_projects_shares_script_preload_with_status(self, tmp_path, monkeypatch):
        """list_projects 一次性加载 episode scripts，传给 StatusCalculator，去除 cover + status 双重 I/O。"""
        fake_pm = _FakePM(tmp_path)
        # 统计 load_script 调用次数：共享预加载后，ready 项目应只触发一次。
        orig_load_script = fake_pm.load_script
        calls: list[tuple[str, str]] = []

        def _counting_load(name, script_file):
            calls.append((name, script_file))
            return orig_load_script(name, script_file)

        fake_pm.load_script = _counting_load  # type: ignore[method-assign]

        fake_calc = _FakeCalc()
        client = _client(monkeypatch, fake_pm, fake_calc)
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200

        # ready 只有 1 集 script_file="scripts/episode_1.json"：预加载一次。
        # 若 cover + status 各自独立加载，这里会是 2 次。
        ready_calls = [c for c in calls if c[0] == "ready"]
        assert len(ready_calls) == 1, f"expected 1 shared load, got {ready_calls}"

        # 预加载 map 被传给 StatusCalculator
        assert fake_calc.last_preloaded_scripts is not None
        assert "scripts/episode_1.json" in fake_calc.last_preloaded_scripts

    def test_list_projects_returns_style_image_field(self, tmp_path, monkeypatch):
        """列表端点需返回 style_image：否则前端无法区分"自定义风格"与"未设置"。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        # 互斥：自定义图情况下 style_template_id 应为空
        fake_pm.project_data["ready"].pop("style_template_id", None)
        fake_pm.project_data["ready"]["style"] = ""

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.get("/api/v1/projects")
            assert resp.status_code == 200
            ready = [p for p in resp.json()["projects"] if p["name"] == "ready"][0]
            assert ready["style_image"] == "style_reference.png"
            assert ready.get("style_template_id") is None

    def test_update_project_clear_style_combined(self, tmp_path, monkeypatch):
        """一次性清空所有风格：style_template_id=null + clear_style_image=true。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["style_template_id"] = "live_premium_drama"
        fake_pm.project_data["ready"]["style"] = "画风：..."
        fake_pm.project_data["ready"]["style_image"] = "style_reference.png"
        fake_pm.project_data["ready"]["style_description"] = "some desc"

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"style_template_id": None, "clear_style_image": True},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "style_template_id" not in data
            assert data["style"] == ""
            assert "style_image" not in data
            assert "style_description" not in data

    # ---------------------------------------------------------------------------
    # Episodes PATCH tests (Task 12 — reference-video mode)
    # ---------------------------------------------------------------------------

    def test_patch_project_episodes_updates_generation_mode(self, tmp_path, monkeypatch):
        """PATCH /projects/{name} with episodes[] updates generation_mode for matched episode."""
        fake_pm = _FakePM(tmp_path)
        # 项目初始有 2 集，均无 generation_mode 字段
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "第一集", "script_file": "scripts/ep1.json"},
            {"episode": 2, "title": "第二集", "script_file": "scripts/ep2.json"},
        ]

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 1, "generation_mode": "reference_video"}]},
            )
            assert resp.status_code == 200
            episodes = fake_pm.project_data["ready"]["episodes"]
            ep1 = next(e for e in episodes if e["episode"] == 1)
            ep2 = next(e for e in episodes if e["episode"] == 2)
            assert ep1["generation_mode"] == "reference_video"
            # 第二集不受影响
            assert "generation_mode" not in ep2

    def test_patch_project_episodes_strips_computed_fields(self, tmp_path, monkeypatch):
        """PATCH 不得将 StatusCalculator 注入的计算字段写回 project.json。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {"episode": 1, "title": "原标题", "script_file": "scripts/ep1.json"},
        ]

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "episodes": [
                        {
                            "episode": 1,
                            "title": "新标题",
                            # 以下为 StatusCalculator 注入的计算字段，不应写入磁盘
                            "scenes_count": 999,
                            "status": "completed",
                            "storyboards": {"total": 5, "completed": 3},
                            "videos": {"total": 5, "completed": 5},
                            "script_status": "segmented",
                            "duration_seconds": 120,
                        }
                    ]
                },
            )
            assert resp.status_code == 200
            ep1 = fake_pm.project_data["ready"]["episodes"][0]
            # 合法字段应被写入
            assert ep1["title"] == "新标题"
            # 计算字段不得写入
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

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 999, "generation_mode": "grid"}]},
            )
            assert resp.status_code == 200
            episodes = fake_pm.project_data["ready"]["episodes"]
            # 集数不变
            assert len(episodes) == 2
            # 已有字段不受影响
            assert all("generation_mode" not in e for e in episodes)

    def test_patch_project_episodes_clears_generation_mode_with_null(self, tmp_path, monkeypatch):
        """PATCH 传入 generation_mode=null 时，清除集级覆盖以回退项目级继承。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/ep1.json",
                "generation_mode": "reference_video",
            },
        ]

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"episodes": [{"episode": 1, "generation_mode": None}]},
            )
            assert resp.status_code == 200
            ep1 = fake_pm.project_data["ready"]["episodes"][0]
            # 显式 null 清除覆盖，回退项目级继承
            assert "generation_mode" not in ep1
            # 其他字段保持不变
            assert ep1["title"] == "第一集"
            assert ep1["script_file"] == "scripts/ep1.json"
