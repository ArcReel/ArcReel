"""Whether an episode has queued/running tasks: shared Web/Agent checks before changes remove or retire derived episodes."""

from __future__ import annotations

from lib.episode.episode_ids import episode_ids_in_record
from lib.generation.generation_queue import GenerationQueue


async def episode_has_active_tasks(queue: GenerationQueue, project_name: str, episode: int) -> bool:
    """Whether this episode has queued/running tasks: its ID appears in task resources, script files, or payloads."""
    for status in ("queued", "running"):
        page = 1
        while True:
            listing = await queue.list_tasks(project_name=project_name, status=status, page=page, page_size=200)
            items = listing.get("items") or []
            for task in items:
                record = {key: task.get(key) for key in ("resource_id", "script_file", "payload")}
                if episode in episode_ids_in_record(record):
                    return True
            if len(items) < 200:
                break
            page += 1
    return False


__all__ = ["episode_has_active_tasks"]
