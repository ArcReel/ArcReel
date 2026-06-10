import contextlib
from pathlib import Path

import pytest

from lib.video_backends.base import VideoCapabilityError
from server.services import generation_tasks
from server.services.generation_tasks import assert_duration_supported


class TestAssertDurationSupported:
    def test_supported_duration_passes(self):
        assert_duration_supported(8, [4, 6, 8])  # no raise

    def test_unsupported_duration_rejected(self):
        # 抛带稳定 code 的能力错误（与 ImageCapabilityError 对称），细节在 params。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(5, [4, 6, 8])
        assert exc.value.code == "video_duration_not_supported"
        assert exc.value.params["duration"] == 5

    def test_empty_supported_list_passes(self):
        # 能力不可解析时不更坏：空列表放行，保持既有行为不被本次改动弄坏。
        assert_duration_supported(99, [])  # no raise

    def test_integer_like_string_and_float_accepted(self):
        # 外部配置可能给字符串 / 浮点，可解析为整数秒的归一化后通过，不抛裸异常。
        assert_duration_supported("6", [4, 6, 8])  # no raise
        assert_duration_supported(6.0, [4, 6, 8])  # no raise

    def test_fractional_duration_rejected_not_truncated(self):
        # 非整数秒一律拒绝，绝不截断成「碰巧合法」的 4。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(4.5, [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
        with pytest.raises(VideoCapabilityError):
            assert_duration_supported("4.5", [4, 6, 8])

    def test_non_numeric_duration_rejected(self):
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported("abc", [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"


def _async_return(value):
    """Create an async function that always returns the given value (ignoring args)."""

    async def _inner(*args, **kwargs):
        return value

    return _inner


from lib.storyboard_sequence import (
    PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
    PREVIOUS_STORYBOARD_REFERENCE_LABEL,
)


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "content_mode": "narration",
            "style": "Anime",
            "style_description": "cinematic",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice-ref.png",
                }
            },
            "scenes": {"祠堂": {"scene_sheet": "scenes/祠堂.png"}},
            "props": {"玉佩": {"prop_sheet": "props/玉佩.png"}},
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
                    "image_prompt": "首镜头",
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "image_prompt": {
                        "scene": "在雨夜街道",
                        "composition": {
                            "shot_type": "Medium Shot",
                            "lighting": "暖光",
                            "ambiance": "薄雾",
                        },
                    },
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "segment_break": True,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "image_prompt": "切场后的镜头",
                },
            ],
        }
        self.updated_assets = []

    def load_project(self, project_name: str):
        return self.project

    def get_project_path(self, project_name: str):
        return self.project_path

    def load_script(self, project_name: str, script_file: str):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.updated_assets.append(kwargs)

    def save_project(self, project_name: str, project: dict):
        self.project = project

    def update_project(self, project_name: str, mutate_fn):
        mutate_fn(self.project)

    def project_exists(self, project_name: str) -> bool:
        return True

    def _update_asset_sheet(self, asset_type: str, project_name: str, name: str, sheet_path: str) -> dict:
        from lib.asset_types import ASSET_SPECS

        spec = ASSET_SPECS[asset_type]
        self.project.setdefault(spec.bucket_key, {}).setdefault(name, {})[spec.sheet_field] = sheet_path
        return self.project

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        self.project.setdefault("characters", {}).setdefault(name, {})["character_sheet"] = sheet_path
        return self.project


class _FakeGenerator:
    def __init__(self):
        self.image_calls = []
        self.video_calls = []
        self.versions = self

    def generate_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/image.png"), 1

    async def generate_image_async(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/image.png"), 1

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    async def generate_video_async(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-01-01T00:00:00Z"}]}


def _prepare_files(tmp_path: Path):
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "scenes").mkdir(parents=True, exist_ok=True)
    (project_path / "props").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "characters" / "refs" / "Alice-ref.png").write_bytes(b"png")
    (project_path / "scenes" / "祠堂.png").write_bytes(b"png")
    (project_path / "props" / "玉佩.png").write_bytes(b"png")
    return project_path


