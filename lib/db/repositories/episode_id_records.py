"""任务与调用记录里出现过的集 ID。

集 ID 在这两张表里没有独立列，只藏在资源 ID（``E3S01`` / ``E3U02`` / ``episode-3``）、剧本路径
（``scripts/episode_3.json``）、产物路径与生成输入里。项目历史最高号须覆盖它们，否则复用的集 ID 会让新集
认领旧集的费用与任务。资产类任务（资产图、衍生资产图、试听音，以及改图对象是资产的改图）的资源 ID 是资产名，不算。
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
    """项目在任务与调用记录里出现过的最大集 ID；没有记录时为 0。"""

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
        # 改图任务跨资产与分镜共用一个任务类型，改的是哪类资源由 resource_type 指明
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
