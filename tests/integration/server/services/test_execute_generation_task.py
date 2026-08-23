"""Tests for execute_generation_task (split from test_generation_tasks_service.py)."""

import threading

import pytest

from lib.generation_queue import CompensableGenerationResult
from lib.storyboard_sequence import (
    PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION,
    PREVIOUS_STORYBOARD_REFERENCE_LABEL,
)
from server.services import generation_tasks
from tests.integration.server.services.conftest import (
    _fake_resolve_ctx,
    _FakeGenerator,
    _FakePM,
    _prepare_files,
    _register_asset_sheet_claims,
    _seed_current_storyboard,
)


class TestGenerationTasks:
    async def test_execute_task_dispatch(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _register_asset_sheet_claims(fake_pm)
        _seed_current_storyboard(fake_pm)
        fake_generator = _FakeGenerator(project_path)
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
