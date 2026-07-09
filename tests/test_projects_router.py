import re
import shutil
from contextlib import contextmanager
from copy import deepcopy
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
            "ad-ready": {
                "title": "Ad Ready",
                "style": "Realistic",
                "content_mode": "ad",
                "target_duration": 60,
                "brief": "",
                "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
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

    def delete_project_directory(self, name):
        shutil.rmtree(self.get_project_path(name))

    def get_project_status(self, name):
        return {"current_stage": "source_ready"}

    def create_project(self, name, content_mode="narration"):
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
        target_duration=None,
        brief=None,
        source_kind=None,
    ):
        payload = {
            "title": (title or name),
            "style": style or "",
            "content_mode": content_mode,
            "source_kind": source_kind or "novel",
            "aspect_ratio": aspect_ratio,
            "episodes": [],
        }
        if content_mode == "ad":
            # 镜像真实 ProjectManager 的 ad 形状：常量直接取自生产代码，避免第二份真相
            from lib.project_manager import ProjectManager

            payload["target_duration"] = (
                target_duration if target_duration is not None else ProjectManager.AD_DEFAULT_TARGET_DURATION
            )
            payload["brief"] = brief if brief is not None else ""
            payload["episodes"] = [dict(ProjectManager.AD_SINGLE_EPISODE)]
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
        if script_file.startswith("scripts/"):
            script_file = script_file[len("scripts/") :]
        self.scripts[(name, script_file)] = payload

    def update_project(self, name, mutate_fn):
        # 复刻真实 ProjectManager.update_project：load → mutate → save 单一事务，
        # 并返回迁移后的 project dict（调用方据此回前端，无需二次 load_project）。
        # deepcopy 后再 mutate，使异常时（save 未执行）backing store 不被原地突变污染，
        # 忠实于真实 PM「读裸 JSON、出错不写回」的语义。
        project = deepcopy(self.load_project(name))
        mutate_fn(project)
        self.save_project(name, project)
        return project

    @contextmanager
    def locked_script(self, name, script_file):
        # 复刻真实 ProjectManager.locked_script：load → yield → save，异常时跳过写回。
        # deepcopy 同上，确保 with 体内抛异常时原始存储对象保持不变。
        script = deepcopy(self.load_script(name, script_file))
        yield script
        self.save_script(name, script, script_file)

    @contextmanager
    def locked_episode_script(self, name, resolve_script_file, *, validate=True):
        # 复刻真实 ProjectManager.locked_episode_script：解析 episode→script_file →
        # 锁内读改写脚本 → 内联把 title/script_file 镜像回 project.json episodes[]
        # （仅当脚本含 episode int，与真实 _apply_episode_sync 触发条件一致）。
        script_file = resolve_script_file(self.load_project(name))
        norm = script_file[len("scripts/") :] if script_file.startswith("scripts/") else script_file
        script = deepcopy(self.load_script(name, norm))
        yield script
        self.save_script(name, script, norm)
        if isinstance(script.get("episode"), int):
            project = deepcopy(self.load_project(name))
            episodes = project.setdefault("episodes", [])
            entry = next((e for e in episodes if e.get("episode") == script["episode"]), None)
            if entry is None:
                entry = {"episode": script["episode"]}
                episodes.append(entry)
            entry["title"] = script.get("title", "")
            entry["script_file"] = f"scripts/{norm}"
            self.save_project(name, project)

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

    def test_create_persists_source_kind_and_defaults_novel(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            # 显式 screenplay 持久化于 project.json 顶层
            screenplay = client.post(
                "/api/v1/projects",
                json={"name": "scr", "title": "剧本项目", "content_mode": "drama", "source_kind": "screenplay"},
            )
            assert screenplay.status_code == 200
            assert screenplay.json()["project"]["source_kind"] == "screenplay"

            # 缺省 source_kind 落 novel
            default_novel = client.post(
                "/api/v1/projects",
                json={"name": "nov", "title": "默认项目", "content_mode": "drama"},
            )
            assert default_novel.status_code == 200
            assert default_novel.json()["project"]["source_kind"] == "novel"

            # 非法值被 Pydantic 拒（422，不是 500）
            invalid = client.post(
                "/api/v1/projects",
                json={"name": "bad", "title": "X", "content_mode": "drama", "source_kind": "screen_play"},
            )
            assert invalid.status_code == 422

    def test_source_kind_not_editable_after_create(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            rejected = client.patch("/api/v1/projects/ready", json={"source_kind": "screenplay"})
            assert rejected.status_code == 400
            # 不可变字段「出现即拒」：显式传 null 也不得静默通过
            rejected_null = client.patch("/api/v1/projects/ready", json={"source_kind": None})
            assert rejected_null.status_code == 400

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

            # 退役的 image_backend 字段在 PATCH 上也被直接拒绝
            rejected_legacy = client.patch(
                "/api/v1/projects/ready",
                json={"image_backend": "gemini-aistudio/nano-banana"},
            )
            assert rejected_legacy.status_code == 400

            get_script = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert get_script.status_code == 200

            get_script_missing = client.get("/api/v1/projects/ready/scripts/missing.json")
            assert get_script_missing.status_code == 404

    def test_create_ad_project(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            # 默认档位：不传 target_duration → 60；brief 可空；episodes 恒单条；无 default_duration
            created = client.post(
                "/api/v1/projects",
                json={"name": "ad-default", "title": "Ad", "content_mode": "ad", "aspect_ratio": "9:16"},
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
                json={"name": "ad-custom", "content_mode": "ad", "target_duration": 47, "brief": "卖点"},
            )
            assert custom.status_code == 200
            assert custom.json()["project"]["target_duration"] == 47
            assert custom.json()["project"]["brief"] == "卖点"

    def test_create_ad_project_rejects_incompatible_fields(self, tmp_path, monkeypatch):
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            # ad 不暴露 default_duration
            with_default = client.post(
                "/api/v1/projects",
                json={"name": "ad-a", "content_mode": "ad", "default_duration": 8},
            )
            assert with_default.status_code == 400

            # ad 不开放 grid
            with_grid = client.post(
                "/api/v1/projects",
                json={"name": "ad-b", "content_mode": "ad", "generation_mode": "grid"},
            )
            assert with_grid.status_code == 400

            # 非正整数 target_duration 被请求模型拒绝
            bad_duration = client.post(
                "/api/v1/projects",
                json={"name": "ad-c", "content_mode": "ad", "target_duration": 0},
            )
            assert bad_duration.status_code == 422

            # target_duration / brief 仅 ad 可用
            narration_with_td = client.post(
                "/api/v1/projects",
                json={"name": "n-a", "content_mode": "narration", "target_duration": 60},
            )
            assert narration_with_td.status_code == 400
            narration_with_brief = client.post(
                "/api/v1/projects",
                json={"name": "n-b", "content_mode": "narration", "brief": "x"},
            )
            assert narration_with_brief.status_code == 400

    def test_patch_ad_project_fields(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            # content_mode 创建后不可变：补 ad 同样 400
            rejected_mode = client.patch(
                "/api/v1/projects/ready",
                json={"content_mode": "ad"},
            )
            assert rejected_mode.status_code == 400

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

            # ad 项目不持有 default_duration / 不开放 grid / target_duration 不可清空
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": 8}).status_code == 400
            # 字段出现即拒绝:null 也不允许(否则会静默删除返回 200,与禁写契约不一致)
            assert client.patch("/api/v1/projects/ad-ready", json={"default_duration": None}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"generation_mode": "grid"}).status_code == 400
            assert client.patch("/api/v1/projects/ad-ready", json={"target_duration": None}).status_code == 400

            # 非 ad 项目不接受 target_duration / brief
            assert client.patch("/api/v1/projects/ready", json={"target_duration": 60}).status_code == 400
            assert client.patch("/api/v1/projects/ready", json={"brief": "x"}).status_code == 400

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
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "episode_1.json", "updates": {"duration_seconds": 6, "segment_break": True}},
            )
            assert patch_scene.status_code == 200
            assert patch_scene.json()["scene"]["duration_seconds"] == 6

            patch_scene_missing = client.patch(
                "/api/v1/projects/ready/script-scenes/404",
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

    def test_update_segment_writes_character_and_clue_refs(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["Forest"],
                    "props": ["Sword"],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            # 写入新引用列表
            patched = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={
                    "script_file": "narration.json",
                    "characters_in_segment": ["Bob", "Carol"],
                    "scenes": ["Castle"],
                    "props": [],
                },
            )
            assert patched.status_code == 200
            seg = patched.json()["segment"]
            assert seg["characters_in_segment"] == ["Bob", "Carol"]
            assert seg["scenes"] == ["Castle"]
            assert seg["props"] == []

            # 不传字段时不应改动现有值
            untouched = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 7},
            )
            assert untouched.status_code == 200
            seg2 = untouched.json()["segment"]
            assert seg2["duration_seconds"] == 7
            assert seg2["characters_in_segment"] == ["Bob", "Carol"]
            assert seg2["scenes"] == ["Castle"]
            assert seg2["props"] == []

    def test_update_segment_rejects_drama_script_with_residual_segments(self, tmp_path, monkeypatch):
        # drama 脚本残留 segments 键不应被当 narration 改写：须返回 400 而非放行
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "drama.json")] = {
            "content_mode": "drama",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
            "scenes": [{"scene_id": "E1S01"}],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "drama.json", "duration_seconds": 7},
            )
            assert resp.status_code == 400

    def test_update_segment_write_value_error_returns_422(self, tmp_path, monkeypatch):
        # 写盘统一入口对客户端错误（结构非法 / 集号错配 / 非法文件名）抛 ValueError，
        # router 须统一转 422 而非落到 500 兜底。
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "narration.json")] = {
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }

        @contextmanager
        def _raising_locked_script(name, script_file):
            script = fake_pm.load_script(name, script_file)
            yield script
            raise ValueError("脚本内 episode=1 与文件名 episode_10 不一致")

        monkeypatch.setattr(fake_pm, "locked_script", _raising_locked_script)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "narration.json", "duration_seconds": 7},
            )
            assert resp.status_code == 422
            assert "不一致" in resp.json()["detail"]

    def test_update_scene_supports_character_and_clue_refs(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "001",
                    "duration_seconds": 8,
                    "characters_in_scene": ["Alice"],
                    "scenes": [],
                    "props": [],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            patched = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "characters_in_scene": ["Bob"],
                        "scenes": ["Castle"],
                        "props": ["Map"],
                    },
                },
            )
            assert patched.status_code == 200
            scene = patched.json()["scene"]
            assert scene["characters_in_scene"] == ["Bob"]
            assert scene["scenes"] == ["Castle"]
            assert scene["props"] == ["Map"]

            gen_overview_bad = client.post("/api/v1/projects/bad/generate-overview")
            assert gen_overview_bad.status_code == 400

            update_overview = client.patch(
                "/api/v1/projects/ready/overview",
                json={"synopsis": "new synopsis", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            )
            assert update_overview.status_code == 200
            assert update_overview.json()["overview"]["synopsis"] == "new synopsis"

    def test_update_scene_accepts_utterances(self, tmp_path, monkeypatch):
        # drama 分镜详情编辑场景级发声序列：utterances 在白名单内，PATCH 写回并持久化
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ready", "episode_1.json")] = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "001",
                    "duration_seconds": 8,
                    "characters_in_scene": ["Alice"],
                    "scenes": [],
                    "props": [],
                    "utterances": [],
                }
            ],
        }

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        utterances = [
            {"kind": "dialogue", "speaker": "Alice", "text": "你来了。"},
            {"kind": "voiceover", "speaker": None, "text": "命运就此转向。"},
        ]

        with client:
            patched = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "episode_1.json", "updates": {"utterances": utterances}},
            )
            assert patched.status_code == 200
            assert patched.json()["scene"]["utterances"] == utterances
            # 落库持久化（不被白名单静默丢弃）
            assert fake_pm.scripts[("ready", "episode_1.json")]["scenes"][0]["utterances"] == utterances

    @staticmethod
    def _ad_script(shot_ids: list[str]) -> dict:
        return {
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": sid,
                    "section": "hook",
                    "duration_seconds": 4,
                    "voiceover_text": f"口播 {sid}",
                    "products_in_shot": [],
                }
                for sid in shot_ids
            ],
        }

    def test_update_shot_edits_voiceover_section_duration(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02"])
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {
                        "voiceover_text": "新口播",
                        "section": "demo",
                        "duration_seconds": 6,
                        "products_in_shot": ["速干杯"],
                    },
                },
            )
            assert patched.status_code == 200
            shot = patched.json()["shot"]
            assert shot["voiceover_text"] == "新口播"
            assert shot["section"] == "demo"
            assert shot["duration_seconds"] == 6
            assert shot["products_in_shot"] == ["速干杯"]
            # 持久化落到脚本存储
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"][0]
            assert saved["voiceover_text"] == "新口播"

    def test_update_shot_ignores_non_whitelisted_fields(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01"])
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "updates": {"shot_id": "E1S99", "generated_assets": {"status": "completed"}, "note": "备注"},
                },
            )
            assert patched.status_code == 200
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"][0]
            assert saved["shot_id"] == "E1S01"
            assert "generated_assets" not in saved
            assert saved["note"] == "备注"

    def test_update_shot_rejects_non_ad_script(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            rejected = client.patch(
                "/api/v1/projects/ready/script-shots/001",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert rejected.status_code == 400

    def test_update_shot_unknown_id_404(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01"])
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            missing = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S99",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert missing.status_code == 404

    def test_reorder_shots_full_permutation(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02", "E1S03"])
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            reordered = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S03", "E1S01", "E1S02"]},
            )
            assert reordered.status_code == 200
            assert [s["shot_id"] for s in reordered.json()["shots"]] == ["E1S03", "E1S01", "E1S02"]
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"]
            assert [s["shot_id"] for s in saved] == ["E1S03", "E1S01", "E1S02"]

    def test_reorder_shots_rejects_mismatched_ids(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = self._ad_script(["E1S01", "E1S02"])
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            # 数量不一致
            short = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01"]},
            )
            assert short.status_code == 400
            # 重复 ID
            dup = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01", "E1S01"]},
            )
            assert dup.status_code == 400
            # 集合不匹配
            mismatch = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["E1S01", "E1S99"]},
            )
            assert mismatch.status_code == 400
            # 原顺序未被破坏
            saved = fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"]
            assert [s["shot_id"] for s in saved] == ["E1S01", "E1S02"]

    def test_reorder_shots_rejects_non_ad_script(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            rejected = client.post(
                "/api/v1/projects/ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["001"]},
            )
            assert rejected.status_code == 400

    def test_corrupted_shots_shape_fails_loud_not_silently_wiped(self, tmp_path, monkeypatch):
        """shots 非列表 / 含非对象元素时返回 422，且不被 reorder 空排列覆盖成 []。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.scripts[("ad-ready", "episode_1.json")] = {"content_mode": "ad", "shots": "oops"}
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            # 非列表 shots：reorder 传空排列也必须 422，不得把损坏数据覆盖成空列表
            wiped = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": []},
            )
            assert wiped.status_code == 422
            assert fake_pm.scripts[("ad-ready", "episode_1.json")]["shots"] == "oops"

            # PATCH 路径同样 422，而非误导性的 404
            patched = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert patched.status_code == 422

            # 列表含非对象元素：同样 fail loud
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {"content_mode": "ad", "shots": [{"shot_id": "a"}, 42]}
            mixed = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["a"]},
            )
            assert mixed.status_code == 422

            # shot_id 缺失或非字符串：拦下避免 PATCH 误报 404 / reorder KeyError 变 500
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": "a"}, {"section": "hook"}],
            }
            missing_id = client.post(
                "/api/v1/projects/ad-ready/script-shots/reorder",
                json={"script_file": "episode_1.json", "shot_ids": ["a"]},
            )
            assert missing_id.status_code == 422

            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": 7}],
            }
            dirty_id = client.patch(
                "/api/v1/projects/ad-ready/script-shots/E1S01",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert dirty_id.status_code == 422

            # 重复 shot_id：身份键不唯一，PATCH 会静默更新首个命中项，必须拦下
            fake_pm.scripts[("ad-ready", "episode_1.json")] = {
                "content_mode": "ad",
                "shots": [{"shot_id": "a"}, {"shot_id": "a"}],
            }
            dup_id = client.patch(
                "/api/v1/projects/ad-ready/script-shots/a",
                json={"script_file": "episode_1.json", "updates": {"voiceover_text": "x"}},
            )
            assert dup_id.status_code == 422

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
                    "image_provider_t2i": "gemini-aistudio/nano-banana",
                    "text_backend_script": "gemini-aistudio/gemini-2.5",
                    "default_duration": 8,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["m-1"]
            assert data["video_backend"] == "gemini-aistudio/veo-3"
            assert data["image_provider_t2i"] == "gemini-aistudio/nano-banana"
            assert data["text_backend_script"] == "gemini-aistudio/gemini-2.5"
            assert data["default_duration"] == 8

    def test_create_project_rejects_legacy_image_backend(self, tmp_path, monkeypatch):
        """退役的 image_backend 字段在写路径被直接 400 拒绝，避免静默错配（应改用 image_provider_t2i/i2i）。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())

        with client:
            resp = client.post(
                "/api/v1/projects",
                json={"title": "旧字段项目", "name": "legacy-1", "image_backend": "gemini-aistudio/nano-banana"},
            )
            assert resp.status_code == 400
            assert "legacy-1" not in fake_pm.project_data

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
                    "video_backend": "garbage",  # 无 "/"，且不在 PROVIDER_REGISTRY
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

    def test_update_project_persists_narration_overrides(self, tmp_path, monkeypatch):
        """PATCH 旁白配音项目级覆盖：audio_backend / narration_voice / narration_speed 写入 project.json。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={
                    "audio_backend": "dashscope/qwen3-tts-flash",
                    "narration_voice": "Cherry",
                    "narration_speed": 1.2,
                },
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert data["audio_backend"] == "dashscope/qwen3-tts-flash"
            assert data["narration_voice"] == "Cherry"
            assert data["narration_speed"] == 1.2

    def test_update_project_clears_narration_overrides(self, tmp_path, monkeypatch):
        """PATCH 空值/null：旁白配音覆盖回落全局默认（从 project.json 移除）。"""
        fake_pm = _FakePM(tmp_path)
        fake_pm.project_data["ready"]["audio_backend"] = "dashscope/qwen3-tts-flash"
        fake_pm.project_data["ready"]["narration_voice"] = "Cherry"
        fake_pm.project_data["ready"]["narration_speed"] = 1.2

        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"audio_backend": None, "narration_voice": "", "narration_speed": None},
            )
            assert resp.status_code == 200
            data = fake_pm.project_data["ready"]
            assert "audio_backend" not in data
            assert "narration_voice" not in data
            assert "narration_speed" not in data

            # 纯空白音色值同样按清除处理（后端 .strip() 判空），防重构回退“空白即清除”语义
            fake_pm.project_data["ready"]["narration_voice"] = "Cherry"
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"narration_voice": "   "},
            )
            assert resp.status_code == 200
            assert "narration_voice" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_non_positive_narration_speed(self, tmp_path, monkeypatch):
        """语速 0/负数应 422，且不写回 project.json。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"narration_speed": 0})
            assert resp.status_code == 422
            assert "narration_speed" not in fake_pm.project_data["ready"]

    def test_update_project_rejects_invalid_audio_backend(self, tmp_path, monkeypatch):
        """audio_backend 非法 provider 应 400（复用 backend 格式校验）。"""
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"audio_backend": "garbage"})
            assert resp.status_code == 400

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
        """PATCH 不得将 StatusCalculator 注入的计算字段写回 project.json；title 也已移出白名单。"""
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
                            "generation_mode": "grid",  # 合法白名单字段
                            "title": "新标题",  # title 不再可经 PATCH /projects 写入（已移出白名单）
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
            assert ep1["generation_mode"] == "grid"
            # title 不可经此端点改写，保持原值（改名走 PATCH /episodes/{episode}）
            assert ep1["title"] == "原标题"
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

    def test_update_episode_title_renames_script_and_mirror(self, tmp_path, monkeypatch):
        """PATCH /episodes/{episode}：剧本顶层 title 与 project.json 镜像都反映新值，标题首尾空白被裁剪。"""
        fake_pm = _FakePM(tmp_path)
        # 剧本带 episode 字段，触发 _apply_episode_sync 镜像（与真实生成剧本一致）
        fake_pm.scripts[("ready", "episode_1.json")]["episode"] = 1

        client = _client(monkeypatch, fake_pm, _FakeCalc())
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
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            for blank in ("", "   "):
                resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": blank})
                assert resp.status_code == 422

    def test_update_episode_missing_episode_404(self, tmp_path, monkeypatch):
        """不存在的 episode → 404。"""
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/99", json={"title": "x"})
            assert resp.status_code == 404


