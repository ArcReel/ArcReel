"""Active narration-audio task observation shared by transport entry points."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import PurePosixPath

from lib.db.base import DEFAULT_USER_ID
from lib.generation.generation_queue import GenerationQueue, get_generation_queue


async def active_tts_resource_ids(
    *,
    project_name: str,
    resource_ids: Iterable[str],
    script_file: str,
    queue: GenerationQueue | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> frozenset[str]:
    """Return units with active explicit TTS for one script's equivalent locators."""

    normalized = list(dict.fromkeys(resource_id for resource_id in resource_ids if resource_id))
    if not normalized:
        return frozenset()
    normalized_script = str(PurePosixPath(script_file.replace("\\", "/")))
    basename = PurePosixPath(normalized_script).name
    if not basename or basename == ".":
        raise ValueError("script_file must identify a script")
    locators = tuple(dict.fromkeys((normalized_script, basename, f"scripts/{basename}")))
    queue = queue or get_generation_queue()
    active_batches = await asyncio.gather(
        *(
            queue.get_active_tasks_for_resources(
                project_name=project_name,
                task_type="tts",
                resource_ids=normalized,
                script_file=locator,
                user_id=user_id,
            )
            for locator in locators
        )
    )
    return frozenset(str(task.get("resource_id") or "") for batch in active_batches for task in batch)


__all__ = [
    "active_tts_resource_ids",
]
