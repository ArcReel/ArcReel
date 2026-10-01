"""Web 诊断里的集与条目指称；内部 ID 和 Agent 回执保持原样。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import Request

from lib.episode.episode_ids import episode_position, episode_title
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
        title = episode_title(project, episode_id)
        if title:
            return title
        position = episode_position(project, episode_id)
        return translate("episode_position_name", position=position) if position else translate("episode_unlisted_name")

    message = _EPISODE_REF.sub(lambda m: name(int(m.group(1) or m.group(2))), message)
    message = _SCRIPT_REF.sub(lambda m: translate("episode_script_name", name=name(int(m.group(1)))), message)
    message = _EPISODE_PATH.sub(lambda m: name(int(m.group(1))), message)
    return _ITEM_REF.sub(lambda m: f"{name(int(m.group(1)))} · {m.group(2)}", message)


def present_episode_diagnostics(value: Any, project: Mapping[str, Any], translate: Callable[..., str]) -> Any:
    """只改诊断文本字段，不改定位 ID、并发令牌、草稿正文与机器参数。"""
    if isinstance(value, str):
        return render_episode_text(value, project, translate)
    if isinstance(value, list):
        return [
            present_episode_diagnostics(item, project, translate) if isinstance(item, (dict, list)) else item
            for item in value
        ]
    if isinstance(value, dict):
        presented = dict(value)
        for key, item in value.items():
            if key in _TEXT_FIELDS and isinstance(item, str):
                presented[key] = render_episode_text(item, project, translate)
            elif key == "warnings" and isinstance(item, list):
                presented[key] = [
                    render_episode_text(warning, project, translate) if isinstance(warning, str) else warning
                    for warning in item
                ]
            elif key not in _DATA_FIELDS and isinstance(item, (dict, list)):
                presented[key] = present_episode_diagnostics(item, project, translate)
        return presented
    return value


async def present_request_diagnostics(value: Any, request: Request, translate: Callable[..., str]) -> Any:
    """项目 REST 的呈现边界；读不到项目时仍隐藏内部集 ID。"""
    project_name = request.path_params.get("project_name")
    if project_name is None and "/projects/" in request.url.path:
        project_name = request.path_params.get("name")
    if not isinstance(project_name, str):
        return value
    try:
        project = await asyncio.to_thread(get_project_manager().load_project, project_name)
    except (OSError, ValueError):
        project = {}
    return present_episode_diagnostics(value, project, translate)