class TestGetVideoCapabilities:
    """GET /projects/{name}/video-capabilities"""

    def _patch_resolver(self, monkeypatch, side_effect=None, return_value=None):
        """用 MagicMock 替换 ConfigResolver 类，让其 instance.video_capabilities() 返回指定行为。"""
        from unittest.mock import AsyncMock, MagicMock

        resolver_instance = MagicMock()
        if side_effect is not None:
            resolver_instance.video_capabilities = AsyncMock(side_effect=side_effect)
        else:
            resolver_instance.video_capabilities = AsyncMock(return_value=return_value)
        monkeypatch.setattr(projects, "ConfigResolver", lambda _factory: resolver_instance)
        return resolver_instance

    def test_returns_capabilities_json(self, tmp_path, monkeypatch):
        fake_caps = {
            "provider_id": "grok",
            "model": "grok-imagine-video",
            "supported_durations": list(range(1, 16)),
            "max_duration": 15,
            "max_reference_images": 7,
            "source": "registry",
            "default_duration": None,
            "content_mode": "narration",
            "generation_mode": "reference_video",
        }
        self._patch_resolver(monkeypatch, return_value=fake_caps)
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
            assert resp.status_code == 200
            assert resp.json() == fake_caps

    def test_unknown_project_returns_404(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, side_effect=FileNotFoundError("项目 'nonexistent' 不存在"))
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            resp = client.get("/api/v1/projects/nonexistent/video-capabilities")
            assert resp.status_code == 404

    def test_resolver_value_error_returns_422(self, tmp_path, monkeypatch):
        self._patch_resolver(monkeypatch, side_effect=ValueError("model not found: grok/unknown"))
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        with client:
            resp = client.get("/api/v1/projects/ready/video-capabilities")
            assert resp.status_code == 422
            assert "model not found" in resp.json()["detail"]


