from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import _ as i18n_message
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate
from tests.auth_deps import AUTH_DEPENDENCIES


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


async def _noop_bucket_precheck(project, capability):
    return None


def _client(monkeypatch, fake_pm, fake_queue):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr("lib.generation_queue.get_generation_queue", lambda: fake_queue)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)
    # 视频桶预检需要 DB（system_settings）；router 单测无 DB，能力闸行为由
    # test_config_resolver / test_validators_video_bucket 覆盖，这里只保 happy path 放行
    monkeypatch.setattr(generate, "require_video_bucket_capability", _noop_bucket_precheck)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    # raise_server_exceptions=False：500 由 app 级 Exception handler 生成响应后
    # Starlette 会 re-raise，默认配置会把它抛进测试而非返回响应
    return TestClient(app, raise_server_exceptions=False)


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

    @pytest.mark.unit
    def test_video_enqueue_bucket_capability_error_returns_400(self, tmp_path, monkeypatch):
        """i2v 桶预检失败（如默认模型缺首帧能力）→ 提交入口 400 + 修复指引，不入队。"""
        from lib.api_errors import BadRequestError

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        async def _reject(project, capability):
            assert capability == "i2v"
            raise BadRequestError("video_capability_missing_i2v", provider="dashscope", model="happyhorse-1.0-r2v")

        monkeypatch.setattr(generate, "require_video_bucket_capability", _reject)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert res.status_code == 400
        assert res.json()["detail"] == i18n_message(
            "video_capability_missing_i2v", provider="dashscope", model="happyhorse-1.0-r2v"
        )
        assert fake_queue.calls == []

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

    @pytest.mark.integration
    def test_video_generated_assets_non_dict_falls_back_to_default(self, tmp_path, monkeypatch):
        """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时按「未设置」处理、
        回退默认路径，不抛未捕获 AttributeError（脏数据非 dict 上没有 .get()）。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = ["bad"]
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 200, video.text
            assert video.json()["success"] is True

    @pytest.mark.integration
    def test_video_storyboard_image_non_string_returns_400(self, tmp_path, monkeypatch):
        """storyboard_image 是剧本 JSON 里的脏数据（非字符串）时应 400 可读失败，
        而不是让 `project_path / storyboard_rel` 抛未处理 TypeError 变成 500。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": 123}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="E1S01")
            assert fake_queue.calls == []

    @pytest.mark.integration
    def test_video_storyboard_image_absolute_path_returns_400(self, tmp_path, monkeypatch):
        """storyboard_image 是绝对路径时应 400 可读失败，不越权引用项目外文件。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "/etc/passwd"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="E1S01")
            assert fake_queue.calls == []

    @pytest.mark.integration
    def test_video_storyboard_image_path_traversal_returns_400(self, tmp_path, monkeypatch):
        """storyboard_image 含 `..` 越出项目目录时应 400 可读失败，不越权引用项目外文件。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "../../outside.png"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert video.status_code == 400, video.text
            assert video.json()["detail"] == i18n_message("invalid_storyboard_image_path", segment_id="E1S01")
            assert fake_queue.calls == []

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

    def test_video_missing_script_fail_fast_404(self, tmp_path, monkeypatch):
        """剧本文件缺失（FileNotFoundError）时,/generate/video 应 404 fail-fast,
        而不是 silently 走 default storyboard 路径继续 enqueue —— 后者会让用户
        先收到「提交成功」,worker 解析脚本时再确定失败,撕裂提交-执行预期。

        本测试保 default `storyboards/scene_E1S01.png` 存在（旧宫格项目遗留场景），
        验证即使 default 文件恰好存在，脚本缺失也不能被悄悄放过。
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        missing_script_path = project_path / "scripts" / "episode_1.json"

        def _raise_missing(*args, **kwargs):
            raise FileNotFoundError(f"剧本文件不存在: {missing_script_path}")

        fake_pm.load_script = _raise_missing  # type: ignore[method-assign]
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
            assert video.status_code == 404, video.text
            assert str(missing_script_path) not in video.text
            # 任务未入队
            assert fake_queue.calls == []

    def test_video_missing_segment_404(self, tmp_path, monkeypatch):
        """segment_id 在脚本中根本不存在时,/generate/video 应 404,而不是
        悄悄走 default storyboard 路径——即使 default 文件恰好存在(如与旧
        数据撞名),也不能把不存在的 segment 当成校验通过而入队。
        """
        project_path = _prepare_files(tmp_path)
        # default 路径恰好存在的场景：撞上一个不存在 segment 的默认文件名
        (project_path / "storyboards" / "scene_E1S99.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            video = client.post(
                "/api/v1/projects/demo/generate/video/E1S99",
                json={
                    "script_file": "episode_1.json",
                    "duration_seconds": 5,
                    "prompt": "fail fast",
                },
            )
            assert video.status_code == 404, video.text
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

    def test_character_enqueue_resolves_nfd_registered_key(self, tmp_path, monkeypatch):
        """路径参数与桶 key 形态可以不同：登记闸口落 NFC 后，仍须能按 NFD 原文发起生成，
        且入队的 resource_id 用真实落盘 key，不新造一种编码形式。"""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"] = {name_nfd: {"description": "存量 NFD 角色"}}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/generate/character/{name_nfc}",
                json={"prompt": "女主，冷静"},
            )
            assert resp.status_code == 200, resp.text
            assert fake_queue.calls[0]["resource_id"] == name_nfd

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

    每个端点最早调用 get_project_manager()（storyboard/video/tts 在 _sync 内，
    character/scene/prop/product 经 _enqueue_asset_generation 的 _sync 内），将其 monkeypatch
    成抛 RuntimeError。RuntimeError 绕过 FileNotFoundError/ScriptEditError 等专属处理器，
    落到 app 级 Exception handler，断言 500 且哨兵串不出现在响应体。
    """

    def _client_with_leak(self, monkeypatch, sentinel: str) -> TestClient:
        def _boom():
            raise RuntimeError(sentinel)

        monkeypatch.setattr(generate, "get_project_manager", _boom)
        app = FastAPI()
        register_error_handlers(app)
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return TestClient(app, raise_server_exceptions=False)

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


