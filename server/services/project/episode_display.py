"""Web 诊断里的集与条目指称；内部 ID 和 Agent 回执保持原样。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import Request
from pydantic import BaseModel

from lib.episode.episode_ids import episode_display_name
from lib.project.project_manager import get_project_manager

_EPISODE_REF = re.compile(r"(?:集（id=(\d+)）|(?:episode|tập) \(id=(\d+)\))", re.IGNORECASE)
_ITEM_REF = re.compile(r"(?<![A-Za-z0-9])E(\d+)([A-Z](?:\d+|##))")
_SCRIPT_REF = re.compile(r"(?:scripts/)?episode_(\d+)\.json")
_EPISODE_PATH = re.compile(r"episode[_-](\d+)")
_TEXT_FIELDS = frozenset(
    {"detail", "diagnostic", "message", "error_message", "reason", "repair_reason", "label", "text", "next_action"}
)
_DATA_FIELDS = frozenset({"params", "error_params", "content", "inputs", "last_provider_response"})


def render_episode_text(message: str, project: Mapping[str, Any], translate: Callable[..., str]) -> str:
    """把已成文的诊断改成标题或播出位置；已移出账本的集显示为未命名集。"""

    def name(episode_id: int) -> str:
        return episode_display_name(project, episode_id, translate)

    message = _EPISODE_REF.sub(lambda m: name(int(m.group(1) or m.group(2))), message)
    message = _SCRIPT_REF.sub(lambda m: translate("episode_script_name", name=name(int(m.group(1)))), message)
    message = _EPISODE_PATH.sub(lambda m: name(int(m.group(1))), message)
    return _ITEM_REF.sub(lambda m: f"{name(int(m.group(1)))} · {m.group(2)}", message)


def present_episode_diagnostics(value: Any, project: Mapping[str, Any], translate: Callable[..., str]) -> Any:
    """只改诊断文本字段，不改定位 ID、并发令牌、草稿正文与机器参数。"""
    if isinstance(value, str):
        return render_episode_text(value, project, translate)
    if isinstance(value, list):
        changes = {}
        for index, item in enumerate(value):
            if isinstance(item, (BaseModel, dict, list)):
                result = present_episode_diagnostics(item, project, translate)
                if result is not item:
                    changes[index] = result
        if changes:
            return [changes.get(index, item) for index, item in enumerate(value)]
    if isinstance(value, (dict, BaseModel)):
        fields = (
            value.items()
            if isinstance(value, dict)
            else ((name, getattr(value, name)) for name in type(value).model_fields)
        )
        updates = {}
        for key, item in fields:
            if key in _TEXT_FIELDS and isinstance(item, str):
                result = render_episode_text(item, project, translate)
                if result != item:
                    updates[key] = result
            elif key == "warnings" and isinstance(item, list):
                warnings = [render_episode_text(w, project, translate) if isinstance(w, str) else w for w in item]
                if warnings != item:
                    updates[key] = warnings
            elif key not in _DATA_FIELDS and isinstance(item, (BaseModel, dict, list)):
                result = present_episode_diagnostics(item, project, translate)
                if result is not item:
                    updates[key] = result
        if updates:
            return value.model_copy(update=updates) if isinstance(value, BaseModel) else {**value, **updates}
    return value


async def present_request_diagnostics(value: Any, request: Request, translate: Callable[..., str]) -> Any:
    """项目 REST 的呈现边界；读不到项目时仍隐藏内部集 ID。"""
    project_name = request.path_params.get("project_name")
    if project_name is None and "/projects/" in request.url.path:
        project_name = request.path_params.get("name")
    if not isinstance(project_name, str):
        return value
    try:
        project = await asyncio.to_thread(lambda: get_project_manager().load_project(project_name))
    except (OSError, ValueError):
        project = {}
    return present_episode_diagnostics(value, project, translate)
