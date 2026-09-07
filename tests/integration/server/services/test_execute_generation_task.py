"""Tests for execute_generation_task."""

import asyncio
import threading

import pytest

from lib.generation_queue import CompensableGenerationResult
from lib.storyboard_sequence import StoryboardImageBindingRequired
from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    async_return,
    fake_resolve_ctx,
    prepare_files,
    register_asset_sheet_claims,
    seed_current_storyboard,
)


class TestGenerationTasks:
    async def test_execute_task_dispatch(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        register_asset_sheet_claims(fake_pm)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator(project_path)
        emitted_batches = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(fake_generator))
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
        # 参考图只按数组序位传输、不带任何标签；身份由 prompt 内的 Reference_Images 声明行按「图N」指认。
        # provider 收到的是任务私有快照，extra 仍保持裸 Path。
        assert [sorted(ref) if isinstance(ref, dict) else None for ref in storyboard_refs] == [
            ["image"],
            ["image"],
            ["image"],
            None,
            ["image"],
        ]
        assert all(
            not (ref["image"] if isinstance(ref, dict) else ref).is_relative_to(project_path) for ref in storyboard_refs
        )
        assert fake_generator.image_reference_bytes[0] == [b"png"] * 5
        assert fake_generator.image_calls[0]["prompt"] == (
            "Visual style: cinematic\n\n"
            "Style: Anime\n"
            "Reference_Images: 图1为角色参考图；图2为场景参考图；图3为道具参考图；图4为补充参考图；"
            "图5为上一分镜图，只参考构图与色调。\n"
            "Scene: 在雨夜街道\n"
            "Composition:\n  shot_type: Medium Shot\n  lighting: 暖光\n  ambiance: 薄雾\n"
            "Avoid: 水印、多余文字、Logo"
        )

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S03",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )
        assert [ref["image"].name for ref in fake_generator.image_calls[1]["reference_images"]] == [
            "0000-Alice.png",
            "0001-祠堂.png",
            "0002-玉佩.png",
        ]
        assert fake_generator.image_reference_bytes[1] == [b"png"] * 3
        assert (
            "Reference_Images: 图1为角色参考图；图2为场景参考图；图3为道具参考图。\n"
            in (fake_generator.image_calls[1]["prompt"])
        )

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

        with pytest.raises(ValueError, match=r"unsupported task_type: unknown"):
            await generation_tasks.execute_generation_task(
                {"task_type": "unknown", "project_name": "demo", "resource_id": "x", "payload": {}}
            )

    async def test_reused_video_result_emits_the_normal_generation_success_event(self, tmp_path, monkeypatch):
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
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: _FakePM(prepare_files(tmp_path)))
        monkeypatch.setitem(generation_tasks._TASK_EXECUTORS, "video", _executor)
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: emitted.append({"project_name": project_name, "changes": list(changes)}),
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
        assert len(emitted) == 1
        assert emitted[0]["project_name"] == "demo"
        change = emitted[0]["changes"][0]
        assert change["entity_type"] == "segment"
        assert change["action"] == "video_ready"
        assert change["entity_id"] == "E1S01"
        assert change["script_file"] == "episode_1.json"

    async def test_generation_event_failure_keeps_committed_media(self, tmp_path, monkeypatch):
        """事件发送失败被吞在通知边界内：产物已落盘，不因通知失败回撤，任务照常返回。"""
        compensation_threads: list[int] = []
        result_payload = {"resource_type": "storyboards", "resource_id": "E1S01"}

        async def _executor(*_args, **_kwargs):
            return CompensableGenerationResult(
                result_payload,
                cancel_compensation=lambda: compensation_threads.append(threading.get_ident()),
            )

        def _fail_emit(_project_name, _changes):
            raise RuntimeError("event emission failed")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: _FakePM(prepare_files(tmp_path)))
        monkeypatch.setitem(generation_tasks._TASK_EXECUTORS, "storyboard", _executor)
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", _fail_emit)

        result = await generation_tasks.execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "E1S01",
                "payload": {},
            }
        )

        assert result == result_payload
        assert compensation_threads == []

    async def test_cancellation_during_event_runs_media_compensation_off_the_event_loop(self, tmp_path, monkeypatch):
        """取消穿透通知边界（BaseException 不被吞）：补偿跑在事件循环之外，不阻塞循环。"""
        event_loop_thread = threading.get_ident()
        compensation_threads: list[int] = []

        async def _executor(*_args, **_kwargs):
            return CompensableGenerationResult(
                {"resource_type": "storyboards", "resource_id": "E1S01"},
                cancel_compensation=lambda: compensation_threads.append(threading.get_ident()),
            )

        def _cancel_emit(_project_name, _changes):
            raise asyncio.CancelledError

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: _FakePM(prepare_files(tmp_path)))
        monkeypatch.setitem(generation_tasks._TASK_EXECUTORS, "storyboard", _executor)
        monkeypatch.setattr(generation_tasks, "emit_project_change_batch", _cancel_emit)

        with pytest.raises(asyncio.CancelledError):
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

    async def test_execute_task_validation_errors(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(FakeGenerator()))

        with pytest.raises(ValueError, match=r"script_file is required for storyboard task"):
            await generation_tasks.execute_storyboard_task("demo", "E1S01", {"prompt": "x"})

        with pytest.raises(ValueError, match=r"current script unit is missing video_prompt"):
            await generation_tasks.execute_video_task("demo", "E1S01", {"script_file": "episode_1.json"})

        (project_path / "storyboards" / "scene_E1S01.png").unlink()
        with pytest.raises(StoryboardImageBindingRequired, match=r"storyboard binding missing"):
            await generation_tasks.execute_video_task("demo", "E1S01", {"script_file": "episode_1.json", "prompt": "x"})

        with pytest.raises(ValueError, match=r"prompt is required for character task"):
            await generation_tasks.execute_character_task("demo", "Alice", {"prompt": ""})

        with pytest.raises(ValueError, match=r"prompt is required for scene task"):
            await generation_tasks.execute_scene_task("demo", "祠堂", {"prompt": ""})

        with pytest.raises(ValueError, match=r"prompt is required for prop task"):
            await generation_tasks.execute_prop_task("demo", "玉佩", {"prompt": ""})

    async def test_tasks_declare_only_needed_lanes(self, monkeypatch, tmp_path):
        """任务只声明自己用到的 lane：图片类任务不声明 video/audio（只配置图片供应商的项目
        不因视频供应商缺配置失败，未声明 lane 不解析见 tests/server/test_generation_context.py），
        视频任务只声明 video；带参考图时 image lane 请求 i2i 能力。"""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        register_asset_sheet_claims(fake_pm)
        seed_current_storyboard(fake_pm)
        fake_generator = FakeGenerator(project_path)
        seen: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            fake_resolve_ctx(fake_generator, seen_lane_requests=seen),
        )
        monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", async_return(None))
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