class TestVideoRouteGate:
    """逐条视频生成端点按项目生成路线定轴：参考路线在提交入口即拒绝。"""

    def _reference_pm(self, project_path: Path, *, storyboard_script: bool = False) -> _FakePM:
        """参考路线项目。

        ``storyboard_script`` 为真时保留 ``_FakePM`` 的分镜族默认剧本，模拟换路线前
        留下的存量剧本与分镜图产物。
        """
        fake_pm = _FakePM(project_path)
        fake_pm.project["generation_mode"] = "reference_video"
        if not storyboard_script:
            fake_pm.script = {
                "content_mode": "narration",
                "video_units": [{"unit_id": "E1U01", "prompt": "镜头1：奔跑"}],
            }
        return fake_pm

    @pytest.mark.integration
    def test_reference_route_rejected_with_route_guidance(self, tmp_path, monkeypatch):
        """参考路线：拒绝并指引走参考生视频流程，而非「先生成分镜图」。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = self._reference_pm(project_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1U01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == i18n_message("video_route_is_reference_video")
        assert fake_queue.calls == []

    @pytest.mark.integration
    def test_reference_route_rejected_even_with_leftover_storyboard(self, tmp_path, monkeypatch):
        """残留分镜图不改变判定：路线以 project.json 为唯一真相源，不按磁盘产物换路径。"""
        project_path = _prepare_files(tmp_path)
        # 分镜图产物 scene_E1S01.png 由 _prepare_files 写入
        fake_pm = self._reference_pm(project_path, storyboard_script=True)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == i18n_message("video_route_is_reference_video")
        assert fake_queue.calls == []

    @pytest.mark.integration
    def test_storyboard_route_enqueues_with_i2v_bucket(self, tmp_path, monkeypatch):
        """分镜路线：行为不变，桶预检仍按 i2v。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["generation_mode"] = "storyboard"
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        seen: list[str] = []

        async def _record(project, capability):
            seen.append(capability)

        monkeypatch.setattr(generate, "require_video_bucket_capability", _record)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/video/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
        assert resp.status_code == 200
        assert seen == ["i2v"]
        assert fake_queue.calls[0]["task_type"] == "video"


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


