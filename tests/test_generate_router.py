from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import generate


class _FakeQueue:
    """Mock GenerationQueue that records enqueue calls."""

    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "style": "Anime",
            "style_description": "cinematic",
            "content_mode": "narration",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice_ref.png",
                    "description": "hero",
                }
            },
            "scenes": {
                "祠堂": {
                    "scene_sheet": "scenes/祠堂.png",
                    "description": "scene",
                }
            },
            "props": {
                "玉佩": {
                    "prop_sheet": "props/玉佩.png",
                    "description": "prop",
                }
            },
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": [],
                    "scenes": [],
                    "props": [],
                    "generated_assets": {},
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "generated_assets": {},
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "segment_break": True,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "generated_assets": {},
                },
            ],
        }

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "scenes").mkdir(parents=True, exist_ok=True)
    (project_path / "props").mkdir(parents=True, exist_ok=True)

    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "scenes" / "祠堂.png").write_bytes(b"png")
    (project_path / "props" / "玉佩.png").write_bytes(b"png")
    return project_path


def _client(monkeypatch, fake_pm, fake_queue):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("lib.generation_queue.get_generation_queue", lambda: fake_queue)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1")
    return TestClient(app)


class TestGenerateRouter:
    def test_storyboard_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            sb = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {
                        "scene": "雨夜",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                },
            )
            assert sb.status_code == 200
            body = sb.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"
            assert "message" in body

            # Verify enqueue was called correctly
            call = fake_queue.calls[0]
            assert call["project_name"] == "demo"
            assert call["task_type"] == "storyboard"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "E1S02"
            assert call["source"] == "webui"

    def test_video_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": {
                        "action": "奔跑",
                        "camera_motion": "Static",
                        "ambiance_audio": "雨声",
                        "dialogue": [{"speaker": "Alice", "line": "快走"}],
                    },
                },
            )
            assert video.status_code == 200
            body = video.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "video"
            assert call["media_type"] == "video"
            assert call["payload"]["duration_seconds"] == 5

    def test_video_enqueue_grid_mode_uses_first_frame(self, tmp_path, monkeypatch):
        """宫格模式：storyboard 写入 _first.png 并记录于 generated_assets，路由应识别该路径。"""
        project_path = _prepare_files(tmp_path)
        # 只保留宫格模式产物，删除默认路径
        (project_path / "storyboards" / "scene_E1S01.png").unlink()
        (project_path / "storyboards" / "scene_E1S02_first.png").write_bytes(b"png")

        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02_first.png"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S02",
                json={
                    "script_file": "episode_1.json",
                    "prompt": "宫格切片后的动作",
                },
            )
            assert video.status_code == 200, video.text
            assert video.json()["success"] is True

    def test_character_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            character = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "女主，冷静"},
            )
            assert character.status_code == 200
            body = character.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "character"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "Alice"

    def test_scene_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            scene = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
                json={"prompt": "阴森古朴"},
            )
            assert scene.status_code == 200
            body = scene.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "scene"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "祠堂"

    def test_prop_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            prop = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
                json={"prompt": "古朴玉佩"},
            )
            assert prop.status_code == 200
            body = prop.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"

            call = fake_queue.calls[0]
            assert call["task_type"] == "prop"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "玉佩"

    def test_error_paths(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            # Bad storyboard prompt (structured but missing scene)
            bad_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": {"composition": {}}},
            )
            assert bad_prompt.status_code == 400

            # Nonexistent segment
            not_found = client.post(
                "/api/v1/projects/demo/generate/storyboard/MISSING",
                json={"script_file": "episode_1.json", "prompt": "test"},
            )
            assert not_found.status_code == 404

            # Video without storyboard
            (project_path / "storyboards" / "scene_E1S01.png").unlink()
            no_storyboard = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "text"},
            )
            assert no_storyboard.status_code == 400

            # Bad video prompt
            bad_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": {"action": ""}},
            )
            assert bad_video_prompt.status_code in (400, 500)

            # Empty string prompt for storyboard route (segment exists, prompt is empty str)
            empty_storyboard_prompt = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": ""},
            )
            assert empty_storyboard_prompt.status_code == 400

            # Whitespace-only string prompt for video route — ensure storyboard exists first
            # so we hit the prompt check, not the missing-storyboard check
            (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
            empty_video_prompt = client.post(
                "/api/v1/projects/demo/generate/video/E1S02",
                json={"script_file": "episode_1.json", "prompt": "   "},
            )
            assert empty_video_prompt.status_code == 400

            # Missing character
            fake_pm.project["characters"] = {}
            missing_char = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "x"},
            )
            assert missing_char.status_code == 404

            # Missing scene
            fake_pm.project["scenes"] = {}
            missing_scene = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
                json={"prompt": "x"},
            )
            assert missing_scene.status_code == 404

            # Missing prop
            fake_pm.project["props"] = {}
            missing_prop = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
                json={"prompt": "x"},
            )
            assert missing_prop.status_code == 404


# ==================== _snapshot_image_backend 单元测试 ====================
"""_snapshot_image_backend writes T2I/I2I split payload keys from project."""

from unittest.mock import patch


def _make_pm_loader(project: dict):
    """构造一个 mock get_project_manager 返回值。"""

    class _PM:
        def load_project(self, _name):
            return project

    return _PM()


def test_snapshot_writes_split_keys_from_project_split_fields():
    from server.routers.generate import _snapshot_image_backend

    project = {
        "image_provider_t2i": "openai/gen-1",
        "image_provider_i2i": "openai/edit-1",
    }
    with patch("server.routers.generate.get_project_manager", return_value=_make_pm_loader(project)):
        snap = _snapshot_image_backend("demo")
    assert snap == {
        "image_provider_t2i": "openai/gen-1",
        "image_provider_i2i": "openai/edit-1",
    }


def test_snapshot_falls_back_to_legacy_image_backend():
    """旧 project 只有 image_backend → 两槽都用此值。"""
    from server.routers.generate import _snapshot_image_backend

    project = {"image_backend": "openai/legacy-model"}
    with patch("server.routers.generate.get_project_manager", return_value=_make_pm_loader(project)):
        snap = _snapshot_image_backend("demo")
    assert snap == {
        "image_provider_t2i": "openai/legacy-model",
        "image_provider_i2i": "openai/legacy-model",
    }


def test_snapshot_returns_empty_when_no_project_config():
    """无项目级配置 → 返回空 dict（让 resolver 用全局默认）。"""
    from server.routers.generate import _snapshot_image_backend

    project = {}
    with patch("server.routers.generate.get_project_manager", return_value=_make_pm_loader(project)):
        snap = _snapshot_image_backend("demo")
    assert snap == {}


def test_snapshot_partial_split_fields():
    """只设了 t2i 槽时，i2i 槽用旧 image_backend 兜底；都没有则不写 i2i。"""
    from server.routers.generate import _snapshot_image_backend

    project_only_t2i = {
        "image_provider_t2i": "openai/gen-1",
        "image_backend": "openai/legacy",
    }
    with patch("server.routers.generate.get_project_manager", return_value=_make_pm_loader(project_only_t2i)):
        snap = _snapshot_image_backend("demo")
    assert snap.get("image_provider_t2i") == "openai/gen-1"
    assert snap.get("image_provider_i2i") == "openai/legacy"
