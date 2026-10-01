"""一集有没有排队或执行中的任务：改动要移除或退下切出集之前的检查，Web 路由与 Agent 工具共用。"""

from __future__ import annotations

from lib.episode.episode_ids import episode_ids_in_record
from lib.generation.generation_queue import GenerationQueue


async def episode_has_active_tasks(queue: GenerationQueue, project_name: str, episode: int) -> bool:
    """这一集是否有排队或执行中的任务：任务的资源、剧本文件或载荷带这一集的集 ID。"""
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
