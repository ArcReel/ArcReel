"""任务与调用记录里出现过的集 ID。

集 ID 在这两张表里没有独立列，只藏在资源 ID（``E3S01`` / ``E3U02`` / ``episode-3``）、剧本路径
（``scripts/episode_3.json``）、产物路径与生成输入里。项目历史最高号须覆盖它们，否则复用的集 ID 会让新集
认领旧集的费用与任务。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.api_call import ApiCall
from lib.db.models.task import Task
from lib.episode.episode_ids import episode_ids_in_names, episode_ids_in_record


async def max_recorded_episode_id(session: AsyncSession, project_name: str) -> int:
    """项目在任务与调用记录里出现过的最大集 ID；没有记录时为 0。"""

    names: list[str] = []
    recorded: set[int] = set()
    tasks = await session.execute(
        select(
            Task.resource_id, Task.script_file, Task.payload_json, Task.result_json, Task.execution_checkpoint_json
        ).where(Task.project_name == project_name)
    )
    for resource_id, script_file, *documents in tasks:
        names.extend(value for value in (resource_id, script_file) if value)
        for document in documents:
            if document:
                try:
                    recorded.update(episode_ids_in_record(json.loads(document)))
                except ValueError:
                    continue
    calls = await session.execute(
        select(ApiCall.segment_id, ApiCall.output_path, ApiCall.inputs).where(ApiCall.project_name == project_name)
    )
    for segment_id, output_path, inputs in calls:
        names.extend(value for value in (segment_id, output_path) if value)
        recorded.update(episode_ids_in_record(inputs))
    return max(recorded | episode_ids_in_names(names), default=0)


__all__ = ["max_recorded_episode_id"]