class TestNoServerPathLeak:
    """404/400/500 响应形状回归：detail 不得含服务器绝对路径片段。

    lib 层 FileNotFoundError 的消息携带绝对路径（如 load_script 的
    「剧本文件不存在: /abs/path」），app 级 handler 必须脱敏为通用 404 文案。
    """

    _PATH_SENTINELS = ("/Users", "/var", "/private", "\\Users")

    def _assert_no_path(self, resp) -> None:
        detail = resp.json()["detail"]
        assert isinstance(detail, str) and detail
        for sentinel in self._PATH_SENTINELS:
            assert sentinel not in detail, f"detail 泄露路径片段 {sentinel!r}: {detail}"

    def _client_project_missing(self, tmp_path, monkeypatch) -> TestClient:
        """load_project 抛携带服务器绝对路径的 FileNotFoundError（各端点第一步都会触发）。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")

        def _raise(*args, **kwargs):
            raise FileNotFoundError(f"项目元数据文件不存在: {tmp_path / 'projects' / 'demo' / 'project.json'}")

        fake_pm.load_project = _raise  # type: ignore[method-assign]
        return _client(monkeypatch, fake_pm, _FakeQueue())

    def test_file_not_found_404_hides_path_for_all_endpoints(self, tmp_path, monkeypatch):
        client = self._client_project_missing(tmp_path, monkeypatch)
        requests = [
            ("/api/v1/projects/demo/generate/storyboard/E1S01", {"script_file": "episode_1.json", "prompt": "x"}),
            ("/api/v1/projects/demo/generate/video/E1S01", {"script_file": "episode_1.json", "prompt": "x"}),
            ("/api/v1/projects/demo/generate/tts/E1S01", {"script_file": "episode_1.json"}),
            ("/api/v1/projects/demo/generate/tts", {"script_file": "episode_1.json"}),
            ("/api/v1/projects/demo/generate/character/Alice", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/scene/祠堂", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/prop/玉佩", {"prompt": "x"}),
            ("/api/v1/projects/demo/generate/product/保温杯", {"prompt": "x"}),
        ]
        with client:
            for url, payload in requests:
                resp = client.post(url, json=payload)
                assert resp.status_code == 404, f"{url}: {resp.status_code} {resp.text}"
                assert "/Users" not in resp.text, f"{url} 泄露路径: {resp.text}"
                self._assert_no_path(resp)

    def test_script_file_not_found_404_hides_path(self, tmp_path, monkeypatch):
        """load_script 的 FileNotFoundError 是原始泄漏源（消息含绝对路径）。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)

        def _raise(*args, **kwargs):
            raise FileNotFoundError(f"剧本文件不存在: {project_path / 'scripts' / 'episode_1.json'}")

        fake_pm.load_script = _raise  # type: ignore[method-assign]
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 404, resp.text
            self._assert_no_path(resp)

    def test_bad_prompt_400_shape(self, tmp_path, monkeypatch):
        """TaskSpecValidationError → app 级 handler 翻译为 400，无路径片段。"""
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S02",
                json={"script_file": "episode_1.json", "prompt": ""},
            )
            assert resp.status_code == 400, resp.text
            self._assert_no_path(resp)

    def test_unexpected_error_500_hides_path(self, tmp_path, monkeypatch):
        """未预期异常消息含路径时，500 响应仍为通用文案。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")

        def _raise(*args, **kwargs):
            raise RuntimeError(f"boom at {tmp_path / 'projects' / 'demo' / 'project.json'}")

        fake_pm.load_project = _raise  # type: ignore[method-assign]
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={"script_file": "episode_1.json", "prompt": "x"},
            )
            assert resp.status_code == 500, resp.text
            assert "boom" not in resp.text
            self._assert_no_path(resp)


class _FakeDedupeHitQueue(_FakeQueue):
    """模拟 dedupe 索引命中：返回既有任务行而非新建。"""

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": "task-existing", "deduped": True, "existing_task_id": "task-existing"}


class TestDedupedPassthrough:
    """入队响应透出队列层的 deduped 事实，供前端识别「本次未新建任务」。"""

    def test_fresh_enqueue_reports_deduped_false(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {"scene": "雨夜", "composition": {"shot_type": "Medium Shot"}},
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deduped"] is False

    def test_dedupe_hit_reports_deduped_true_with_existing_task_id(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeDedupeHitQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/storyboard/E1S01",
                json={
                    "script_file": "episode_1.json",
                    "prompt": {"scene": "雨夜", "composition": {"shot_type": "Medium Shot"}},
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["deduped"] is True
            assert body["task_id"] == "task-existing"

    def test_asset_generation_exposes_deduped(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        client = _client(monkeypatch, _FakePM(project_path), _FakeDedupeHitQueue())

        with client:
            resp = client.post(
                "/api/v1/projects/demo/generate/character/Alice",
                json={"prompt": "hero"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deduped"] is True
