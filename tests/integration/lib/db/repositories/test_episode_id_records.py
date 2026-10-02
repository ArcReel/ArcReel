"""任务与调用记录里出现过的最大集 ID：项目历史最高号的数据库侧来源。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from lib.backends.providers import CallStatus
from lib.db.base import DEFAULT_USER_ID
from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.db.repositories.episode_id_records import max_recorded_episode_id
from lib.db.repositories.usage_repo import UsageRepository
from lib.episode.episode_ids import allocate_episode_ids
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from tests.legacy_project_shapes import write_legacy_episode_id_remnants_project

_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _task(
    task_id: str,
    *,
    project_name: str,
    resource_id: str,
    script_file: str | None = None,
    task_type: str = "video",
    media_type: str = "video",
    resource_type: str | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        project_name=project_name,
        task_type=task_type,
        media_type=media_type,
        resource_id=resource_id,
        resource_type=resource_type,
        script_file=script_file,
        status="succeeded",
        queued_at=_NOW,
        updated_at=_NOW,
    )


def _call(
    *,
    project_name: str,
    segment_id: str | None = None,
    output_path: str | None = None,
    call_type: str = "video",
    task_id: str | None = None,
) -> ApiCall:
    return ApiCall(
        project_name=project_name,
        call_type=call_type,
        task_id=task_id,
        model="m",
        provider="gemini",
        status=CallStatus.SUCCESS,
        started_at=_NOW,
        segment_id=segment_id,
        output_path=output_path,
        user_id=DEFAULT_USER_ID,
    )


async def test_max_recorded_episode_id_reads_tasks_and_calls_of_one_project(async_session) -> None:
    async_session.add_all(
        [
            _task("t1", project_name="demo", resource_id="E2S01", script_file="scripts/episode_2.json"),
            _task("t2", project_name="demo", resource_id="episode-6"),
            _task("t3", project_name="other", resource_id="E40S01"),
            _call(project_name="demo", segment_id="E3S02"),
            _call(project_name="demo", output_path="reference_videos/E11U01.mp4"),
            _call(project_name="other", segment_id="E50S01"),
        ]
    )
    await async_session.commit()

    assert await max_recorded_episode_id(async_session, "demo") == 11
    assert await max_recorded_episode_id(async_session, "missing") == 0


async def test_asset_names_shaped_like_item_ids_do_not_count(async_session) -> None:
    asset = "E12345678901234567A1"
    async_session.add_all(
        [
            _task("t1", project_name="demo", resource_id="E2S01"),
            _task("sheet", project_name="demo", resource_id=asset, task_type="character", media_type="image"),
            _task("voice", project_name="demo", resource_id=asset, task_type="voice_sample", media_type="audio"),
            _call(project_name="demo", call_type="audio", segment_id=asset, task_id="voice"),
            _task(
                "edit-sheet",
                project_name="demo",
                resource_id=asset,
                task_type="image_edit",
                media_type="image",
                resource_type="character",
            ),
            _call(project_name="demo", call_type="image", segment_id=asset, task_id="edit-sheet"),
            _task(
                "edit-shot",
                project_name="demo",
                resource_id="E3S01",
                task_type="image_edit",
                media_type="image",
                resource_type="storyboard",
            ),
        ]
    )
    await async_session.commit()

    assert await max_recorded_episode_id(async_session, "demo") == 3


async def test_migration_reserves_ids_only_retained_in_task_payloads_and_call_inputs(async_session, tmp_path) -> None:
    project_dir = write_legacy_episode_id_remnants_project(tmp_path / "projects")
    task = _task("legacy-task", project_name=project_dir.name, resource_id="E1S01")
    task.payload_json = json.dumps({"start_image": "storyboards/scene_E77S01.png"})
    task.result_json = json.dumps({"path": "videos/scene_E78S01.mp4"})
    task.execution_checkpoint_json = json.dumps({"artifact_episode": 79})
    async_session.add(task)
    await UsageRepository(async_session).start_call(
        project_name=project_dir.name,
        call_type="video",
        model="m",
        segment_id="E1S01",
        inputs={"end_image": "storyboards/scene_E99S01.png", "parameters": {"prompt": "E999S01"}},
    )
    await async_session.commit()
    recorded = await max_recorded_episode_id(async_session, project_dir.name)
    assert recorded == 99

    await asyncio.to_thread(migrate_project_dir, project_dir, recorded_episode_ids=lambda _name: recorded)
    project = ProjectManager.for_project_dir(project_dir).load_project(project_dir.name)
    assert allocate_episode_ids(project, 1) == [100]
