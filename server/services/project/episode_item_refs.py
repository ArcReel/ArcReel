"""集页面之外的条目指称：给任务与调用记录附上条目所属集的标题与播出位置，以及条目本身的当前位置。

任务与调用记录只存条目 ID（``E3S01``），条目 ID 只分配不复用，对创作者没有意义；界面凭这里附上的
结构拼出「标题 · 5」，并配上缩略图与正文摘录。分镜与视频单元的位置是它在本集条目数组里的当前
序号（从 1 起），按剧本现算；条目已不在剧本里时为 None，界面显示「已删除」。记录可能跨项目，
每个项目的账本、每集的剧本各只读一次。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from lib.episode.episode_ids import episode_item_ref, find_ledger_entry, item_id_episode
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


# 分镜（segment / scene / shot）与视频单元的条目 ID；宫格（E3G01）不按位置指称。
_POSITIONED_ITEM_ID = re.compile(r"^E\d+[SU]\d")
_ITEM_ARRAYS = (("segments", "segment_id"), ("scenes", "scene_id"), ("shots", "shot_id"), ("video_units", "unit_id"))
_EXCERPT_LENGTH = 16
_MENTION = re.compile(r"@\[([^\]]*)\]")
_NO_FACTS: dict[str, Any] = {"item_position": None, "item_excerpt": "", "item_thumbnail": None}


def _item_facts(
    projects: ProjectManager,
    project_name: str,
    project: Mapping[str, Any],
    item_id: str,
    cache: dict[tuple[str, str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """条目在本集剧本里的当前位置、正文摘录与缩略图；条目已不在剧本里时位置为 None。"""
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
    for array_key, id_field in _ITEM_ARRAYS:
        items = script.get(array_key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items, start=1):
            if isinstance(item, Mapping) and item.get(id_field) == item_id:
                return {"item_position": index, "item_excerpt": _excerpt(item), "item_thumbnail": _thumbnail(item)}
    return dict(_NO_FACTS)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _image_prompt_scene(item: Mapping[str, Any]) -> str:
    prompt = item.get("image_prompt")
    if isinstance(prompt, Mapping):
        return _text(prompt.get("scene"))
    return _text(prompt)


def _body(item: Mapping[str, Any]) -> str:
    """条目正文：旁白取原文，剧情演绎取画面描述，广告/短片取口播，视频单元取正文（与前端 ``itemExcerpt`` 同口径）。"""
    if "segment_id" in item:
        return _text(item.get("novel_text"))
    if "unit_id" in item:
        return _MENTION.sub(r"@\1", _text(item.get("text")))
    if "shot_id" in item:
        return _text(item.get("voiceover_text")) or _image_prompt_scene(item)
    return _text(item.get("scene_description")) or _image_prompt_scene(item) or _text(item.get("source_text"))


def _excerpt(item: Mapping[str, Any]) -> str:
    body = " ".join(_body(item).split())
    return body if len(body) <= _EXCERPT_LENGTH else f"{body[:_EXCERPT_LENGTH]}…"


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
