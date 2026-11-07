"""Episode IDs that have appeared in task and call records.

Neither table has a dedicated episode-ID column: IDs appear in resource IDs
(``E3S01`` / ``E3U02`` / ``episode-3``), script paths (``scripts/episode_3.json``),
artifact paths and generation inputs. The project's historical maximum must include
these, otherwise reused IDs make new episodes claim old costs and tasks. Asset-task
resource IDs are asset names and excluded: asset images, derivative images, voice previews,
and image edits targeting assets.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.episode.episode_ids import episode_ids_in_names, episode_ids_in_record
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from lib.project.asset_types import ASSET_SPECS

_ASSET_TASK_TYPES = frozenset({*ASSET_SPECS, DERIVATIVE_TASK_TYPE, "voice_sample"})


async def max_recorded_episode_id(session: AsyncSession, project_name: str) -> int:
    """Maximum episode ID appearing in the project's task/call records; 0 when no records exist."""

    names: list[str] = []
    recorded: set[int] = set()
    asset_task_ids: set[str] = set()
    tasks = await session.execute(
        select(
            Task.task_id,
            Task.task_type,
            Task.resource_type,
            Task.resource_id,
            Task.script_file,
            Task.payload_json,
            Task.result_json,
            Task.execution_checkpoint_json,
        ).where(Task.project_name == project_name)
    )
    for task_id, task_type, resource_type, resource_id, script_file, *documents in tasks:
        # Asset and storyboard image edits share one task type; resource_type identifies the resource kind being edited
        if (resource_type if task_type == "image_edit" else task_type) in _ASSET_TASK_TYPES:
            asset_task_ids.add(task_id)
            resource_id = None
        names.extend(value for value in (resource_id, script_file) if value)
        for document in documents:
            if document:
                try:
                    recorded.update(episode_ids_in_record(json.loads(document)))
                except ValueError:
                    continue
    calls = await session.execute(
        select(ApiCall.task_id, ApiCall.segment_id, ApiCall.output_path, ApiCall.inputs).where(
            ApiCall.project_name == project_name
        )
    )
    for task_id, segment_id, output_path, inputs in calls:
        if task_id in asset_task_ids:
            segment_id = None
        names.extend(value for value in (segment_id, output_path) if value)
        recorded.update(episode_ids_in_record(inputs))
    return max(recorded | episode_ids_in_names(names), default=0)


__all__ = ["max_recorded_episode_id"]
