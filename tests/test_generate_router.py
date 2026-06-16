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
            "products": {
                "保温杯": {
                    "product_sheet": "",
                    "brand": "",
                    "reference_images": ["products/refs/保温杯_1.jpg"],
                    "selling_points": [],
                    "description": "product",
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

    def test_video_dirty_script_fail_fast_400(self, tmp_path, monkeypatch):
        """脏脚本(分镜数组键损坏)时,/generate/video 应在路由层 4xx 失败,
        而不是 silently 走 default storyboard 路径继续 enqueue —— 后者会让用户
        先收到「提交成功」,worker 解析脚本时再确定失败,撕裂提交-执行预期。

        本测试保 default `storyboards/scene_E1S01.png` 存在(否则会被 line 192 的
        「先生成分镜图」分支挡住,无法暴露 surprise 路径)。
        """
        from lib.script_editor import ScriptEditError

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)

        def _raise_dirty(*args, **kwargs):
            raise ScriptEditError("segments 必须是列表，当前为 NoneType")

        fake_pm.load_script = _raise_dirty  # type: ignore[method-assign]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": "fail fast",
                },
            )
            assert video.status_code == 400, video.text
            # detail 走 i18n 不直接暴露内部 str(e)
            assert (
                "segments" not in video.json()["detail"]
                or "script" in video.json()["detail"].lower()
                or "kịch bản" in video.json()["detail"]
                or "损坏" in video.json()["detail"]
            )
            # 任务未入队
            assert fake_queue.calls == []

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

    def test_product_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            product = client.post(
                "/api/v1/projects/demo/generate/product/保温杯",
                json={"prompt": "不锈钢保温杯"},
            )
            assert product.status_code == 200
            body = product.json()
            assert body["success"] is True

            call = fake_queue.calls[0]
            assert call["task_type"] == "product"
            assert call["media_type"] == "image"
            assert call["resource_id"] == "保温杯"

    def test_product_enqueue_unknown_product_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/product/不存在",
                json={"prompt": "x"},
            )
            assert resp.status_code == 404
            assert fake_queue.calls == []

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


class TestUnexpectedErrorMapsTo500:
    """未预期异常 → 通用 500 且不泄露内部异常细节。

    每个端点 try 块内最早调用 get_project_manager()（storyboard/video/tts 在 _sync 内，
    character/scene/prop/product 经 _enqueue_asset_generation 的 _sync 内），将其 monkeypatch
    成抛 RuntimeError。RuntimeError 绕过前置的 FileNotFoundError/HTTPException/ScriptEditError
    处理器，落到 except Exception，断言 500 且哨兵串不出现在响应体。
    """

    def _client_with_leak(self, monkeypatch, sentinel: str) -> TestClient:
        def _boom():
            raise RuntimeError(sentinel)

        monkeypatch.setattr(generate, "get_project_manager", _boom)
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(generate.router, prefix="/api/v1")
        return TestClient(app)

    def test_storyboard_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_storyboard")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_storyboard" not in resp.text

    def test_video_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_video")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_video" not in resp.text

    def test_tts_segment_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_tts_segment")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/tts/E1S01",
                json={"script_file": "episode_1.json"},
            )
            assert resp.status_code == 500
            assert "LEAK_tts_segment" not in resp.text

    def test_tts_batch_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_tts_batch")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert resp.status_code == 500
            assert "LEAK_tts_batch" not in resp.text

    def test_character_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_character")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_character" not in resp.text

    def test_scene_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_scene")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/scene/祠堂",
                json={"prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_scene" not in resp.text

    def test_prop_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_prop")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/prop/玉佩",
                json={"prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_prop" not in resp.text

    def test_product_unexpected_error_maps_to_500(self, monkeypatch):
        client = self._client_with_leak(monkeypatch, "LEAK_product")
        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/product/保温杯",
                json={"prompt": "x"},
            )
            assert resp.status_code == 500
            assert "LEAK_product" not in resp.text


class TestAdStoryboardRegeneration:
    """ad 剧本（平铺 shots[]）沿用既有分镜生成/重生成端点——人工审核后重生成同一入口。"""

    def _ad_pm(self, project_path: Path) -> _FakePM:
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "ad"
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "section": "product_reveal",
                    "duration_seconds": 4,
                    "voiceover_text": "产品亮相",
                    "characters_in_shot": [],
                    "scenes": [],
                    "props": [],
                    "products_in_shot": ["保温杯"],
                    "generated_assets": {},
                },
            ],
        }
        return fake_pm

    def test_ad_shot_storyboard_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = self._ad_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "产品特写"},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True
            call = fake_queue.calls[0]
            assert call["task_type"] == "storyboard"
            assert call["resource_id"] == "E1S01"

    def test_ad_shot_not_found_is_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = self._ad_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E9S99",
                json={"script_file": "episode_1.json", "prompt": "产品特写"},
            )
            assert resp.status_code == 404
