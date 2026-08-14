import asyncio
import copy
import json
import re
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.config.resolver import ProviderModel
from lib.generation_queue import CompensableGenerationResult
from lib.narration_delivery import (
    USE_TTS,
    NarratedVideoDurationBlockedError,
    NarrationDeliveryPreparation,
    NarrationTtsStatus,
    TtsSynthesisSettings,
    prepare_narrated_video_duration,
)
from lib.prompt_builders import append_image_negative_tail
from lib.prompt_utils import image_prompt_to_yaml
from lib.video_backends.base import VideoCapabilities, VideoCapabilityError
from lib.video_frame_slots import gate_video_request
from lib.video_visual_provenance import build_storyboard_video_visual_basis
from server.services import generation_tasks
from server.services.generation_context import AudioLaneResult, GenerationContext, ImageLaneResult, VideoLaneResult
from server.services.generation_tasks import assert_duration_supported


class TestAssertDurationSupported:
    @pytest.mark.unit
    def test_supported_duration_passes(self):
        assert_duration_supported(8, [4, 6, 8])  # no raise

    @pytest.mark.unit
    def test_unsupported_duration_rejected(self):
        # 抛带稳定 code 的能力错误（与 ImageCapabilityError 对称），细节在 params。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(5, [4, 6, 8])
        assert exc.value.code == "video_duration_not_supported"
        assert exc.value.params["duration"] == 5

    @pytest.mark.unit
    def test_empty_supported_list_passes(self):
        # 能力不可解析时空列表放行，保持宽容行为。
        assert_duration_supported(99, [])  # no raise

    @pytest.mark.unit
    def test_integer_like_string_and_float_accepted(self):
        # 外部配置可能给字符串 / 浮点，可解析为整数秒的归一化后通过，不抛裸异常。
        assert_duration_supported("6", [4, 6, 8])  # no raise
        assert_duration_supported(6.0, [4, 6, 8])  # no raise

    @pytest.mark.unit
    def test_fractional_duration_rejected_not_truncated(self):
        # 非整数秒一律拒绝，绝不截断成「碰巧合法」的 4。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(4.5, [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
        with pytest.raises(VideoCapabilityError):
            assert_duration_supported("4.5", [4, 6, 8])

    @pytest.mark.unit
    def test_non_numeric_duration_rejected(self):
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported("abc", [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"


class TestCollectSheetReferences:
    @pytest.mark.unit
    def test_max_count_truncates_refs_from_single_item(self, tmp_path):
        # 单个 item 内的角色数就超过 max_count 时，_group 内层三段循环不会在
        # item 中途触发外层 break，需要在返回前再做一次显式切片。
        from server.services.generation_tasks import _collect_sheet_references

        characters = {}
        char_names = [f"char{i}" for i in range(8)]
        for name in char_names:
            sheet_path = tmp_path / f"{name}.png"
            sheet_path.write_bytes(b"fake-image")
            characters[name] = {"character_sheet": f"{name}.png"}

        project = {"characters": characters, "scenes": {}, "props": {}}
        items = [{"characters_in_segment": char_names}]

        refs, seen = _collect_sheet_references(
            project,
            tmp_path,
            items,
            char_field="characters_in_segment",
            scene_field="scenes",
            prop_field="props",
            max_count=6,
        )

        assert len(refs) == 6
        assert len(seen) == 8


def _async_return(value):
    """Create an async function that always returns the given value (ignoring args)."""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def _fake_resolve_ctx(
    generator,
    *,
    image_provider=("openai", "gpt-image-2"),
    image_resolution=None,
    video_provider=("ark", "seedance"),
    video_backend_model=None,
    video_resolution="720p",
    supported_durations=(4, 6, 8),
    voice_consistency="soft",
    requested_generate_audio=True,
    seen_lane_requests=None,
):
    """lane 感知的假 resolve_generation_context：按调用方声明的 lane 拼装 frozen dataclass 产物。

    ``seen_lane_requests`` 传入 list 时记录每次调用声明的 lane 请求，供断言任务只声明
    自己用到的 lane。
    """

    async def _resolve(
        project_name, payload, *, project, user_id="default", episode=None, image=None, video=None, audio=None
    ):
        if seen_lane_requests is not None:
            seen_lane_requests.append({"image": image, "video": video, "audio": audio})
        image_lane = None
        if image is not None:
            provider, model = image_provider
            image_lane = ImageLaneResult(
                provider_model=ProviderModel(provider, model),
                backend_name=provider,
                backend_model=model,
                resolution=image_resolution,
            )
        video_lane = None
        if video is not None:
            provider, model = video_provider
            backend_model = video_backend_model or model
            video_lane = VideoLaneResult(
                provider_model=ProviderModel(provider, model),
                backend_name=provider,
                backend_model=backend_model,
                resolution=video_resolution,
                resolution_or_fallback=video_resolution or "720p",
                supported_durations=tuple(supported_durations),
                max_duration=None,
                max_reference_images=None,
                voice_consistency=voice_consistency,
                requested_generate_audio=requested_generate_audio,
            )
        audio_lane = None
        if audio is not None:
            audio_lane = AudioLaneResult(
                provider_model=ProviderModel("dashscope", "configured-tts"),
                backend_name="dashscope",
                backend_model="actual-tts",
                narration_voice="Cherry",
                narration_speed=1.1,
                voices=(),
            )
        return GenerationContext(
            generator=generator,
            image_lane=image_lane,
            video_lane=video_lane,
            audio_lane=audio_lane,
        )

    return _resolve


from lib.storyboard_sequence import (
    PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
    PREVIOUS_STORYBOARD_REFERENCE_LABEL,
)


@pytest.mark.unit
def test_storyboard_visual_basis_tracks_effective_request_context(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.png"
    storyboard.write_bytes(b"png")
    common = {
        "prompt": {"action": "跑"},
        "storyboard_image": storyboard,
        "end_frame_image": None,
        "content_mode": "narration",
        "utterances": None,
        "voice_characters": None,
        "provider_id": "ark",
        "model_id": "seedance",
        "resolution": "720p",
        "seed": 7,
        "requested_generate_audio": True,
        "has_utterances": False,
    }

    portrait = build_storyboard_video_visual_basis(**common, aspect_ratio="9:16")
    landscape = build_storyboard_video_visual_basis(**common, aspect_ratio="16:9")
    normalized_equivalent = build_storyboard_video_visual_basis(
        **{
            **common,
            "prompt": {
                "action": " 跑 ",
                "camera_motion": "Static",
                "ambiance_audio": "",
                "dialogue": [],
                "ignored": "not sent to the provider",
            },
        },
        aspect_ratio="9:16",
    )

    assert portrait.digest != landscape.digest
    assert portrait.digest == normalized_equivalent.digest

    other_provider = build_storyboard_video_visual_basis(
        **{**common, "provider_id": "openai"},
        aspect_ratio="9:16",
    )
    other_model = build_storyboard_video_visual_basis(
        **{**common, "model_id": "seedance-pro"},
        aspect_ratio="9:16",
    )
    other_resolution = build_storyboard_video_visual_basis(
        **{**common, "resolution": "1080p"},
        aspect_ratio="9:16",
    )
    other_seed = build_storyboard_video_visual_basis(
        **{**common, "seed": 8},
        aspect_ratio="9:16",
    )
    other_audio_request = build_storyboard_video_visual_basis(
        **{**common, "requested_generate_audio": False},
        aspect_ratio="9:16",
    )

    assert portrait.digest != other_provider.digest
    assert portrait.digest != other_model.digest
    assert portrait.digest != other_resolution.digest
    assert portrait.digest != other_seed.digest
    assert portrait.digest != other_audio_request.digest


@pytest.mark.unit
def test_storyboard_visual_basis_tracks_only_referenced_character_voices(tmp_path: Path) -> None:
    storyboard = tmp_path / "storyboard.png"
    storyboard.write_bytes(b"png")
    common = {
        "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
        "storyboard_image": storyboard,
        "end_frame_image": None,
        "aspect_ratio": "9:16",
        "provider_id": "ark",
        "model_id": "seedance",
        "resolution": "720p",
        "seed": None,
        "requested_generate_audio": True,
        "content_mode": "drama",
        "utterances": [{"kind": "dialogue", "speaker": "Alice", "text": "Run"}],
        "has_utterances": True,
    }
    characters = {
        "Alice": {"voice_style": "bright"},
        "Bob": {"voice_style": "deep"},
    }

    original = build_storyboard_video_visual_basis(**common, voice_characters=characters)
    unrelated_change = build_storyboard_video_visual_basis(
        **common,
        voice_characters={**characters, "Bob": {"voice_style": "soft"}},
    )
    used_voice_change = build_storyboard_video_visual_basis(
        **common,
        voice_characters={**characters, "Alice": {"voice_style": "soft"}},
    )

    assert original.digest == unrelated_change.digest
    assert original.digest != used_voice_change.digest


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "content_mode": "narration",
            "style": "Anime",
            "style_description": "cinematic",
            "characters": {
                "Alice": {
                    "description": "hero",
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice-ref.png",
                }
            },
            "scenes": {"祠堂": {"description": "temple", "scene_sheet": "scenes/祠堂.png"}},
            "props": {"玉佩": {"description": "jade", "prop_sheet": "props/玉佩.png"}},
            "products": {
                "保温杯": {
                    "description": "不锈钢保温杯",
                    "product_sheet": "",
                    "brand": "",
                    "reference_images": ["products/refs/保温杯_1.jpg", "products/refs/missing.jpg"],
                    "selling_points": [],
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
        self.project_path.mkdir(parents=True, exist_ok=True)
        (self.project_path / "project.json").write_text('{"schema_version":7}', encoding="utf-8")
        scripts_dir = self.project_path / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(self.script, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_project(self, project_name: str):
        return self.project

    def get_project_path(self, project_name: str):
        return self.project_path

    def load_script(self, project_name: str, script_file: str):
        return self.script

    def update_scene_asset(self, **kwargs):
        on_commit = kwargs.pop("on_commit", None)
        self.updated_assets.append(kwargs)
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / kwargs["script_filename"])

    @contextmanager
    def locked_script(self, project_name, script_filename, *, validate=True, on_commit=None):
        yield self.script
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / script_filename)

    def _set_scene_asset_in_script(self, script, scene_id, asset_type, asset_path):
        items, id_field, _kind = generation_tasks.resolve_items(script)
        item = next(candidate for candidate in items if str(candidate.get(id_field)) == str(scene_id))
        assets = item.setdefault("generated_assets", {})
        assets[asset_type] = asset_path
        assets["status"] = "storyboard_ready" if asset_type == "storyboard_image" else "completed"
        return item

    def save_project(self, project_name: str, project: dict):
        self.project = project

    def update_project(self, project_name: str, mutate_fn, *, on_commit=None):
        mutate_fn(self.project)
        if on_commit is not None:
            on_commit(self.project_path / "project.json")

    def project_exists(self, project_name: str) -> bool:
        return True

    def _update_asset_sheet(
        self, asset_type: str, project_name: str, name: str, sheet_path: str, *, on_commit=None
    ) -> dict:
        from lib.asset_types import ASSET_SPECS

        spec = ASSET_SPECS[asset_type]
        self.project.setdefault(spec.bucket_key, {}).setdefault(name, {})[spec.sheet_field] = sheet_path
        if on_commit is not None:
            on_commit(self.project_path / "project.json")
        return self.project

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        self.project.setdefault("characters", {}).setdefault(name, {})["character_sheet"] = sheet_path
        return self.project


class _FakeGenerator:
    def __init__(self):
        self.image_calls = []
        self.image_reference_bytes = []
        self.video_calls = []
        self.versions = self
        self.current_versions = {}

    def generate_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/image.png"), 1

    async def generate_image_async(self, **kwargs):
        self.image_calls.append(kwargs)
        self.image_reference_bytes.append(
            [
                (reference["image"] if isinstance(reference, dict) else reference).read_bytes()
                for reference in kwargs.get("reference_images") or []
            ]
        )
        self.current_versions[(kwargs["resource_type"], kwargs["resource_id"])] = 1
        return Path("/tmp/image.png"), 1

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    async def generate_video_async(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"version": 1, "created_at": "2026-01-01T00:00:00Z"}]}

    def get_current_version(self, resource_type, resource_id):
        return self.current_versions.get((resource_type, resource_id), 0)

    def reject_current_version(self, resource_type, resource_id, *, rejected_version, current_file, on_reject=None):
        key = (resource_type, resource_id)
        if self.current_versions.get(key) != rejected_version:
            return False
        self.current_versions[key] = 0
        if on_reject is not None:
            on_reject()
        return True


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
    (project_path / "products" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "products" / "refs" / "保温杯_1.jpg").write_bytes(b"jpg")
    return project_path


def _persist_active_fake_project(fake_pm: _FakePM, *, register_script: bool = True) -> None:
    """Persist the fake manager's state as one schema-8 project."""

    fake_pm.project.update(
        {
            "schema_version": 8,
            "generation_mode": "storyboard",
            "aspect_ratio": "9:16",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_pm.script["episode"] = 1
    scripts_dir = fake_pm.project_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (fake_pm.project_path / "project.json").write_text(
        json.dumps(fake_pm.project, ensure_ascii=False),
        encoding="utf-8",
    )
    (scripts_dir / "episode_1.json").write_text(
        json.dumps(fake_pm.script, ensure_ascii=False),
        encoding="utf-8",
    )
    if register_script:
        ArtifactManifest(ProjectArtifactManifestAdapter(fake_pm.project_path)).register(
            ArtifactKey.episode_script(1),
            artifact_path="scripts/episode_1.json",
            basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
        )


def _register_stale_visual_claim(project_path: Path, key: ArtifactKey, artifact_path: str) -> None:
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        key,
        artifact_path=artifact_path,
        basis=ArtifactBasis.build("test/visual-reference", kind_version=1, inputs={}),
    )


class TestGenerationTasks:
    @pytest.mark.unit
    async def test_formal_finalizer_without_task_id_defers_cancellation(self):
        started = threading.Event()
        release = threading.Event()

        def _finalize() -> str:
            started.set()
            assert release.wait(timeout=5)
            return "committed"

        task = asyncio.create_task(generation_tasks.run_formal_task_finalizer(_finalize, task_id=None))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()

        assert await task == "committed"

    @pytest.mark.unit
    def test_helper_functions(self, tmp_path):
        from lib.storyboard_sequence import get_storyboard_items

        mode_items = get_storyboard_items({"content_mode": "drama", "scenes": []})
        assert mode_items[1] == "scene_id"

        prompt = generation_tasks._normalize_storyboard_prompt("text", "Anime")
        assert prompt == append_image_negative_tail("text")
        assert generation_tasks._normalize_storyboard_prompt(prompt, "Anime") == prompt

        structured_input = {
            "scene": "林清坐在窗边",
            "composition": {"shot_type": "Close-up", "lighting": "暖光", "ambiance": "薄雾"},
        }
        structured = generation_tasks._normalize_storyboard_prompt(structured_input, "Anime")
        assert structured == append_image_negative_tail(image_prompt_to_yaml(structured_input, "Anime"))

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

    @pytest.mark.unit
    async def test_execute_task_dispatch(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        emitted_batches = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
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
        # 资产 sheet 以「资产名 label」显式绑定（供 Gemini 等支持内联标签的后端
        # 把参考图与 prompt 专名对应）；provider 收到的是任务私有快照，extra 无标签仍保持裸 Path。
        assert [ref.get("label") if isinstance(ref, dict) else None for ref in storyboard_refs] == [
            "Alice",
            "祠堂",
            "玉佩",
            None,
            PREVIOUS_STORYBOARD_REFERENCE_LABEL,
        ]
        assert storyboard_refs[-1]["description"] == PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION
        assert all(
            not (ref["image"] if isinstance(ref, dict) else ref).is_relative_to(project_path) for ref in storyboard_refs
        )
        assert fake_generator.image_reference_bytes[0] == [b"png"] * 5

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S03",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )
        assert [ref["label"] for ref in fake_generator.image_calls[1]["reference_images"]] == [
            "Alice",
            "祠堂",
            "玉佩",
        ]
        assert fake_generator.image_reference_bytes[1] == [b"png"] * 3

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

    @pytest.mark.unit
    async def test_storyboard_registers_manifest_only_after_finalization_succeeds(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        registered: list[tuple[tuple[object, ...], dict[str, object]]] = []

        class _BrokenVersionLookup(_FakeGenerator):
            def get_versions(self, resource_type, resource_id):
                raise RuntimeError("injected finalization failure")

        fake_generator = _BrokenVersionLookup()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(
            generation_tasks,
            "register_current_resource_artifact",
            lambda *args, **kwargs: registered.append((args, kwargs)),
        )

        with pytest.raises(RuntimeError, match="injected finalization failure"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert registered == []

    @pytest.mark.unit
    async def test_storyboard_registers_manifest_after_successful_formal_commit(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        registered: list[tuple[tuple[object, ...], dict[str, object]]] = []
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(
            generation_tasks,
            "register_current_resource_artifact",
            lambda *args, **kwargs: registered.append((args, kwargs)),
        )

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert len(registered) == 1
        args, kwargs = registered[0]
        assert args == (project_path,)
        assert kwargs["resource_type"] == "storyboards"
        assert kwargs["resource_id"] == "E1S01"
        assert kwargs["script_file"] == "episode_1.json"
        assert kwargs["artifact_path"] == "storyboards/scene_E1S01.png"
        assert isinstance(kwargs["basis"], ArtifactBasis)

    @pytest.mark.integration
    async def test_schema8_storyboard_excludes_unclaimed_formal_references(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S02",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert fake_generator.image_calls[0]["reference_images"] is None

    @pytest.mark.integration
    async def test_schema8_storyboard_rejects_an_unclaimed_bound_script_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _persist_active_fake_project(fake_pm, register_script=False)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="episode script is not registered"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    @pytest.mark.integration
    async def test_schema8_video_rejects_an_unclaimed_bound_script_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm, register_script=False)
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01.png",
        )
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="episode script is not registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert fake_generator.video_calls == []

    @pytest.mark.integration
    def test_grid_completion_serializes_manifest_registration_with_schema_activation(self, tmp_path, monkeypatch):
        from lib import artifact_activation
        from lib.artifact_activation import activate_artifact_target_state
        from lib.grid.models import GridGeneration
        from lib.grid_manager import GridManager
        from lib.version_manager import VersionManager

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(
            {
                "schema_version": 7,
                "generation_mode": "storyboard",
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        )
        fake_pm.script["episode"] = 1
        (project_path / "scripts").mkdir(exist_ok=True)
        (project_path / "project.json").write_text(
            json.dumps(fake_pm.project, ensure_ascii=False),
            encoding="utf-8",
        )
        (project_path / "scripts" / "episode_1.json").write_text(
            json.dumps(fake_pm.script, ensure_ascii=False),
            encoding="utf-8",
        )
        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01"],
            rows=1,
            cols=1,
            grid_size="grid_1",
            provider="test",
            model="test",
            video_aspect_ratio="9:16",
        )
        grid.status = "generating"
        manager = GridManager(project_path)
        manager.save(grid)
        staged = project_path / "grids" / f".{grid.id}.staged.png"
        staged.write_bytes(b"paid-grid")
        current = manager.image_path(grid.id)
        basis = ArtifactBasis.build("test/grid", kind_version=1, inputs={"grid": grid.id})
        outcomes = []
        commit = generation_tasks._grid_formal_image_callback(
            project_path=project_path,
            grid_manager=manager,
            grid=grid,
            initial_grid=grid.to_dict(),
            resource_id=grid.id,
            prompt="grid",
            versions=VersionManager(project_path),
            task_id=None,
            basis=basis,
            outcome_box=outcomes,
        )

        activation_ready = threading.Event()
        release_activation = threading.Event()
        writer_started = threading.Event()
        writer_done = threading.Event()
        failures: list[BaseException] = []
        original_commit_schema = artifact_activation._commit_schema_version

        def _pause_before_schema(project_dir, project):
            activation_ready.set()
            assert release_activation.wait(timeout=5)
            original_commit_schema(project_dir, project)

        monkeypatch.setattr(artifact_activation, "_commit_schema_version", _pause_before_schema)

        def _activate() -> None:
            try:
                activate_artifact_target_state(project_path, bump_schema=True)
            except Exception as exc:
                failures.append(exc)

        def _complete_grid() -> None:
            writer_started.set()
            try:
                commit(staged, current, {})
            except Exception as exc:
                failures.append(exc)
            finally:
                writer_done.set()

        activation_thread = threading.Thread(target=_activate)
        writer_thread = threading.Thread(target=_complete_grid)
        activation_thread.start()
        assert activation_ready.wait(timeout=5)
        writer_thread.start()
        assert writer_started.wait(timeout=5)
        assert not writer_done.wait(timeout=0.2)
        release_activation.set()
        activation_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        assert not activation_thread.is_alive()
        assert not writer_thread.is_alive()
        assert failures == []
        assert json.loads((project_path / "project.json").read_text(encoding="utf-8"))["schema_version"] == 8
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_grid(1, grid.id))
        assert entry is not None
        assert entry.basis_digest == basis.digest

    @pytest.mark.integration
    async def test_storyboard_rechecks_selected_manifest_claims_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][2]["scenes"] = []
        fake_pm.script["segments"][2]["props"] = []
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.asset_sheet("character", "Alice")
        _register_stale_visual_claim(project_path, key, "characters/Alice.png")
        fake_generator = _FakeGenerator()
        resolve_context = _fake_resolve_ctx(fake_generator)

        async def _delete_claim_then_resolve(*args, **kwargs):
            ProjectArtifactManifestAdapter(project_path).delete_entry(key)
            return await resolve_context(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _delete_claim_then_resolve)

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S03",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    @pytest.mark.integration
    async def test_storyboard_rejects_same_basis_bytes_replaced_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][2]["scenes"] = []
        fake_pm.script["segments"][2]["props"] = []
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.asset_sheet("character", "Alice")
        artifact_path = "characters/Alice.png"
        _register_stale_visual_claim(project_path, key, artifact_path)
        fake_generator = _FakeGenerator()
        resolve_context = _fake_resolve_ctx(fake_generator)

        async def _replace_bytes_then_resolve(*args, **kwargs):
            (project_path / artifact_path).write_bytes(b"replacement")
            return await resolve_context(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _replace_bytes_then_resolve)

        with pytest.raises(ValueError, match="changed since it was selected"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S03",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    @pytest.mark.integration
    async def test_storyboard_provider_reads_the_same_reference_bytes_as_its_basis(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import VisualReference, build_storyboard_image_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        fake_pm.script["segments"][1]["scenes"] = []
        fake_pm.script["segments"][1]["props"] = []
        _persist_active_fake_project(fake_pm)
        character_path = project_path / "characters" / "Alice.png"
        previous_path = project_path / "storyboards" / "scene_E1S01.png"
        character_path.write_bytes(b"selected-character")
        previous_path.write_bytes(b"selected-previous")
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.asset_sheet("character", "Alice"),
            "characters/Alice.png",
        )
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01.png",
        )
        expected_basis = build_storyboard_image_visual_basis(
            resource_id="E1S02",
            image_prompt="direct prompt",
            style="Anime",
            aspect_ratio="9:16",
            references=(
                VisualReference(
                    path=character_path,
                    role="asset_sheet",
                    logical_type="character",
                    logical_id="Alice",
                    kind="sheet",
                ),
                VisualReference(
                    path=previous_path,
                    role="previous_storyboard",
                    logical_type="storyboard",
                    logical_id="E1S01",
                ),
            ),
        )
        captured_basis: list[ArtifactBasis] = []

        class _RacingGenerator(_FakeGenerator):
            def __init__(self):
                super().__init__()
                self.reference_bytes: list[bytes] = []

            async def generate_image_async(self, **kwargs):
                character_path.write_bytes(b"changed-character")
                previous_path.write_bytes(b"changed-previous")
                refs = kwargs["reference_images"]
                self.reference_bytes = [(ref["image"] if isinstance(ref, dict) else ref).read_bytes() for ref in refs]
                return await super().generate_image_async(**kwargs)

        fake_generator = _RacingGenerator()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        def _register(*_args, **kwargs):
            captured_basis.append(kwargs["basis"])
            return True

        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", _register)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S02",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert fake_generator.reference_bytes == [b"selected-character", b"selected-previous"]
        assert captured_basis == [expected_basis]

    @pytest.mark.unit
    async def test_formal_image_commit_requires_the_exact_selected_version_record(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        current = project_path / "characters" / "Alice.png"
        staged = project_path / "characters" / ".Alice.stage.png"

        class _IncompleteVersions:
            def __init__(self):
                self.versions = self

            async def generate_image_async(self, **kwargs):
                staged.write_bytes(b"generated")
                version = kwargs["commit_formal_output"](staged, current, {})
                return current, version

            def commit_staged_version(self, **kwargs):
                kwargs["current_file"].write_bytes(kwargs["staged_file"].read_bytes())
                kwargs["on_commit"]()
                return 2

            def get_current_version(self, resource_type, resource_id):
                return 2

            def get_versions(self, resource_type, resource_id):
                return {"versions": [{"version": 1, "created_at": "2026-01-01T00:00:00Z"}]}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(_IncompleteVersions()),
        )
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        with pytest.raises(RuntimeError, match="creation timestamp"):
            await generation_tasks.execute_character_task(
                "demo",
                "Alice",
                {"prompt": "hero"},
            )

    @pytest.mark.unit
    async def test_storyboard_registers_generation_frozen_basis_when_script_changes_in_flight(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import build_storyboard_image_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        captured: list[ArtifactBasis] = []
        original_generate = fake_generator.generate_image_async

        async def _generate(**kwargs):
            result = await original_generate(**kwargs)
            fake_pm.script["segments"][0]["image_prompt"] = "latest persisted prompt"
            return result

        fake_generator.generate_image_async = _generate
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "queued prompt"},
            task_id="storyboard-task",
        )

        expected = build_storyboard_image_visual_basis(
            resource_id="E1S01",
            image_prompt="queued prompt",
            style="Anime",
            aspect_ratio="9:16",
            references=(),
        )
        latest = build_storyboard_image_visual_basis(
            resource_id="E1S01",
            image_prompt="latest persisted prompt",
            style="Anime",
            aspect_ratio="9:16",
            references=(),
        )
        assert captured == [expected]
        assert captured[0].digest != latest.digest

    @pytest.mark.unit
    async def test_asset_sheet_registers_generation_frozen_basis_when_definition_changes_in_flight(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import VisualReference, build_asset_sheet_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        captured: list[ArtifactBasis] = []
        original_generate = fake_generator.generate_image_async

        async def _generate(**kwargs):
            result = await original_generate(**kwargs)
            fake_pm.project["characters"]["Alice"]["description"] = "latest definition"
            return result

        fake_generator.generate_image_async = _generate
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register)

        await generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "queued definition"},
            task_id="character-task",
        )

        references = (
            VisualReference(
                path=project_path / "characters" / "refs" / "Alice-ref.png",
                role="source",
                logical_type="character",
                logical_id="Alice",
                kind="original",
            ),
        )
        expected = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="queued definition",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="16:9",
            references=references,
        )
        latest = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="latest definition",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="16:9",
            references=references,
        )
        assert captured == [expected]
        assert captured[0].digest != latest.digest

    @pytest.mark.integration
    async def test_formal_image_version_records_complete_frozen_basis_evidence(self, tmp_path, monkeypatch):
        from lib.media_generator import task_image_staging_path
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager
        from lib.visual_artifact_provenance import build_asset_sheet_visual_basis

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "queued definition")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        version_manager = VersionManager(project_path)

        class _Generator:
            versions = version_manager

            async def generate_image_async(self, **kwargs):
                staged = task_image_staging_path(current, kwargs["task_id"])
                staged.write_bytes(b"generated-sheet")
                version = kwargs["commit_formal_output"](
                    staged,
                    current,
                    {"aspect_ratio": "16:9"},
                )
                return current, version

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        result = await generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "queued definition"},
        )

        record = next(
            item
            for item in version_manager.get_versions("characters", "Alice")["versions"]
            if item["version"] == result["version"]
        )
        project = pm.load_project("demo")
        expected = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="queued definition",
            style=str(project.get("style") or ""),
            style_description=str(project.get("style_description") or ""),
            aspect_ratio="16:9",
        )
        assert ArtifactBasis.from_evidence_dict(record["artifact_image_basis"]) == expected

    @pytest.mark.unit
    async def test_storyboard_cancellation_waits_for_registration_and_returns_compensation(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        registration_started = threading.Event()
        finish_registration = threading.Event()
        compensated: list[str] = []

        class _Receipt:
            def compensate_cancelled(self) -> None:
                compensated.append("manifest")

        def _register(*_args, **_kwargs):
            registration_started.set()
            assert finish_registration.wait(timeout=5)
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", _register)
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register, raising=False)

        task = asyncio.create_task(
            generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
                task_id="storyboard-task",
            )
        )
        assert await asyncio.to_thread(registration_started.wait, 5)
        task.cancel()
        finish_registration.set()

        result = await task

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()
        assert compensated == ["manifest"]

    @pytest.mark.integration
    async def test_storyboard_cancellation_restores_selected_media_version_and_metadata(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        projects_root = tmp_path / "projects"
        pm = ProjectManager(projects_root)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_episode("demo", 1, "E1", "scripts/episode_1.json")
        pm.save_script(
            "demo",
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "旁白",
                        "image_prompt": "queued prompt",
                        "generated_assets": {"storyboard_image": "storyboards/old.png", "status": "pending"},
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = pm.get_project_path("demo")
        current = project_path / "storyboards" / "scene_E1S01.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-image")
        version_manager = VersionManager(project_path)
        old_version = version_manager.add_version("storyboards", "E1S01", "old", source_file=current)
        current.write_bytes(b"cancelled-image")
        selected_version = version_manager.add_version("storyboards", "E1S01", "new", source_file=current)

        class _Generator:
            versions = version_manager

            async def generate_image_async(self, **_kwargs):
                return current, selected_version

        compensated: list[str] = []

        class _Receipt:
            def compensate_cancelled(self) -> None:
                compensated.append("manifest")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", lambda *_a, **_kw: _Receipt())
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
            ArtifactKey.episode_script(1),
            artifact_path="scripts/episode_1.json",
            basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
        )

        result = await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "queued prompt"},
            task_id="storyboard-task",
        )
        committed = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert committed["storyboard_image"] == "storyboards/scene_E1S01.png"
        assert version_manager.get_current_version("storyboards", "E1S01") == selected_version

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()

        restored = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert restored == {"storyboard_image": "storyboards/old.png", "status": "pending"}
        assert version_manager.get_current_version("storyboards", "E1S01") == old_version
        assert current.read_bytes() == b"old-image"
        assert compensated == ["manifest"]

    @pytest.mark.integration
    async def test_asset_sheet_cancellation_uses_the_same_full_selection_compensation(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "hero")

        def _set_old_sheet(project):
            project["characters"]["Alice"]["character_sheet"] = "characters/old.png"

        pm.update_project("demo", _set_old_sheet)
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-sheet")
        version_manager = VersionManager(project_path)
        old_version = version_manager.add_version("characters", "Alice", "old", source_file=current)
        current.write_bytes(b"cancelled-sheet")
        selected_version = version_manager.add_version("characters", "Alice", "new", source_file=current)

        class _Generator:
            versions = version_manager

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", lambda *_a, **_kw: _Receipt())

        _created_at, receipt = await generation_tasks._finalize_asset_sheet_task(
            asset_type="character",
            project_name="demo",
            resource_id="Alice",
            sheet_path="characters/Alice.png",
            generator=_Generator(),
            version=selected_version,
            task_id="character-task",
        )
        assert receipt is not None
        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"

        receipt.compensate_cancelled()

        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/old.png"
        assert version_manager.get_current_version("characters", "Alice") == old_version
        assert current.read_bytes() == b"old-sheet"

    @pytest.mark.integration
    async def test_asset_generation_registration_failure_never_exposes_uncommitted_image(self, tmp_path, monkeypatch):
        from lib.media_generator import task_image_staging_path
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "queued definition")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-sheet")
        versions = VersionManager(project_path)
        old_version = versions.add_version("characters", "Alice", "old", source_file=current)

        class _Generator:
            def __init__(self):
                self.versions = versions

            async def generate_image_async(self, **kwargs):
                assert kwargs["formal_output"] is True
                staged = task_image_staging_path(current, kwargs["task_id"])
                staged.write_bytes(b"new-sheet")
                version = kwargs["commit_formal_output"](staged, current, {"aspect_ratio": "16:9"})
                return current, version

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(
            generation_tasks,
            "register_task_current_resource_artifact",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
        )

        with pytest.raises(RuntimeError, match="manifest commit failed"):
            await generation_tasks.execute_character_task(
                "demo",
                "Alice",
                {"prompt": "queued definition"},
                task_id="character-task",
            )

        assert current.read_bytes() == b"old-sheet"
        assert versions.get_current_version("characters", "Alice") == old_version
        assert pm.load_project("demo")["characters"]["Alice"].get("character_sheet") == ""

    @pytest.mark.unit
    async def test_reused_video_result_emits_the_normal_generation_success_event(self, monkeypatch):
        reused = {
            "version": 3,
            "file_path": "videos/scene_E1S01.mp4",
            "resource_type": "videos",
            "resource_id": "E1S01",
            "reused_existing": True,
        }

        async def _executor(*_args, **_kwargs):
            return reused

        emitted: list[dict] = []
        monkeypatch.setitem(generation_tasks._TASK_EXECUTORS, "video", _executor)
        monkeypatch.setattr(
            generation_tasks,
            "emit_generation_success_batch",
            lambda **kwargs: emitted.append(kwargs) or {},
        )

        result = await generation_tasks.execute_generation_task(
            {
                "task_id": "task-reuse",
                "task_type": "video",
                "project_name": "demo",
                "resource_id": "E1S01",
                "payload": {"script_file": "episode_1.json"},
            }
        )

        assert result is reused
        assert emitted == [
            {
                "task_type": "video",
                "project_name": "demo",
                "resource_id": "E1S01",
                "payload": {"script_file": "episode_1.json"},
            }
        ]

    @pytest.mark.unit
    async def test_generation_event_failure_runs_media_compensation_off_the_event_loop(self, monkeypatch):
        event_loop_thread = threading.get_ident()
        compensation_threads: list[int] = []

        async def _executor(*_args, **_kwargs):
            return CompensableGenerationResult(
                {"resource_type": "storyboards", "resource_id": "E1S01"},
                cancel_compensation=lambda: compensation_threads.append(threading.get_ident()),
            )

        def _fail_event(**_kwargs):
            raise RuntimeError("event emission failed")

        monkeypatch.setitem(generation_tasks._TASK_EXECUTORS, "storyboard", _executor)
        monkeypatch.setattr(generation_tasks, "emit_generation_success_batch", _fail_event)

        with pytest.raises(RuntimeError, match="event emission failed"):
            await generation_tasks.execute_generation_task(
                {
                    "task_type": "storyboard",
                    "project_name": "demo",
                    "resource_id": "E1S01",
                    "payload": {},
                }
            )

        assert len(compensation_threads) == 1
        assert compensation_threads[0] != event_loop_thread

    @pytest.mark.unit
    async def test_execute_product_task_injects_reference_images(self, tmp_path, monkeypatch):
        """product sheet 生成把用户上传原图作为参考注入（标准化整理的输入），缺失文件跳过；
        完成后回写 product_sheet。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        result = await generation_tasks.execute_product_task(
            "demo",
            "保温杯",
            {"prompt": "不锈钢保温杯，银色磨砂"},
        )
        assert result["resource_type"] == "products"
        assert result["file_path"] == "products/保温杯.png"
        assert fake_pm.project["products"]["保温杯"]["product_sheet"] == "products/保温杯.png"

        call = fake_generator.image_calls[0]
        # 仅存在的原图进入参考；缺失文件跳过
        assert len(call["reference_images"]) == 1
        assert call["reference_images"][0].name == "0000-保温杯_1.jpg"
        assert not call["reference_images"][0].is_relative_to(project_path)
        assert fake_generator.image_reference_bytes[0] == [b"jpg"]
        assert "保温杯" in call["prompt"]

    @pytest.mark.unit
    async def test_execute_product_task_without_refs_is_t2i(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = []
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        await generation_tasks.execute_product_task("demo", "保温杯", {"prompt": "保温杯"})
        assert fake_generator.image_calls[0]["reference_images"] is None

    @pytest.mark.unit
    def test_collect_product_reference_images_rejects_path_escape(self, tmp_path):
        """reference_images 中的绝对路径与 `..` 穿越值不得越出项目目录读取宿主机文件；目录路径同样跳过。"""
        project_path = _prepare_files(tmp_path)
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"jpg")
        project = {
            "products": {
                "保温杯": {
                    "reference_images": [
                        str(outside),
                        "../outside.jpg",
                        "products/refs/../../../outside.jpg",
                        "products/refs",
                        "products/refs/保温杯_1.jpg",
                    ],
                }
            }
        }

        result = generation_tasks._collect_product_reference_images(project, project_path, "保温杯")

        assert result == [project_path / "products" / "refs" / "保温杯_1.jpg"]

    @pytest.mark.unit
    def test_product_fingerprints(self, monkeypatch, tmp_path):
        project_path = _prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        fps = generation_tasks.compute_affected_fingerprints("demo", "product", "保温杯")
        assert "products/保温杯.png" in fps

    @pytest.mark.unit
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
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
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

    @pytest.mark.integration
    async def test_storyboard_worker_materializes_current_request_and_checkpoints_staged_frames(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.reference_video.execution_checkpoint import StoryboardSubmissionCheckpoint

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        current_prompt = {"action": "current action", "camera_motion": "Static", "dialogue": []}
        item["video_prompt"] = current_prompt
        item["duration_seconds"] = 8
        manifest = project_path / ".arcreel_artifacts.json"
        manifest.write_bytes(b'{"unchanged":true}')
        submitted: dict[str, Mapping[str, object]] = {}

        class _CheckpointingGenerator(_FakeGenerator):
            async def generate_video_async(self, **kwargs):
                self.video_calls.append(kwargs)
                start_image = kwargs["start_image"]
                assert isinstance(start_image, Path)
                assert ".arcreel/tasks/task-storyboard/provider_media/" in start_image.as_posix()
                assert start_image.read_bytes() == b"png"
                metadata = await kwargs["before_submit"](41)
                assert metadata is not None
                submitted["metadata"] = metadata
                from lib.version_manager import PaidVersionCommit

                kwargs["commit_formal_output"].outcome = PaidVersionCommit(version=2, selected=True)
                return project_path / "videos" / "scene_E1S01.mp4", 2, "ref", "uri"

        fake_generator = _CheckpointingGenerator()
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=(4, 8, 12)),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "stale.json",
                "prompt": {"action": "stale enqueue prompt"},
                "duration_seconds": 4,
                "video_provider_i2v": "stale/model",
            },
            script_file="episode_1.json",
            task_id="task-storyboard",
        )

        raw = fake_queue.persist_execution_checkpoint.await_args.args[1]
        checkpoint = StoryboardSubmissionCheckpoint.from_json(raw)
        call = fake_generator.video_calls[0]
        assert checkpoint.script_file == "episode_1.json"
        assert checkpoint.prompt == call["prompt"]
        assert "current action" in checkpoint.prompt
        assert "stale enqueue prompt" not in checkpoint.prompt
        assert checkpoint.duration_seconds == 8
        assert checkpoint.provider_id == "ark"
        assert [media.role for media in checkpoint.media] == ["start_image"]
        assert checkpoint.artifact_visual_basis is not None
        assert checkpoint.artifact_visual_basis.kind == "artifact-visual/video-storyboard"
        assert checkpoint.artifact_currency is not None
        assert submitted["metadata"]["artifact_video_currency"] == checkpoint.artifact_currency.to_dict()
        assert call["formal_output"] is True
        assert submitted["metadata"]["execution_request_digest"] == checkpoint.request_digest
        assert manifest.read_bytes() == b'{"unchanged":true}'
        assert not (project_path / ".arcreel" / "tasks" / "task-storyboard" / "provider_media").exists()

    @pytest.mark.integration
    async def test_execute_video_task_lane_bucket_follows_project_route(self, monkeypatch, tmp_path):
        """lane 归桶按项目路线求值，不再无条件 i2v——与提交入口口径同源。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        seen_lanes: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, seen_lane_requests=seen_lanes),
        )
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        payload = {
            "script_file": "episode_1.json",
            "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
        }
        fake_pm.project["generation_mode"] = "storyboard"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].capability == "i2v"

        fake_pm.project["generation_mode"] = "reference_video"
        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert seen_lanes[-1]["video"].capability == "r2v"

    @pytest.mark.unit
    async def test_execute_video_task_rejects_unsupported_duration(self, monkeypatch, tmp_path):
        """执行层在解析出 ProviderModel 后，对越界 duration 以明确错误拒绝。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

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

    @pytest.mark.unit
    async def test_execute_video_task_supported_duration_passes(self, monkeypatch, tmp_path):
        """合法 duration 通过守卫，正常进入后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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

    @pytest.mark.unit
    async def test_execute_post_production_video_does_not_require_an_episode_number(self, monkeypatch, tmp_path):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        (project_path / "scripts" / "legacy_script.json").write_text(
            json.dumps(fake_pm.script, ensure_ascii=False),
            encoding="utf-8",
        )
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "legacy_script.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )

        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    @pytest.mark.integration
    async def test_execute_video_task_reprojects_current_tts_and_rejects_changed_tier(self, monkeypatch, tmp_path):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        current_tts_duration = 6.2
        seen_lane_requests: list[dict] = []

        async def fake_prepare_current_narrated_video_duration(**kwargs):
            tts_settings = await kwargs["resolver"].resolve_tts_synthesis_settings(kwargs["project"])
            assert tts_settings == TtsSynthesisSettings("dashscope", "actual-tts", "Cherry", 1.1)
            narration = NarrationDeliveryPreparation(
                delivery=USE_TTS,
                unit_id="E1S01",
                speech_mode=None,
                tts_status=NarrationTtsStatus.CURRENT,
                artifact_path="audio/segment_E1S01.wav",
                basis_digest="current-basis",
                actual_duration_seconds=current_tts_duration,
                problems=(),
            )
            return prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=kwargs["planned_duration_seconds"],
                supported_durations=kwargs["supported_durations"],
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            )

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                supported_durations=(4, 8, 12),
                seen_lane_requests=seen_lane_requests,
            ),
        )
        monkeypatch.setattr(
            generation_tasks,
            "prepare_current_narrated_video_duration",
            fake_prepare_current_narrated_video_duration,
        )
        monkeypatch.setattr(generation_tasks, "tts_task_in_progress", AsyncMock(return_value=False))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        payload = {
            "script_file": "episode_1.json",
            "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
            "narration_delivery_options": {
                "narration_delivery": USE_TTS,
                "confirmed_request_duration_seconds": 8,
            },
        }

        await generation_tasks.execute_video_task("demo", "E1S01", payload)
        assert fake_generator.video_calls[0]["duration_seconds"] == 8
        assert len(seen_lane_requests) == 1
        assert seen_lane_requests[0]["video"] is not None
        assert seen_lane_requests[0]["audio"] is not None

        # Worker 必须从执行时最新 unit 重新取规划时长；队列不保存预检派生档位。
        fake_pm.script["segments"][0]["duration_seconds"] = 8
        current_tts_duration = 9.5
        with pytest.raises(NarratedVideoDurationBlockedError) as exc:
            await generation_tasks.execute_video_task("demo", "E1S01", payload)

        assert exc.value.preparation.problem_payloads()[0]["code"] == "reference_duration_confirmation_required"
        assert exc.value.preparation.request_duration_seconds == 12
        assert len(fake_generator.video_calls) == 1
        assert len(seen_lane_requests) == 2

    @pytest.mark.integration
    async def test_execute_video_task_reuses_selected_visual_in_the_latest_tts_tier_without_side_effects(
        self,
        monkeypatch,
        tmp_path,
    ):
        from lib.artifact_manifest import (
            ArtifactKey,
            ArtifactManifest,
            ProjectArtifactManifestAdapter,
            compose_video_artifact_basis,
        )
        from lib.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
        from lib.speech_composition import admit_script_unit
        from lib.version_manager import VersionManager
        from lib.video_artifact_facts import VideoArtifactCurrencyFacts
        from lib.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_generator.versions = VersionManager(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "current narration"
        item["generated_assets"] = {
            "status": "completed",
            "video_clip": "videos/scene_E1S01.mp4",
            "video_uri": "provider://existing",
        }
        current = project_path / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"existing-paid-video")
        visual_prompt = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["video_prompt"] = visual_prompt
        visual_basis = build_storyboard_video_visual_basis(
            prompt=visual_prompt,
            storyboard_image=project_path / "storyboards" / "scene_E1S01.png",
            end_frame_image=None,
            aspect_ratio="9:16",
            provider_id="ark",
            model_id="seedance",
            resolution="720p",
            seed=None,
            requested_generate_audio=True,
            content_mode="narration",
            utterances=None,
            has_utterances=False,
            voice_characters=None,
        )
        artifact_visual_basis = build_storyboard_video_artifact_visual_basis(
            resource_id="E1S01",
            visual_prompt=visual_prompt,
            storyboard_image=project_path / "storyboards" / "scene_E1S01.png",
            end_frame_image=None,
            aspect_ratio="9:16",
        )
        artifact_speech_basis = build_video_speech_basis(admit_script_unit("segments", item).preparation)
        artifact_duration_basis = build_video_duration_basis(8)
        artifact_currency = VideoArtifactCurrencyFacts(
            episode=1,
            request_duration_seconds=8,
            visual_basis=artifact_visual_basis,
            speech_basis=artifact_speech_basis,
            duration_basis=artifact_duration_basis,
            video_basis=compose_video_artifact_basis(
                visual=artifact_visual_basis,
                speech=artifact_speech_basis,
                duration=artifact_duration_basis,
            ),
            voice_style_speakers=(),
            duration_tiers=(4, 8, 12),
            reference_image_limit=None,
            parent_version=0,
        )
        selected_version = fake_generator.versions.add_version(
            "videos",
            "E1S01",
            "old visual",
            source_file=current,
            duration_seconds=8,
            visual_basis_digest=visual_basis.digest,
            execution_checkpoint_schema_version=3,
            execution_script_file="episode_1.json",
            execution_duration_seconds=8,
            execution_request_digest="d" * 64,
            execution_provider_media=[],
            artifact_video_currency=artifact_currency.to_dict(),
        )
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register_descriptor(
            ArtifactKey.episode_video(1, "E1S01"),
            artifact_path="videos/scene_E1S01.mp4",
            basis=artifact_currency.video_descriptor,
        )
        script_before = copy.deepcopy(fake_pm.script)
        history_before = copy.deepcopy(fake_generator.versions.get_versions("videos", "E1S01"))

        async def _prepare(**kwargs):
            narration = NarrationDeliveryPreparation(
                delivery=USE_TTS,
                unit_id="E1S01",
                speech_mode=None,
                tts_status=NarrationTtsStatus.CURRENT,
                artifact_path="audio/segment_E1S01.wav",
                basis_digest="sha256-v1:" + "c" * 64,
                actual_duration_seconds=6.2,
                problems=(),
            )
            return prepare_narrated_video_duration(
                narration=narration,
                planned_duration_seconds=kwargs["planned_duration_seconds"],
                supported_durations=kwargs["supported_durations"],
                confirmed_request_duration_seconds=kwargs["confirmed_request_duration_seconds"],
            )

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                video_provider=("ark", "configured-seedance"),
                video_backend_model="seedance",
                supported_durations=(4, 8, 12),
            ),
        )
        monkeypatch.setattr(generation_tasks, "prepare_current_narrated_video_duration", _prepare)
        monkeypatch.setattr(generation_tasks, "tts_task_in_progress", AsyncMock(return_value=False))
        monkeypatch.setattr(
            "server.services.narration_delivery_tasks.probe_existing_media_duration_seconds",
            AsyncMock(return_value=8.0),
        )
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": visual_prompt,
                "narration_delivery_options": {
                    "narration_delivery": USE_TTS,
                    "confirmed_request_duration_seconds": 8,
                },
            },
            task_id="task-reuse",
        )

        assert result["reused_existing"] is True
        assert result["version"] == selected_version
        assert result["request_duration_seconds"] == 8
        assert fake_generator.video_calls == []
        fake_queue.persist_execution_checkpoint.assert_not_awaited()
        assert not (project_path / ".arcreel" / "tasks" / "task-reuse" / "provider_media").exists()
        assert fake_pm.script == script_before
        assert fake_generator.versions.get_versions("videos", "E1S01") == history_before
        assert current.read_bytes() == b"existing-paid-video"

    @pytest.mark.integration
    async def test_execute_video_task_storyboard_image_legacy_grid_filename_resolves(self, monkeypatch, tmp_path):
        """旧宫格项目 storyboard_image 指向 scene_{id}_first.png（非 canonical 文件名），只要落在
        storyboards/ 目录内就正常解析——与 end_frame_image 不同，这里不要求文件名与 canonical
        路径逐一比对。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01_first.png"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["start_image"] == project_path / "storyboards" / "scene_E1S01_first.png"

    @pytest.mark.integration
    async def test_execute_video_task_missing_storyboard_image_fails_hard(self, monkeypatch, tmp_path):
        """storyboard_image 字段指向的文件缺失时硬失败，不调用后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_missing.png"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_schema8_requires_registered_storyboard(self, monkeypatch, tmp_path):
        """Schema 8 workers reject an existing storyboard whose Manifest claim is absent."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard is not registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_rechecks_storyboard_claim_after_staging_before_provider(
        self,
        monkeypatch,
        tmp_path,
    ):
        """A restore that drops the selected claim cannot race a paid video submission."""

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        item = fake_pm.script["segments"][0]
        item["novel_text"] = "旁白正文"
        item["video_prompt"] = {"action": "跑", "camera_motion": "Static", "dialogue": []}
        item["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.episode_storyboard(1, "E1S01")
        _register_stale_visual_claim(project_path, key, "storyboards/scene_E1S01.png")

        provider_submissions: list[str] = []

        class _SubmittingGenerator(_FakeGenerator):
            async def generate_video_async(self, **kwargs):
                await kwargs["before_submit"](72)
                provider_submissions.append("submitted")
                raise AssertionError("provider submission must remain unreachable")

        fake_generator = _SubmittingGenerator()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        original_stage = generation_tasks.stage_provider_media_for_task

        async def _drop_claim_after_staging(project_dir, task_id, inputs):
            staged = await original_stage(project_dir, task_id, inputs)
            ProjectArtifactManifestAdapter(project_path).delete_entry(key)
            return staged

        monkeypatch.setattr(generation_tasks, "stage_provider_media_for_task", _drop_claim_after_staging)
        fake_queue = type("Queue", (), {})()
        fake_queue.persist_execution_checkpoint = AsyncMock()
        monkeypatch.setattr(generation_tasks, "get_generation_queue", lambda: fake_queue)

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json"},
                task_id="task-storyboard-claim-race",
            )

        assert provider_submissions == []
        fake_queue.persist_execution_checkpoint.assert_not_awaited()

    @pytest.mark.integration
    async def test_execute_video_task_generated_assets_non_dict_falls_back_to_default(self, monkeypatch, tmp_path):
        """generated_assets 容器本身被外部编辑损坏为非 dict（如 list）时按「未设置」处理、
        回退默认路径，不抛未捕获 AttributeError（`{"...": ...}.get()` 在非 dict 上不存在）。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = ["bad"]

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["start_image"] == project_path / "storyboards" / "scene_E1S01.png"

    @pytest.mark.parametrize(
        ("storyboard_value", "expected_message"),
        [
            # 越界与脏数据：统称非法路径
            ("/etc/passwd", "invalid storyboard image path"),  # 绝对路径：裸 `/` 拼接会整体丢弃左操作数
            ("../../outside.png", "invalid storyboard image path"),  # `..` 穿越出项目目录
            (123, "invalid storyboard image path"),  # 剧本 JSON 里的脏数据（非字符串）须可读失败而非 TypeError
            (0, "invalid storyboard image path"),  # falsy 脏数据：真值判断不得当成「未设置」而静默回退默认路径
            (False, "invalid storyboard image path"),  # 同上
            ([], "invalid storyboard image path"),  # 同上
            ({}, "invalid storyboard image path"),  # 同上
            # 目录归属：项目内但不在 storyboards/，措辞需与越界区分，便于定位外部编辑过的剧本
            ("storyboards/../end_frames/scene_E1S01.png", "must stay under storyboards/"),
            ("end_frames/scene_E1S01.png", "must stay under storyboards/"),
        ],
    )
    @pytest.mark.integration
    async def test_execute_video_task_storyboard_image_outside_dir_fails_hard(
        self, monkeypatch, tmp_path, storyboard_value, expected_message
    ):
        """剧本是磁盘 JSON，storyboard_image 字段不可信：越界 / 绕开 storyboards 目录 / 脏数据
        一律硬失败，不把任意服务器文件送进视频请求上传给供应商。"""
        project_path = _prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": storyboard_value}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match=re.escape(expected_message)):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_storyboard_image_absolute_path_inside_project_fails_hard(
        self, monkeypatch, tmp_path
    ):
        """storyboard_image 是项目 storyboards/ 内的绝对路径时同样硬失败：`os.path.join` 遇绝对
        路径会丢弃项目根，越界校验只看结果是否落在项目内，光靠目录归属挡不住这类值，须显式拒绝
        绝对路径本身。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        absolute_value = str(project_path / "storyboards" / "scene_E1S01.png")
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": absolute_value}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid storyboard image path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_storyboard_image_rooted_no_drive_path_fails_hard(self, monkeypatch, tmp_path):
        """storyboard_image 是无盘符的根路径（如 `\\Users\\...`）时同样硬失败：`Path.is_absolute()`
        在 Windows 原生运行时对这类值返回 False，但 `os.path.join` 遇到根路径仍会丢弃项目根（仅
        保留 base 的盘符），须按正斜杠归一化后单独判断根分隔符开头。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "\\Users\\Alice\\scene.png"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid storyboard image path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_storyboard_image_pointing_to_directory_fails_hard(self, monkeypatch, tmp_path):
        """storyboard_image 指向 storyboards/ 内一个存在的目录（而非文件）时硬失败：目录同样
        通过 `exists()`，若不显式要求是文件，目录会被当作 start_image 传给视频后端，在编码阶段
        才失败且原因不可读。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "storyboards" / "subdir").mkdir(parents=True)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/subdir"}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="storyboard not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_image_passed_to_generator(self, monkeypatch, tmp_path):
        """镜头设置了 end_frame_image 时，生成视频请求携带 end_image；快照路径取自
        镜头持久字段拼接的项目内固定相对路径。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_image_bare_filename_resolves_via_default_dir(
        self, monkeypatch, tmp_path
    ):
        """尾帧字段是裸文件名（无 `end_frames/` 前缀）时按校验侧
        data_validator._resolve_existing_path 的 default_dir 回退口径解析——否则通过导入校验
        （校验器对裸文件名会补目录重试）的值会在生成期无理由硬失败。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] == end_frame_dir / "scene_E1S01.png"

    @pytest.mark.integration
    @pytest.mark.parametrize("missing", [True, False], ids=["field-absent", "empty-string"])
    async def test_execute_video_task_without_end_frame_image_passes_none(self, monkeypatch, tmp_path, missing):
        """未设置尾帧的镜头行为不变：字段缺失或显式空字符串，end_image 均为 None。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        if not missing:
            fake_pm.script["segments"][0]["end_frame_image"] = ""

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )

        assert fake_generator.video_calls[0]["end_image"] is None

    @pytest.mark.integration
    async def test_execute_video_task_missing_end_frame_snapshot_fails_hard(self, monkeypatch, tmp_path):
        """尾帧字段指向的快照文件缺失时硬失败，不调用后端生成。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="end frame snapshot not found"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.parametrize(
        "end_frame_value",
        [
            "/etc/passwd",  # 绝对路径：裸 `/` 拼接会整体丢弃左操作数，读到项目外文件
            "../../outside.png",  # `..` 穿越出项目目录
            "end_frames/../storyboards/scene_E1S01.png",  # 项目内但绕开快照目录，等于直接引用源图
            "storyboards/scene_E1S01.png",  # 同上：字段只接受 end_frames/ 内的快照
            123,  # 剧本 JSON 里的脏数据（非字符串）须给出可读失败，而非 TypeError
            0,  # falsy 脏数据：真值判断不得把它当成「未设置」而静默跳过尾帧
            False,  # 同上
            [],  # 同上
            {},  # 同上
            "end_frames/scene_E1S02.png",  # 落在快照目录内、文件也存在，但属于别的镜头——跨镜头误引
        ],
    )
    @pytest.mark.integration
    async def test_execute_video_task_end_frame_path_outside_snapshot_dir_fails_hard(
        self, monkeypatch, tmp_path, end_frame_value
    ):
        """剧本是磁盘 JSON，尾帧字段不可信：越界 / 绕开 end_frames 快照目录 / 脏数据一律硬失败，
        不把任意服务器文件送进视频请求。约束与写侧 end_frame.py、校验侧 data_validator 同口径。"""
        project_path = _prepare_files(tmp_path)
        (tmp_path / "outside.png").write_bytes(b"png")
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(b"png")  # 别的镜头的快照，供跨镜头误引用例检查
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = end_frame_value

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_canonical_path_symlink_fails_hard(self, monkeypatch, tmp_path):
        """尾帧字段值恰是当前镜头的 canonical 相对路径，但磁盘上那个位置被替换成指向别处
        （另一镜头快照）的符号链接：try_safe_join / safe_join 都会展开符号链接把两侧解析到
        同一真实目标，仅凭路径相等挡不住「路径字符串对、磁盘对象被调包」，须显式拒绝。"""
        project_path = _prepare_files(tmp_path)
        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S02.png").write_bytes(b"png")
        (end_frame_dir / "scene_E1S01.png").symlink_to(end_frame_dir / "scene_E1S02.png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_parent_dir_symlink_fails_hard(self, monkeypatch, tmp_path):
        """符号链接调包不止发生在文件名这一级：`end_frames/` 目录本身被替换成指向项目内
        别的目录的符号链接时，最终文件名一致、realpath 展开后两侧路径也相等，仅检查文件名
        这一段挡不住——须逐段检查 canonical 路径的每个组件（含父目录）。"""
        project_path = _prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        (project_path / "end_frames").symlink_to(real_dir)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_parent_dir_junction_fails_hard(self, monkeypatch, tmp_path):
        """Windows 原生环境下目录联接（junction）是独立于符号链接的 reparse point 类型，
        `Path.is_symlink()` 识别不到；`end_frames/` 被联接到项目内别的目录时须靠 `is_junction()`
        单独挡住。非 Windows 平台无法真实创建 junction，这里 monkeypatch `Path.is_junction()`
        模拟该状态，验证逐段检查确实调用了它而非只查符号链接。"""
        project_path = _prepare_files(tmp_path)
        real_dir = project_path / "storyboards_end_frames_swap"
        real_dir.mkdir(parents=True, exist_ok=True)
        (real_dir / "scene_E1S01.png").write_bytes(b"png")
        end_frames_dir = project_path / "end_frames"
        end_frames_dir.mkdir(parents=True, exist_ok=True)
        (end_frames_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(Path, "is_junction", lambda self: self == end_frames_dir)

        with pytest.raises(ValueError, match="invalid end frame snapshot path"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.integration
    async def test_execute_video_task_end_frame_capability_unsupported_propagates(self, monkeypatch, tmp_path):
        """后端不支持尾帧能力时硬失败，不降级为参考图、不静默丢帧。

        替身只替换 provider 调用，能力判定交给生产代码 gate_video_request 跑真值（caps
        last_frame=False）——否则替身按自己的条件抛异常，验的是替身而非接线是否真能触达
        gating。能力组合的各分支另见 tests/test_video_frame_slots.py。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        async def _plan_with_real_gating(**kwargs):
            fake_generator.video_calls.append(kwargs)
            gate_video_request(
                caps=VideoCapabilities(first_frame=True, last_frame=False),
                provider="ark",
                model="seedance",
                end_image=kwargs.get("end_image"),
            )
            raise AssertionError("gate_video_request 应在 end_image 非空且 caps.last_frame 为假时硬失败")

        fake_generator.generate_video_async = _plan_with_real_gating

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(VideoCapabilityError) as exc:
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert exc.value.code == "video_last_frame_unsupported"

    @pytest.mark.integration
    async def test_execute_video_task_reuses_end_frame_on_regeneration(self, monkeypatch, tmp_path):
        """视频重生成无需额外操作即自动沿用尾帧：字段是镜头持久属性，每次执行都从剧本重新加载。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        end_frame_dir = project_path / "end_frames"
        end_frame_dir.mkdir(parents=True, exist_ok=True)
        (end_frame_dir / "scene_E1S01.png").write_bytes(b"png")
        fake_pm.script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        for _ in range(2):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert len(fake_generator.video_calls) == 2
        for call in fake_generator.video_calls:
            assert call["end_image"] == end_frame_dir / "scene_E1S01.png"

    @pytest.mark.unit
    async def test_execute_video_task_drama_dialogue_from_utterances(self, monkeypatch, tmp_path):
        """drama 口型台词从场景级 dialogue-kind utterances 取（覆盖 payload 已不带的
        video_prompt.dialogue）；voiceover-kind 不进视频 YAML。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        # 改用 drama 剧本：E1S01 携带有序 utterances（voiceover 在前、dialogue 在后）
        fake_pm.script = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        # payload 的 video_prompt 不带 dialogue（drama 新结构）
        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        # dialogue-kind 台词与说话人进 YAML，voiceover-kind 不进视频提示词
        assert "你来了。" in prompt_yaml
        assert "王" in prompt_yaml
        assert "那是命运的开端。" not in prompt_yaml

    def _drama_script(self):
        return {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }

    def _legacy_drama_script(self):
        """utterances 迁移前的存量 drama 剧本：scene 无 utterances，台词仍留在 video_prompt.dialogue。"""
        script = self._drama_script()
        del script["scenes"][0]["utterances"]
        return script

    @pytest.mark.unit
    async def test_execute_video_task_injects_voice_profiles_for_audible_model(self, monkeypatch, tmp_path):
        """有音轨模型（voice_consistency != none）：dialogue speaker 命中的角色资产
        非空 voice_style 机械派生进 Voice_Profiles 顶部声明段。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        # 顶部集中声明段须先于 Action 出现
        assert prompt_yaml.index("Voice_Profiles") < prompt_yaml.index("Action")

    @pytest.mark.unit
    async def test_execute_video_task_skips_voice_profiles_for_silent_model(self, monkeypatch, tmp_path):
        """C 类（真无声，voice_consistency == none）模型不注入 Voice_Profiles。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="none")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        # 台词不随声音风格一并省略：无声成片里台词文本照常下发，供应商可用作口型参考
        assert "你来了。" in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_skips_voice_profiles_when_episode_audio_disabled(self, monkeypatch, tmp_path):
        """本集关闭音频（requested_generate_audio=False）：即便模型有音轨也不注入 Voice_Profiles，
        与 C 类真无声模型同口径。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._drama_script()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        # 台词逐字不变，与 C 类真无声路径同口径
        assert "你来了。" in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_strips_caller_supplied_voice_profiles_for_non_drama(self, monkeypatch, tmp_path):
        """narration/ad（item 无 utterances 字段）请求体自带 voice_profiles 时一律剥离：
        该声明段唯一来源是 build_drama_video_prompt 的机械派生，调用方不得越权注入、绕过
        C 类（真无声）门控。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "voice_profiles": [{"Speaker": "赝品", "Voice_Style": "越权"}],
                },
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "越权" not in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_injects_voice_profiles_from_legacy_dialogue(self, monkeypatch, tmp_path):
        """utterances 迁移前的存量 drama 剧本（scene 无 utterances 字段，台词仍在
        video_prompt.dialogue）：load_script 按原始 JSON 读盘不过 pydantic 迁移，改走 legacy
        出口派生 Voice_Profiles，不因缺 utterances 静默丢失。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._legacy_drama_script()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "王", "line": "你来了。"}],
                },
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        assert "你来了。" in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_skips_voice_profiles_from_legacy_dialogue_when_silent(
        self, monkeypatch, tmp_path
    ):
        """legacy dialogue 出口同过无声门控：本集关闭音频时不注入 Voice_Profiles，台词照常下发。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = self._legacy_drama_script()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, voice_consistency="soft", requested_generate_audio=False),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {
                    "action": "起身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                    "dialogue": [{"speaker": "王", "line": "你来了。"}],
                },
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" not in prompt_yaml
        assert "低沉沙哑" not in prompt_yaml
        assert "你来了。" in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_content_mode_falls_back_to_project_when_episode_omits_it(
        self, monkeypatch, tmp_path
    ):
        """存量 episode 剧本省略顶层 content_mode 时回退到 project.json 的 content_mode
        （与 lib.data_validator 已校验通过的既定口径一致），不因回退缺失而被误判为
        narration、静默跳过 dialogue 重建与 Voice_Profiles 注入。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["content_mode"] = "drama"
        fake_pm.project["characters"]["王"] = {"voice_style": "低沉沙哑"}
        fake_generator = _FakeGenerator()
        fake_pm.script = {
            # 顶层无 content_mode：存量 episode 省略该字段，真相源退到 project.json。
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "segment_break": False,
                    "characters_in_scene": ["王"],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                    "video_prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                    "utterances": [
                        {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
                    ],
                }
            ],
        }

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator, voice_consistency="soft")
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "起身", "camera_motion": "Static", "ambiance_audio": "风声"},
                "duration_seconds": 8,
            },
        )

        prompt_yaml = fake_generator.video_calls[0]["prompt"]
        assert "Voice_Profiles" in prompt_yaml
        assert "低沉沙哑" in prompt_yaml
        assert "你来了。" in prompt_yaml

    @pytest.mark.unit
    async def test_execute_video_task_default_duration_from_caps(self, monkeypatch, tmp_path):
        """无显式 duration 时，默认值由 caps 收口（取 supported_durations[0]），且必然合法。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=(6, 10)),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        # 项目默认 duration 也置空，强制走 caps 默认。
        fake_pm.project.pop("default_duration", None)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert result["resource_type"] == "videos"
        assert fake_generator.video_calls[0]["duration_seconds"] == 6

    @pytest.mark.unit
    async def test_execute_video_task_default_duration_respects_resolution_constraint(self, monkeypatch, tmp_path):
        """Auto（无显式 duration）在受约束分辨率下取约束内的时长，而非 supported_durations 首项。

        Veo + 4k 只接受 8 秒；取首项 4 秒会让默认设置必然撞上 backend 的执行期拒绝。
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(
                fake_generator,
                video_provider=("gemini-aistudio", "veo-3.1-generate-preview"),
                video_resolution="4k",
                supported_durations=(4, 6, 8),
            ),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)
        fake_pm.project.pop("default_duration", None)

        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []}},
        )
        assert fake_generator.video_calls[0]["duration_seconds"] == 8

    @pytest.mark.unit
    async def test_empty_supported_durations_guard_permissive(self, monkeypatch, tmp_path):
        """能力不可解析时 lane 交付空 supported_durations：守卫放行（不更坏），
        resolution 仍取自 lane 已解析出的值，不因能力缺失被改写。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, supported_durations=()),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

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
        assert fake_generator.video_calls[0]["duration_seconds"] == 9
        assert fake_generator.video_calls[0]["resolution"] == "720p"

    @pytest.mark.unit
    async def test_video_resolve_failure_fails_task_without_fallback(self, monkeypatch, tmp_path):
        """视频解析失败即任务失败：异常原样上抛留痕，无硬编码 provider/model 兜底，后端不被调用。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()

        async def _boom(*args, **kwargs):
            raise RuntimeError("video provider unconfigured")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _boom)

        with pytest.raises(RuntimeError, match="video provider unconfigured"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )
        assert fake_generator.video_calls == []

    @pytest.mark.unit
    async def test_tasks_declare_only_needed_lanes(self, monkeypatch, tmp_path):
        """任务只声明自己用到的 lane：图片类任务不声明 video/audio（只配置图片供应商的项目
        不因视频供应商缺配置失败，未声明 lane 不解析见 tests/server/test_generation_context.py），
        视频任务只声明 video；带参考图时 image lane 请求 i2i 能力。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        seen: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(fake_generator, seen_lane_requests=seen),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)

        # E1S02 引用角色/场景/道具 sheet → 带参考图 → i2i；character 带 reference_image → i2i
        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "画面"}
        )
        await generation_tasks.execute_character_task("demo", "Alice", {"prompt": "角色描述"})
        await generation_tasks.execute_scene_task("demo", "祠堂", {"prompt": "场景描述"})
        for req in seen:
            assert req["image"] is not None
            assert req["video"] is None
            assert req["audio"] is None
        assert seen[0]["image"].capability == "i2i"

        seen.clear()
        await generation_tasks.execute_video_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 8,
            },
        )
        assert len(seen) == 1
        assert seen[0]["video"] is not None
        assert seen[0]["image"] is None
        assert seen[0]["audio"] is None

    @pytest.mark.unit
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

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("script", "expected_entity_type", "expected_label"),
        [
            pytest.param(
                {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]},
                "drama_scene",
                "场景「E1S01」",
                id="drama-scenes",
            ),
            pytest.param(
                {"content_mode": "ad", "shots": [{"shot_id": "E1S01"}]},
                "shot",
                "镜头「E1S01」",
                id="ad-shots",
            ),
            pytest.param(
                {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]},
                "segment",
                "分镜「E1S01」",
                id="narration-segments",
            ),
        ],
    )
    def test_emit_success_batch_storyboard_entity_type_follows_skeleton(
        self, monkeypatch, tmp_path, script, expected_entity_type, expected_label
    ):
        """storyboard/video 任务完成通知与分镜级事件同口径：实体类型与名词按项目剧本骨架
        种类解析，不恒为 narration 的 segment/「分镜」。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = script
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        for task_type, action in (("storyboard", "storyboard_ready"), ("video", "video_ready")):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E1S01",
                payload={"script_file": "ep01.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["entity_type"] == expected_entity_type
            assert change["action"] == action
            assert change["label"] == expected_label

    @pytest.mark.unit
    def test_emit_success_batch_reference_video_entity_type_aligns_with_frontend(self, monkeypatch, tmp_path):
        """参考生视频任务完成通知的 entity_type 需为前端联合类型认识的 "reference_unit"
        （而非仅本侧认识的 "reference_video_unit"），分组标题才能落「视频单元」而非「内容」
        兜底；条目文案仍沿用「参考视频」措辞，不随骨架名词改动。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "video_units": [{"unit_id": "U01"}]}
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label"] == "参考视频「U01」"

    @pytest.mark.unit
    def test_emit_success_batch_reference_video_ad_entity_type_not_shot(self, monkeypatch, tmp_path):
        """ad 剧本骨架恒为 shots[]，reference_video 路径派生的 video_unit 索引与 shots
        同存于一份剧本 JSON——resolve_script_kind 的数据形状判别会因 shots 键仍在而落回
        content_mode==ad→shots，与该任务实际对应 video_unit 资源不符，故需固定解析，
        不随骨架判别漂到 "shot"。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "U01", "shot_ids": ["E1S01"]}],
        }
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label"] == "参考视频「U01」"

    @pytest.mark.unit
    def test_emit_success_batch_reference_video_tts_entity_type_not_shot(self, monkeypatch, tmp_path):
        """TTS 任务与视频任务共用项目路线，ad 参考路线的混合骨架不能把 unit 事件分到 shot。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(content_mode="ad", generation_mode="reference_video")
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "E1U01"}],
        }
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="tts",
            project_name="demo",
            resource_id="E1U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "tts_ready"
        assert change["label"] == "旁白「E1U01」"

    @pytest.mark.unit
    def test_emit_success_batch_falls_back_to_segments_when_script_load_fails(self, monkeypatch, tmp_path):
        """骨架判定拿不到剧本（脚本缺失/损坏）时兜底 segments/「分镜」，不让通知发送中断。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)

        def _raise_load_script(project_name, script_file):
            raise FileNotFoundError(script_file)

        fake_pm.load_script = _raise_load_script
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "missing.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    @pytest.mark.unit
    def test_emit_success_batch_falls_back_to_segments_when_script_not_a_dict(self, monkeypatch, tmp_path):
        """剧本文件内容损坏成非 dict（如顶层数组）时兜底 segments/「分镜」，不让
        resolve_script_kind 内部的 .get() 调用抛 AttributeError 中断通知发送。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = ["not", "a", "dict"]
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "corrupted.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    @pytest.mark.unit
    def test_emit_success_batch_attaches_script_file_and_episode(self, monkeypatch, tmp_path):
        """骨架驱动的完成事件须挂 script_file 与 episode（供 episode 作用域消费方使用）——
        锁死这条挂载，防将来改动 emit 时静默丢字段。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "episode": 3, "segments": [{"segment_id": "E3S01"}]}
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        for task_type in ("storyboard", "video", "reference_video"):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E3S01",
                payload={"script_file": "ep03.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["script_file"] == "ep03.json"
            assert change["episode"] == 3

    @pytest.mark.unit
    def test_grid_fingerprints_include_split_cells(self, monkeypatch, tmp_path):
        """宫格指纹应包含切割覆写的 canonical 分镜图（cache-bust），但拒绝越出项目目录的路径"""
        from lib.grid.models import FrameCell, GridGeneration
        from lib.grid_manager import GridManager

        project_path = tmp_path / "demo"
        (project_path / "storyboards").mkdir(parents=True)
        (project_path / "grids").mkdir()
        (project_path / "grids" / "grid_0123456789ab.png").write_bytes(b"grid")
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"img2")
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"secret")

        grid = GridGeneration(
            id="grid_0123456789ab",
            episode=1,
            script_file="ep01.json",
            scene_ids=["E1S01"],
            grid_image_path="grids/grid_0123456789ab.png",
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

        fps = generation_tasks.compute_affected_fingerprints("demo", "grid", "grid_0123456789ab")

        assert "grids/grid_0123456789ab.png" in fps
        assert "storyboards/scene_E1S01.png" in fps
        assert "storyboards/scene_E1S02.png" in fps
        assert all("outside" not in key for key in fps)
        assert all(not key.startswith("/") for key in fps)

    @pytest.mark.unit
    async def test_execute_task_validation_errors(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_FakeGenerator()))

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


class TestGetAspectRatio:
    @pytest.mark.unit
    def test_reads_top_level_aspect_ratio(self):
        project = {"aspect_ratio": "16:9", "content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "storyboards") == "16:9"

    @pytest.mark.unit
    def test_fallback_to_content_mode_narration(self):
        project = {"content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "9:16"

    @pytest.mark.unit
    def test_fallback_to_content_mode_drama(self):
        project = {"content_mode": "drama"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"

    @pytest.mark.unit
    def test_characters_always_16_9(self):
        # 角色资产统一采用四视图横版，与项目整体画幅无关
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "characters") == "16:9"

    @pytest.mark.unit
    def test_scenes_and_props_always_16_9(self):
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "scenes") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "props") == "16:9"


def _ad_pm(project_path: Path, *, with_sheet: bool) -> _FakePM:
    """ad 项目 fixture：产品镜头 E1S02（引用保温杯）+ 氛围镜头 E1S01/E1S03。"""
    pm = _FakePM(project_path)
    pm.project["content_mode"] = "ad"
    if with_sheet:
        pm.project["products"]["保温杯"]["product_sheet"] = "products/保温杯.png"
    pm.script = {
        "content_mode": "ad",
        "shots": [
            {
                "shot_id": "E1S01",
                "section": "hook",
                "duration_seconds": 4,
                "voiceover_text": "开场",
                "characters_in_shot": ["Alice"],
                "scenes": ["祠堂"],
                "props": [],
                "products_in_shot": [],
                "image_prompt": "氛围开场",
            },
            {
                "shot_id": "E1S02",
                "section": "product_reveal",
                "duration_seconds": 4,
                "voiceover_text": "产品亮相",
                "characters_in_shot": ["Alice"],
                "scenes": ["祠堂"],
                "props": [],
                "products_in_shot": ["保温杯"],
                "image_prompt": "产品特写",
            },
        ],
    }
    return pm


def _ref_paths(refs: list) -> list:
    return [r["image"] if isinstance(r, dict) else r for r in refs]


class TestAdProductFidelityStoryboard:
    """产品保真注入二元化——分镜层。"""

    def _patch(self, monkeypatch, pm, generator):
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(generator))

    @pytest.mark.unit
    async def test_product_shot_injects_sheet_then_originals_before_other_sheets(self, tmp_path, monkeypatch):
        """有确认 sheet 的产品镜头：注入集为「sheet 多角度 + 原图压阵」，排序绝对优先于角色/场景 sheet。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = _ad_pm(project_path, with_sheet=True)
        generator = _FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "产品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        # 产品参考全量注入且排首位：sheet 在前、原图压阵，先于角色/场景 sheet
        assert [reference["kind"] for reference in refs[:2]] == ["sheet", "original"]
        assert [path.name for path in paths[:2]] == ["0000-保温杯.png", "0001-保温杯_1.jpg"]
        assert generator.image_reference_bytes[0][:2] == [b"png", b"jpg"]
        # 既有装配照常跟在产品参考之后（角色/场景 sheet + 上一分镜衔接参考）
        assert {reference.get("label") for reference in refs[2:] if isinstance(reference, dict)} >= {"Alice", "祠堂"}
        # 产品参考带可读标签（供支持 label 的后端内联）
        assert all(isinstance(r, dict) and "保温杯" in r["label"] for r in refs[:2])
        # 附高保真还原指令
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith("产品特写")
        assert "「保温杯」" in prompt

    @pytest.mark.unit
    async def test_product_shot_without_sheet_injects_originals_directly(self, tmp_path, monkeypatch):
        """无 sheet 的产品镜头：原图直注、仍排首位；声明但缺失的原图跳过。"""
        project_path = _prepare_files(tmp_path)
        pm = _ad_pm(project_path, with_sheet=False)
        generator = _FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "产品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        assert refs[0]["kind"] == "original"
        assert paths[0].name == "0000-保温杯_1.jpg"
        assert generator.image_reference_bytes[0][0] == b"jpg"
        # 全量注入 = 存在的原图都进；声明的 missing.jpg 不指向任何文件，不出现
        assert all("missing" not in str(p) for p in paths)
        assert "「保温杯」" in generator.image_calls[0]["prompt"]

    @pytest.mark.unit
    async def test_fidelity_instruction_only_names_products_with_injected_references(self, tmp_path, monkeypatch):
        """指令点名的产品与实际注入参考的产品一致：图全缺的产品不被指令点名（避免指向不存在的参考）。"""
        project_path = _prepare_files(tmp_path)
        pm = _ad_pm(project_path, with_sheet=False)
        pm.project["products"]["杯刷"] = {
            "description": "配套杯刷",
            "product_sheet": "",
            "brand": "",
            "reference_images": ["products/refs/不存在.jpg"],
            "selling_points": [],
        }
        pm.script["shots"][1]["products_in_shot"] = ["保温杯", "杯刷"]
        generator = _FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "双产品同框"}
        )

        prompt = generator.image_calls[0]["prompt"]
        assert "「保温杯」" in prompt
        assert "「杯刷」" not in prompt

    @pytest.mark.unit
    async def test_atmosphere_shot_zero_product_images(self, tmp_path, monkeypatch):
        """氛围镜头（products_in_shot 为空）：零产品图，场景/角色 sheet 照常注入，prompt 无保真指令。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = _ad_pm(project_path, with_sheet=True)
        generator = _FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "氛围开场"}
        )

        refs = generator.image_calls[0]["reference_images"]
        assert [reference["label"] for reference in refs] == ["Alice", "祠堂"]
        assert generator.image_reference_bytes[0] == [b"png", b"png"]
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith("氛围开场")
        assert "产品高保真还原" not in prompt

    @pytest.mark.unit
    def test_collect_shot_product_references_skips_non_list_products_in_shot(self, tmp_path):
        """products_in_shot 为 str/dict 等非列表脏数据：跳过不抛，零产品参考（str 不得被逐字符迭代）。"""
        project_path = _prepare_files(tmp_path)
        project = {"products": {"保温杯": {"reference_images": ["products/refs/保温杯_1.jpg"]}}}

        for dirty in ("保温杯", {"保温杯": True}, 7):
            item = {"shot_id": "E1S02", "products_in_shot": dirty}
            assert generation_tasks._collect_shot_product_references(project, project_path, item) == []

        # 缺失 / None / 空列表是氛围镜头的正常表达，同样返回空列表
        for empty in (None, []):
            item = {"shot_id": "E1S01", "products_in_shot": empty}
            assert generation_tasks._collect_shot_product_references(project, project_path, item) == []
        assert generation_tasks._collect_shot_product_references(project, project_path, {"shot_id": "E1S01"}) == []

    @pytest.mark.integration
    def test_collect_product_references_resolves_nfd_registered_name_by_nfc_query(self, tmp_path):
        """产品以 NFD key 登记、镜头 products_in_shot 传入 NFC 名字：
        collect_product_references_for_names 须按归一形式查找 bucket 命中，不能因编码
        形式不同静默跳过。"""
        import unicodedata

        project_path = _prepare_files(tmp_path)
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd
        project = {"products": {name_nfd: {"reference_images": ["products/refs/保温杯_1.jpg"]}}}

        refs = generation_tasks.collect_product_references_for_names(project, project_path, [name_nfc])
        assert [r["image"] for r in refs] == [project_path / "products" / "refs" / "保温杯_1.jpg"]

    @pytest.mark.integration
    def test_collect_product_references_dedupes_nfc_nfd_pair(self, tmp_path):
        """同一产品的 NFC/NFD 两种编码形式同时出现在 products_in_shot：归一后是同一产品，
        只应注入一份参考图，不能各自命中同一 bucket 条目各注入一份，否则会重复消耗参考位、
        挤掉真正的角色/场景参考。"""
        import unicodedata

        project_path = _prepare_files(tmp_path)
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd
        project = {"products": {name_nfd: {"reference_images": ["products/refs/保温杯_1.jpg"]}}}

        refs = generation_tasks.collect_product_references_for_names(project, project_path, [name_nfc, name_nfd])
        assert [r["image"] for r in refs] == [project_path / "products" / "refs" / "保温杯_1.jpg"]


def _patch_video_path(monkeypatch, pm, generator):
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(generator))
    monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", _async_return(None))
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)


@pytest.mark.integration
class TestAdProductVideoRequest:
    """产品镜头的视频请求走纯图生视频：分镜图作首帧，不带参考图。"""

    async def test_product_shot_video_request_carries_no_reference_images(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
        pm = _ad_pm(project_path, with_sheet=True)
        generator = _FakeGenerator()
        _patch_video_path(monkeypatch, pm, generator)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S02",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "举起保温杯", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 4,
            },
        )

        assert result["resource_type"] == "videos"
        call = generator.video_calls[0]
        assert "reference_images" not in call
        assert call["start_image"] == project_path / "storyboards" / "scene_E1S02.png"
        # 参考缺席时不附产品保真指令（指令指向参考图，会误导模型）
        assert "高保真" not in call["prompt"]
