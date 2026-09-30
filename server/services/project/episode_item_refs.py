"""集页面之外的条目指称：给任务与调用记录附上条目所属集的标题与播出位置。

任务与调用记录只存条目 ID（``E3S01``），其中的集 ID 不给创作者看；界面凭这里附上的结构拼出
「标题 · S01」。记录可能跨项目，每个项目的账本只读一次。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from lib.episode.episode_ids import episode_item_ref, item_id_episode
from lib.i18n import _
from lib.project.project_manager import ProjectManager
from server.services.project.episode_display import present_episode_diagnostics


def with_episode_item_refs(
    items: Iterable[Mapping[str, Any]],
    *,
    id_field: str,
    ref_field: str,
    projects: ProjectManager,
    translate: Callable[..., str] = _,
) -> list[dict[str, Any]]:
    """返回附上 ``ref_field`` 的副本；项目读不到、ID 不带集前缀或集已不在账本里时为 None。"""

    ledgers: dict[str, Mapping[str, Any] | None] = {}
    enriched: list[dict[str, Any]] = []
    for item in items:
        project_name = item.get("project_name")
        item_id = item.get(id_field)
        ref = None
        if isinstance(project_name, str) and project_name:
            if project_name not in ledgers:
                ledgers[project_name] = _load_project(projects, project_name)
            project = ledgers[project_name]
            if project is not None and isinstance(item_id, str) and item_id_episode(item_id) is not None:
                ref = episode_item_ref(project, item_id)
            item = present_episode_diagnostics(dict(item), project or {}, translate)
        enriched.append({**item, ref_field: ref})
    return enriched


def _load_project(projects: ProjectManager, project_name: str) -> Mapping[str, Any] | None:
    try:
        return projects.load_project(project_name)
    except (OSError, ValueError):
        return None


__all__ = ["with_episode_item_refs"]
