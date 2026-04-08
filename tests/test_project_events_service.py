import asyncio
import json

import pytest

from lib.project_change_hints import emit_project_change_batch, project_change_source
from lib.project_manager import ProjectManager
from server.services.project_events import ProjectEventService


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
                    "title": "Episode 1",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "clues_in_segment": [],
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
            )

        service = ProjectEventService(tmp_path)
        previous = service._build_snapshot("demo")

        project = pm.load_project("demo")
        project["characters"]["Hero"] = {
            "description": "protagonist",
            "voice_style": "calm",
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
            pm.save_script("demo", script, "episode_1.json")

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
                    "title": "Episode 1",
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "duration_seconds": 4,
                            "segment_break": False,
                            "characters_in_segment": [],
                            "clues_in_segment": [],
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
                "clues_in_segment": [],
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
            pm.save_script("demo", script, "episode_1.json")

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
        queue, snapshot = await service.subscribe("demo")

        assert snapshot["project_name"] == "demo"

        script_path = pm.get_project_path("demo") / "scripts" / "episode_2.json"
        script_path.write_text(
            json.dumps(
                {
                    "episode": 2,
                    "title": "Episode 2",
                    "content_mode": "narration",
                    "segments": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        event_name, payload = await asyncio.wait_for(queue.get(), timeout=1.5)
        assert event_name == "changes"
        assert payload["source"] == "filesystem"
        assert any(
            change["entity_type"] == "episode" and change["action"] == "created" and change["episode"] == 2
            for change in payload["changes"]
        )
        assert any(episode["episode"] == 2 for episode in pm.load_project("demo")["episodes"])

        await service.unsubscribe("demo", queue)
        await service.shutdown()

    @pytest.mark.asyncio
    async def test_emitted_batch_is_broadcast_without_waiting_for_snapshot_diff(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        service = ProjectEventService(tmp_path, poll_interval=1.0)
        await service.start()
        queue, snapshot = await service.subscribe("demo")

        assert snapshot["fingerprint"]

        emit_project_change_batch(
            "demo",
            [
                {
                    "entity_type": "segment",
                    "action": "storyboard_ready",
                    "entity_id": "E1S01",
                    "label": "Storyboard E1S01",
                    "focus": None,
                    "important": True,
                }
            ],
            source="worker",
        )

        event_name, payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event_name == "changes"
        assert payload["source"] == "worker"
        assert payload["fingerprint"] == snapshot["fingerprint"]
        assert payload["changes"][0]["action"] == "storyboard_ready"

        await service.unsubscribe("demo", queue)
        await service.shutdown()
