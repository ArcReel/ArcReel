import asyncio
import json

import pytest

from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from server.services.project_events import ProjectEventService


async def _next_event(stream, *, timeout: float) -> tuple[str, dict]:
    """Pull the next real (event_name, payload) tuple, skipping ``_idle`` sentinels."""

    async def _pull() -> tuple[str, dict]:
        async for item in stream:
            if isinstance(item, dict):
                if item.get("type") == "_idle":
                    continue
                raise AssertionError(f"unexpected dict sentinel: {item}")
            return item
        raise AssertionError("stream ended before a real event arrived")

    return await asyncio.wait_for(_pull(), timeout=timeout)


class TestProjectEventService:
    def test_diff_snapshots_reports_character_and_storyboard_changes(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {
                    "episode": 1,
                    "title": "第一集",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": {
                                "storyboard_image": None,
                                "video_clip": None,
                                "video_uri": None,
                                "status": "pending",
                            },
                        }
                    ],
                },
                "episode_1.json",
                validate=False,  # 事件 diff 测试用简化替身剧本
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("demo")

        project = pm.load_project("demo")
        project["characters"]["Hero"] = {
            "description": "主角",
            "voice_style": "冷静",
            "character_sheet": "",
            "reference_image": "",
        }
        with project_change_source("filesystem"):
            pm.save_project("demo", project)

        script = pm.load_script("demo", "episode_1.json")
        segment = script["segments"][0]
        segment["image_prompt"] = "new"
        segment["generated_assets"]["storyboard_image"] = "storyboards/scene_E1S01.png"
        segment["generated_assets"]["status"] = "storyboard_ready"
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        current = service._build_snapshot("demo")
        changes = service._diff_snapshots(previous, current)

        assert any(change["entity_type"] == "character" and change["action"] == "created" for change in changes)
        assert any(change["action"] == "storyboard_ready" for change in changes)
        assert any(change["entity_type"] == "segment" and change["action"] == "updated" for change in changes)

    def test_diff_snapshots_reports_project_metadata_and_new_segments(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        with project_change_source("filesystem"):
            pm.save_script(
                "demo",
                {
                    "episode": 1,
                    "title": "第一集",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "scenes": [],
                            "props": [],
                            "image_prompt": "old",
                            "video_prompt": "old",
                            "generated_assets": {
                                "storyboard_image": None,
                                "video_clip": None,
                                "video_uri": None,
                                "status": "pending",
                            },
                        }
                    ],
                },
                "episode_1.json",
                validate=False,  # 事件 diff 测试用简化替身剧本
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("demo")

        project = pm.load_project("demo")
        project["title"] = "Demo Updated"
        project["style_description"] = "moody lighting"
        with project_change_source("filesystem"):
            pm.save_project("demo", project)

        script = pm.load_script("demo", "episode_1.json")
        script["segments"].append(
            {
                "segment_id": "E1S02",
                "duration_seconds": 4,
                "segment_break": False,
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": "new",
                "video_prompt": "new",
                "generated_assets": {
                    "storyboard_image": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            }
        )
        with project_change_source("filesystem"):
            pm.save_script("demo", script, "episode_1.json", validate=False)

        current = service._build_snapshot("demo")
        changes = service._diff_snapshots(previous, current)

        assert any(change["entity_type"] == "project" and change["action"] == "updated" for change in changes)
        assert any(
            change["entity_type"] == "segment" and change["action"] == "created" and change["entity_id"] == "E1S02"
            for change in changes
        )

    @pytest.mark.asyncio
    async def test_poll_detects_direct_script_write_and_syncs_episode_index(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            # 首个事件是 snapshot 元组。
            first = await anext(stream)
            assert first[0] == "snapshot"
            assert first[1]["project_name"] == "demo"

            script_path = pm.get_project_path("demo") / "scripts" / "episode_2.json"
            script_path.write_text(
                json.dumps(
                    {
                        "episode": 2,
                        "title": "第二集",
                        "content_mode": "narration",
                        "segments": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            event_name, payload = await _next_event(stream, timeout=1.5)
            assert event_name == "changes"
            assert payload["source"] == "filesystem"
            assert any(
                change["entity_type"] == "episode" and change["action"] == "created" and change["episode"] == 2
                for change in payload["changes"]
            )
            assert any(episode["episode"] == 2 for episode in pm.load_project("demo")["episodes"])

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_is_broadcast_without_waiting_for_snapshot_diff(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()

        async with service.stream_events("demo", idle_timeout=0.1) as stream:
            event_name, snapshot = await anext(stream)
            assert event_name == "snapshot"
            assert snapshot["fingerprint"]

            emit_project_change_batch(
                "demo",
                [
                    {
                        "entity_type": "segment",
                        "action": "storyboard_ready",
                        "entity_id": "E1S01",
                        "label": "分镜「E1S01」",
                        "focus": None,
                        "important": True,
                    }
                ],
                source="worker",
            )

            event_name, payload = await _next_event(stream, timeout=1.0)
            assert event_name == "changes"
            assert payload["source"] == "worker"
            assert payload["fingerprint"] == snapshot["fingerprint"]
            assert payload["changes"][0]["action"] == "storyboard_ready"

        await service.shutdown()

    @pytest.mark.asyncio
    async def test_subscribe_cancellation_cleans_up_subscriber(self, tmp_path, monkeypatch):
        """客户端在首次扫描期间断开 → _subscribe 被取消 → 订阅者与 watch task 不泄漏。"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, poll_interval=0.05)
        await service.start()

        # 模拟首次扫描卡住:watch task 永不 set ready_event,_subscribe 会 park 在 wait()。
        async def _never_ready(project_name, channel):
            await asyncio.sleep(3600)

        monkeypatch.setattr(service, "_watch_project", _never_ready)

        task = asyncio.create_task(service._subscribe("demo"))
        await asyncio.sleep(0.05)  # 让 _subscribe 注册 queue 并 park
        channel = service._channels["demo"]
        assert channel.subscribers  # 已注册
        watch_task = channel.task

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 取消后:订阅者被清理、channel 被弹出、watch task 被取消(不泄漏)。
        assert "demo" not in service._channels
        await asyncio.sleep(0)  # 让 watch task 的取消落定
        assert watch_task.cancelled() or watch_task.done()

        await service.shutdown()

    def test_projects_root_kwarg_overrides_default_subdir(self, tmp_path):
        """显式传 projects_root 时，service.pm 走该目录而非 project_root/'projects'。

        覆盖 ARCREEL_DATA_DIR 场景：app.py 启动时传 ``app_data_dir()`` 进来，
        事件监听应跟着切换，不能继续指向旧的 ``project_root/projects``。
        """
        custom_projects = tmp_path / "external-data"
        pm = ProjectManager(custom_projects)
        pm.create_project("demo")

        service = ProjectEventService(tmp_path, projects_root=custom_projects)

        assert service.pm.projects_root == custom_projects.resolve()
        assert service.pm.get_project_path("demo") == (custom_projects / "demo").resolve()
