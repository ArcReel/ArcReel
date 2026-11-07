"""Item references outside episode pages: attach the owning episode title and broadcast position, plus the item’s current position, to task and call records.

Task and call records store only item IDs (``E3S01``), which are allocated without reuse and are not meaningful to creators.
The UI uses the attached structure to display “Title · 5” with a thumbnail and body excerpt. Storyboard and video-unit positions
are their current one-based indices in this episode’s item array, calculated from the script. If an item is no longer in the script,
the position is None and the UI shows “Deleted”. Records may span projects; read each project ledger and each episode script only once."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from lib.episode.episode_ids import episode_item_ref, find_ledger_entry, item_id_episode
from lib.i18n import _
from lib.project.project_manager import ProjectManager
from lib.script.item_excerpt import find_item, item_excerpt
from server.services.project.episode_display import present_episode_diagnostics


def with_episode_item_refs(
    items: Iterable[Mapping[str, Any]],
    *,
    id_field: str,
    ref_field: str,
    projects: ProjectManager,
    translate: Callable[..., str] = _,
) -> list[dict[str, Any]]:
    """Return copies with ``ref_field`` attached; use None when the project cannot be read, the ID lacks an episode prefix, or the episode is absent from the ledger."""

    ledgers: dict[str, Mapping[str, Any] | None] = {}
    scripts: dict[tuple[str, str], Mapping[str, Any] | None] = {}
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
                if ref is not None:
                    ref = {**ref, **_item_facts(projects, project_name, project, item_id, scripts)}
            item = present_episode_diagnostics(dict(item), project or {}, translate)
        enriched.append({**item, ref_field: ref})
    return enriched


# Item IDs for storyboards (segment / scene / shot) and video units; grids (E3G01) are not referenced by position.
_POSITIONED_ITEM_ID = re.compile(r"^E\d+[SU]\d")
_NO_FACTS: dict[str, Any] = {"item_position": None, "item_excerpt": "", "item_thumbnail": None}


def _item_facts(
    projects: ProjectManager,
    project_name: str,
    project: Mapping[str, Any],
    item_id: str,
    cache: dict[tuple[str, str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """An item’s current position, body excerpt and thumbnail in this episode’s script; position is None when the item is no longer in the script."""
    episode_id = item_id_episode(item_id)
    entry = find_ledger_entry(project, episode_id) if episode_id is not None else None
    script_file = entry.get("script_file") if entry else None
    if not _POSITIONED_ITEM_ID.match(item_id) or not isinstance(script_file, str) or not script_file:
        return dict(_NO_FACTS)
    key = (project_name, script_file)
    if key not in cache:
        cache[key] = _load_script(projects, project_name, script_file)
    script = cache[key]
    if script is None:
        return dict(_NO_FACTS)
    found = find_item(script, item_id)
    if found is None:
        return dict(_NO_FACTS)
    position, item = found
    return {"item_position": position, "item_excerpt": item_excerpt(item), "item_thumbnail": _thumbnail(item)}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _thumbnail(item: Mapping[str, Any]) -> str | None:
    assets = item.get("generated_assets")
    if not isinstance(assets, Mapping):
        return None
    return _text(assets.get("video_thumbnail")) or _text(assets.get("storyboard_image")) or None


def _load_script(projects: ProjectManager, project_name: str, script_file: str) -> Mapping[str, Any] | None:
    try:
        return projects.load_script_readonly(project_name, script_file)
    except (OSError, ValueError):
        return None


def _load_project(projects: ProjectManager, project_name: str) -> Mapping[str, Any] | None:
    try:
        return projects.load_project(project_name)
    except (OSError, ValueError):
        return None


__all__ = ["with_episode_item_refs"]