class TestGenerationTasks:
    def test_helper_functions(self, tmp_path):
        from lib.storyboard_sequence import get_storyboard_items

        mode_items = get_storyboard_items({"content_mode": "drama", "scenes": []})
        assert mode_items[1] == "scene_id"

        prompt = generation_tasks._normalize_storyboard_prompt("text", "Anime")
        assert prompt == "text"

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt({"scene": ""}, "Anime")

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt("", "Anime")

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt("   ", "Anime")

        video_yaml = generation_tasks._normalize_video_prompt(
            {
                "action": "行走",
                "camera_motion": "",
                "ambiance_audio": "风声",
                "dialogue": [{"speaker": "Alice", "line": "hello"}],
            }
        )
        assert "Camera_Motion" in video_yaml

        with pytest.raises(ValueError):
            generation_tasks._normalize_video_prompt({"action": ""})

        with pytest.raises(ValueError):
            generation_tasks._normalize_video_prompt("")

        with pytest.raises(ValueError):
            generation_tasks._normalize_video_prompt("   ")

    async def test_execute_task_dispatch(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        emitted_batches = []

        from lib.config.resolver import ProviderModel

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        # get_media_generator 已 mock；storyboard 路径仍直接调 _resolve_effective_image_backend
        # 推导 image_size（DB 无 provider 配置），此处 stub 掉解析——解析逻辑由 TestResolveImageBackend 覆盖。
        monkeypatch.setattr(
            generation_tasks, "_resolve_effective_image_backend", _async_return(ProviderModel("openai", "gpt-image-2"))
        )
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: emitted_batches.append(
                {
                    "project_name": project_name,
                    "changes": list(changes),
                }
            ),
        )

        storyboard_result = await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S02",
            {
                "script_file": "episode_1.json",
                "prompt": "direct prompt",
                "extra_reference_images": ["characters/Alice.png"],
            },
        )
        assert storyboard_result["resource_type"] == "storyboards"
        storyboard_refs = fake_generator.image_calls[0]["reference_images"]
        assert storyboard_refs == [
            project_path / "characters" / "Alice.png",
            project_path / "scenes" / "祠堂.png",
            project_path / "props" / "玉佩.png",
            project_path / "characters" / "Alice.png",
            {
                "image": project_path / "storyboards" / "scene_E1S01.png",
                "label": PREVIOUS_STORYBOARD_REFERENCE_LABEL,
                "description": PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
            },
        ]

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S03",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )
        assert fake_generator.image_calls[1]["reference_images"] == [
            project_path / "characters" / "Alice.png",
            project_path / "scenes" / "祠堂.png",
            project_path / "props" / "玉佩.png",
        ]

        video_result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert video_result["resource_type"] == "videos"
        assert video_result["video_uri"] == "uri"

        character_result = await generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "角色描述"},
        )
        assert character_result["resource_type"] == "characters"
        assert fake_pm.project["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"

        scene_result = await generation_tasks.execute_scene_task(
            "demo",
            "祠堂",
            {"prompt": "场景描述"},
        )
        assert scene_result["resource_type"] == "scenes"

        prop_result = await generation_tasks.execute_prop_task(
            "demo",
            "玉佩",
            {"prompt": "道具描述"},
        )
        assert prop_result["resource_type"] == "props"

        dispatch = await generation_tasks.execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "E1S02",
                "payload": {"script_file": "episode_1.json", "prompt": "text"},
            }
        )
        assert dispatch["resource_type"] == "storyboards"
        assert len(emitted_batches) == 1
        emitted_change = emitted_batches[0]["changes"][0]
        assert emitted_change["entity_type"] == "segment"
        assert emitted_change["action"] == "storyboard_ready"
        assert emitted_change["entity_id"] == "E1S02"
        assert "asset_fingerprints" in emitted_change

        with pytest.raises(ValueError):
            await generation_tasks.execute_generation_task(
                {"task_type": "unknown", "project_name": "demo", "resource_id": "x", "payload": {}}
            )

    async def test_execute_video_task_generates_thumbnail(self, monkeypatch, tmp_path):
        """视频生成后应自动提取首帧缩略图"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        thumbnail_path = project_path / "thumbnails" / "scene_E1S01.jpg"

        async def fake_extract(video_path, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"thumb")
            return out_path

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", fake_extract)
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert result["resource_type"] == "videos"
        # 验证 update_scene_asset 被调用，其中包含 video_thumbnail
        asset_types = [call["asset_type"] for call in fake_pm.updated_assets]
        assert "video_thumbnail" in asset_types
        assert thumbnail_path.exists()

    async def test_execute_video_task_rejects_unsupported_duration(self, monkeypatch, tmp_path):
        """执行层在解析出 ProviderModel 后，对越界 duration 以明确错误拒绝。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        from lib.config import resolver as resolver_mod
        from lib.config.resolver import ProviderModel

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "resolve_resolution", _async_return("720p"))
        monkeypatch.setattr(
            resolver_mod.ConfigResolver, "resolve_video_backend", _async_return(ProviderModel("ark", "seedance"))
        )
        monkeypatch.setattr(
            resolver_mod.ConfigResolver,
            "video_capabilities_for_model",
            _async_return({"supported_durations": [4, 6, 8], "default_duration": None}),
        )

        with pytest.raises(VideoCapabilityError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                    "duration_seconds": 5,
                },
            )
        assert exc.value.code == "video_duration_not_supported"
        # 越界 duration 在起跑时被拒，绝不应调用后端生成。
        assert fake_generator.video_calls == []

    async def test_execute_video_task_supported_duration_passes(self, monkeypatch, tmp_path):
        """合法 duration 通过守卫，正常进入后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        from lib.config import resolver as resolver_mod
        from lib.config.resolver import ProviderModel

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "resolve_resolution", _async_return("720p"))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        monkeypatch.setattr(
            resolver_mod.ConfigResolver, "resolve_video_backend", _async_return(ProviderModel("ark", "seedance"))
        )
        monkeypatch.setattr(
            resolver_mod.ConfigResolver,
            "video_capabilities_for_model",
            _async_return({"supported_durations": [4, 6, 8], "default_duration": None}),
        )

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    async def test_execute_video_task_default_duration_from_caps(self, monkeypatch, tmp_path):
        """无显式 duration 时，默认值由 caps 收口（取 supported_durations[0]），且必然合法。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        from lib.config import resolver as resolver_mod
        from lib.config.resolver import ProviderModel

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "resolve_resolution", _async_return("720p"))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        monkeypatch.setattr(
            resolver_mod.ConfigResolver, "resolve_video_backend", _async_return(ProviderModel("ark", "seedance"))
        )
        monkeypatch.setattr(
            resolver_mod.ConfigResolver,
            "video_capabilities_for_model",
            _async_return({"supported_durations": [6, 10], "default_duration": None}),
        )
        # 项目默认 duration 也置空，强制走 caps 默认。
        fake_pm.project.pop("default_duration", None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 6

    async def test_caps_failure_preserves_resolved_provider(self, monkeypatch, tmp_path):
        """caps 解析失败不得丢弃已解析的 provider/model：resolve_resolution 仍按真实 provider。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        seen_resolution_args: list[tuple] = []

        async def fake_resolution(project, provider, model):
            seen_resolution_args.append((provider, model))
            return "720p"

        from lib.config import resolver as resolver_mod
        from lib.config.resolver import ProviderModel

        async def boom_caps(self, provider_id, model_id, project=None):
            raise ValueError("supported_durations is empty for ark/seedance")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "resolve_resolution", fake_resolution)
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        monkeypatch.setattr(
            resolver_mod.ConfigResolver, "resolve_video_backend", _async_return(ProviderModel("ark", "seedance"))
        )
        monkeypatch.setattr(resolver_mod.ConfigResolver, "video_capabilities_for_model", boom_caps)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 9,
            },
        )
        assert result["resource_type"] == "videos"
        # caps 失败时 supported_durations 留空 → 守卫放行（不更坏），但 provider 不被改写。
        assert seen_resolution_args == [("ark", "seedance")]

    async def test_caps_resolved_for_payload_provider_model(self, monkeypatch, tmp_path):
        """caps 按已解析（含 payload 覆盖）的 provider/model 取，而非按 project 二次解析。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        seen_caps_args: list[tuple] = []

        from lib.config import resolver as resolver_mod
        from lib.config.resolver import ProviderModel

        async def capture_caps(self, provider_id, model_id, project=None):
            seen_caps_args.append((provider_id, model_id))
            return {"supported_durations": [4, 6, 8], "default_duration": None}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(fake_generator))
        monkeypatch.setattr(generation_tasks, "resolve_resolution", _async_return("720p"))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        # 模拟历史任务 payload 覆盖：resolve_video_backend 解析出 ark/seedance。
        monkeypatch.setattr(
            resolver_mod.ConfigResolver, "resolve_video_backend", _async_return(ProviderModel("ark", "seedance"))
        )
        monkeypatch.setattr(resolver_mod.ConfigResolver, "video_capabilities_for_model", capture_caps)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )
        # caps 用解析后的 model 而非 project 默认取，二者一致。
        assert seen_caps_args == [("ark", "seedance")]

    async def test_get_media_generator_skips_image_backend_for_video_tasks(self, monkeypatch, tmp_path):
        """视频任务只应初始化视频 backend，避免图片配置缺失导致提前失败。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_video_backend = object()

        class _FakeResolver:
            def __init__(self, session_factory):
                self.session_factory = session_factory

            @contextlib.asynccontextmanager
            async def session(self):
                yield self

            async def default_image_backend(self):
                raise AssertionError("video tasks should not resolve image backend")

        async def _fake_resolve_video_backend(project_name, resolver, payload):
            assert project_name == "demo"
            # 4 元组：(video_backend, video_backend_type, video_model, provider_id)
            return fake_video_backend, "unused", "video-model", "gemini-aistudio"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr("lib.config.resolver.ConfigResolver", _FakeResolver)
        monkeypatch.setattr(
            generation_tasks,
            "_resolve_video_backend",
            _fake_resolve_video_backend,
        )

        generator = await generation_tasks.get_media_generator(
            "demo",
            payload={"prompt": "video"},
            require_image_backend=False,
        )

        assert generator._image_backend is None
        assert generator._video_backend is fake_video_backend
        # 纯视频任务：video provider_id 透传到咽喉层，image provider_id 为 None（无作用域报错）
        assert generator._video_provider_id == "gemini-aistudio"
        assert generator._image_provider_id is None

    def test_emit_success_batch_includes_fingerprints(self, monkeypatch, tmp_path):
        """生成成功事件应携带 asset_fingerprints"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        (project_path / "storyboards").mkdir()
        sb = project_path / "storyboards" / "scene_E1S01.png"
        sb.write_bytes(b"img")

        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert "asset_fingerprints" in change
        assert "storyboards/scene_E1S01.png" in change["asset_fingerprints"]
        assert isinstance(change["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    def test_grid_fingerprints_include_split_cells(self, monkeypatch, tmp_path):
        """宫格指纹应包含切割覆写的 canonical 分镜图（cache-bust），但拒绝越出项目目录的路径"""
        from lib.grid.models import FrameCell, GridGeneration
        from lib.grid_manager import GridManager

        project_path = tmp_path / "demo"
        (project_path / "storyboards").mkdir(parents=True)
        (project_path / "grids").mkdir()
        (project_path / "grids" / "grid_1.png").write_bytes(b"grid")
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"img2")
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"secret")

        grid = GridGeneration(
            id="grid_1",
            episode=1,
            script_file="ep01.json",
            scene_ids=["E1S01"],
            grid_image_path="grids/grid_1.png",
            rows=2,
            cols=2,
            cell_count=4,
            frame_chain=[
                FrameCell(
                    index=0,
                    row=0,
                    col=0,
                    frame_type="first",
                    next_scene_id="E1S01",
                    image_path="storyboards/scene_E1S01.png",
                ),
                FrameCell(
                    index=1,
                    row=0,
                    col=1,
                    frame_type="transition",
                    # 项目内的绝对路径：允许纳入，但指纹 key 必须归一为相对路径
                    image_path=str(project_path / "storyboards" / "scene_E1S02.png"),
                ),
                FrameCell(index=2, row=1, col=0, frame_type="transition", image_path="../outside.png"),
                FrameCell(index=3, row=1, col=1, frame_type="transition", image_path=str(outside)),
            ],
            status="completed",
            prompt=None,
            provider="p",
            model="m",
            grid_size="2K",
            created_at="2026-01-01T00:00:00Z",
        )
        GridManager(project_path).save(grid)

        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        fps = generation_tasks.compute_affected_fingerprints("demo", "grid", "grid_1")

        assert "grids/grid_1.png" in fps
        assert "storyboards/scene_E1S01.png" in fps
        assert "storyboards/scene_E1S02.png" in fps
        assert all("outside" not in key for key in fps)
        assert all(not key.startswith("/") for key in fps)

    async def test_execute_task_validation_errors(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_media_generator", _async_return(_FakeGenerator()))

        with pytest.raises(ValueError):
            await generation_tasks.execute_storyboard_task("demo", "E1S01", {"prompt": "x"})

        with pytest.raises(ValueError):
            await generation_tasks.execute_video_task("demo", "E1S01", {"script_file": "episode_1.json"})

        (project_path / "storyboards" / "scene_E1S01.png").unlink()
        with pytest.raises(ValueError):
            await generation_tasks.execute_video_task("demo", "E1S01", {"script_file": "episode_1.json", "prompt": "x"})

        with pytest.raises(ValueError):
            await generation_tasks.execute_character_task("demo", "Alice", {"prompt": ""})

        with pytest.raises(ValueError):
            await generation_tasks.execute_scene_task("demo", "祠堂", {"prompt": ""})

        with pytest.raises(ValueError):
            await generation_tasks.execute_prop_task("demo", "玉佩", {"prompt": ""})


from server.services.generation_tasks import _resolve_effective_image_backend


@pytest.mark.asyncio
async def test_resolve_picks_t2i_from_payload_when_no_refs():
    payload = {
        "image_provider_t2i": "openai/gen-1",
        "image_provider_i2i": "openai/edit-1",
    }
    resolved = await _resolve_effective_image_backend({}, payload, needs_i2i=False)
    assert (resolved.provider_id, resolved.model_id) == ("openai", "gen-1")


@pytest.mark.asyncio
async def test_resolve_picks_i2i_from_payload_when_refs():
    payload = {
        "image_provider_t2i": "openai/gen-1",
        "image_provider_i2i": "openai/edit-1",
    }
    resolved = await _resolve_effective_image_backend({}, payload, needs_i2i=True)
    assert (resolved.provider_id, resolved.model_id) == ("openai", "edit-1")


@pytest.mark.asyncio
async def test_resolve_falls_back_to_legacy_payload_image_provider():
    """payload 仅有旧 image_provider/image_model（历史任务）时两槽都用此值。"""
    payload = {"image_provider": "openai", "image_model": "legacy"}
    t2i = await _resolve_effective_image_backend({}, payload, needs_i2i=False)
    i2i = await _resolve_effective_image_backend({}, payload, needs_i2i=True)
    assert (t2i.provider_id, t2i.model_id) == ("openai", "legacy")
    assert (i2i.provider_id, i2i.model_id) == ("openai", "legacy")


@pytest.mark.asyncio
async def test_resolve_reads_project_split_fields():
    project = {
        "image_provider_t2i": "openai/proj-gen",
        "image_provider_i2i": "openai/proj-edit",
    }
    t2i = await _resolve_effective_image_backend(project, {}, needs_i2i=False)
    i2i = await _resolve_effective_image_backend(project, {}, needs_i2i=True)
    assert (t2i.provider_id, t2i.model_id) == ("openai", "proj-gen")
    assert (i2i.provider_id, i2i.model_id) == ("openai", "proj-edit")


class TestGetAspectRatio:
    def test_reads_top_level_aspect_ratio(self):
        project = {"aspect_ratio": "16:9", "content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "storyboards") == "16:9"

    def test_fallback_to_content_mode_narration(self):
        project = {"content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "9:16"

    def test_fallback_to_content_mode_drama(self):
        project = {"content_mode": "drama"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"

    def test_characters_always_16_9(self):
        # 角色采用四视图横版（issue #353）
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "characters") == "16:9"

    def test_scenes_and_props_always_16_9(self):
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "scenes") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "props") == "16:9"


class TestFillSimpleProviderKwargs:
    """_fill_simple_provider_kwargs 应优先用户 base_url，缺省回落 ProviderMeta.default_base_url。"""

    class _FakeResolver:
        def __init__(self, config: dict):
            self._config = config

        async def provider_config(self, name: str) -> dict:
            return self._config

    async def test_uses_default_base_url_when_user_unset(self):
        resolver = self._FakeResolver({"api_key": "sk-test"})
        kwargs: dict = {}
        await generation_tasks._fill_simple_provider_kwargs("ark", resolver, kwargs, "doubao-seed-2-0-pro-260215")
        assert kwargs["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"

    async def test_user_base_url_wins(self):
        resolver = self._FakeResolver({"api_key": "sk-test", "base_url": "https://custom.example.com/v3"})
        kwargs: dict = {}
        await generation_tasks._fill_simple_provider_kwargs("ark", resolver, kwargs, "model-x")
        assert kwargs["base_url"] == "https://custom.example.com/v3"

    async def test_no_default_no_user_no_kwarg(self):
        resolver = self._FakeResolver({"api_key": "sk-test"})
        kwargs: dict = {}
        await generation_tasks._fill_simple_provider_kwargs("grok", resolver, kwargs, "m")
        assert "base_url" not in kwargs