class TestModelSettingsApi:
    def test_create_project_with_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            resp = client.post(
                "/api/v1/projects",
                json={
                    "name": "demo-res",
                    "title": "T",
                    "model_settings": {
                        "gemini-aistudio/veo-3.1-lite-generate-preview": {"resolution": "720p"},
                    },
                },
            )
            assert resp.status_code == 200
            # 直接从 create 返回值验证 model_settings 已持久化
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"
            # 也验证 fake_pm 内部存储
            stored = fake_pm.project_data["demo-res"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"

    def test_patch_project_model_settings(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path)
        client = _client(monkeypatch, fake_pm, _FakeCalc())
        with client:
            # 先创建（利用现有 ready 项目）
            resp = client.patch(
                "/api/v1/projects/ready",
                json={"model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "1080p"}}},
            )
            assert resp.status_code == 200
            # 直接从 patch 返回值验证 model_settings
            project = resp.json()["project"]
            assert project["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"
            # 也验证 fake_pm 内部存储
            stored = fake_pm.project_data["ready"]
            assert stored["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "1080p"


def _raise(sentinel):
    """返回一个「一调用即抛 RuntimeError(sentinel)」的可调用，用于替换 try 块内最早被命中的内部函数。

    RuntimeError 不会被路由前面的 except FileNotFoundError / ValueError / HTTPException 捕获，
    必然落到 except Exception 兜底分支，从而走到「通用 500 + 不回显内部异常」路径。
    """

    def _factory(*_a, **_k):
        raise RuntimeError(sentinel)

    return _factory


class TestUnexpectedErrorsDoNotLeak:
    """未预期异常统一映射为通用 500，且响应体不得回显内部异常文本（不泄露）。

    每个端点用独一无二的哨兵串替换 try 块内最早被调用的内部函数，再断言：
    响应 500 且哨兵串不出现在响应体里。
    """

    def _body(self, resp):
        # detail 在普通端点是 json["detail"]，import 端点用 JSONResponse 同样有 detail；
        # 这里直接断言整段原始文本，覆盖 detail / errors / warnings 任意字段都不泄露。
        return resp.text

    def test_create_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_create_project"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        # _sync 里最早命中 get_project_manager()，RuntimeError 绕过 ValueError/HTTPException 分支
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects",
                json={"name": "demo", "title": "T", "content_mode": "narration"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_get_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_get_project"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_project"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"title": "X"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_delete_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_delete_project"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.delete("/api/v1/projects/remove-me")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_get_script_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_get_script"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_scene_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_scene"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "scripts/episode_1.json", "updates": {"note": "x"}},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_shot_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_shot"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/script-shots/shot-1",
                json={"script_file": "scripts/episode_1.json", "updates": {"note": "x"}},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_reorder_shots_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_reorder_shots"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/ready/script-shots/reorder",
                json={"script_file": "scripts/episode_1.json", "shot_ids": ["a", "b"]},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_segment_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_segment"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "scripts/narration.json", "duration_seconds": 5},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_episode_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_episode"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        # title 非空校验在 try 前；_sync 里最早命中 get_project_manager()
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "新标题"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_set_project_source_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_set_source"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            # content 走 multipart form；get_project_manager() 在 try 内最早被调用
            resp = client.post(
                "/api/v1/projects/ready/source",
                data={"content": "正文", "generate_overview": "false"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_generate_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_generate_overview"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/generate-overview")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_overview"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/overview", json={"synopsis": "新简介"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_create_export_token_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_token"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        # scope 合法（默认 full）；_sync 里最早命中 get_project_manager()
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/export/token")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_export_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_archive"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        # download_token 校验先放行，再让归档服务抛 RuntimeError 落到兜底
        monkeypatch.setattr(projects, "verify_download_token", lambda token, name: {"sub": "u"})
        monkeypatch.setattr(projects, "get_archive_service", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/export?download_token=tok&scope=full")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_import_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_import_archive"
        client = _client(monkeypatch, _FakePM(tmp_path), _FakeCalc())
        # 上传副本写盘成功后，_sync 调归档服务抛 RuntimeError，落到 JSONResponse(500) 兜底
        monkeypatch.setattr(projects, "get_archive_service", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/import",
                files={"file": ("demo.zip", b"PK\x03\x04not-a-real-zip", "application/zip")},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)
