"""剪辑时间线命令的领域错误：问题码稳定，HTTP 与 Agent 工具各自映射。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

type EditTimelineErrorCode = Literal[
    "project_not_found",
    "episode_not_found",
    "script_invalid",
    "timeline_not_found",
    "revision_not_found",
    "timeline_name_invalid",
    "timeline_name_conflict",
    "timeline_invalid",
    "operation_invalid",
    "revision_conflict",
    "revision_summary_invalid",
]


class EditTimelineError(Exception):
    """剪辑时间线命令无法完成；``code`` 供适配器映射，``params`` 携带定位信息。"""

    def __init__(self, code: EditTimelineErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: EditTimelineErrorCode = code
        self.params = params


# 会展示给创作者的错误码 → 译文 key：项目与集复用通用 key。批量编辑的错误只回给 Agent，不在此登记。
_MESSAGE_KEYS: dict[str, str] = {
    "project_not_found": "project_not_found",
    "episode_not_found": "episode_not_found",
    "timeline_not_found": "edit_timeline_not_found",
    "revision_not_found": "edit_timeline_revision_not_found",
    "timeline_name_conflict": "edit_timeline_name_conflict",
    "timeline_name_invalid": "edit_timeline_name_invalid",
    "script_invalid": "edit_timeline_script_invalid",
    "timeline_invalid": "edit_timeline_invalid",
}


def edit_timeline_message(code: str, params: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """错误码对应的译文 key 与插值参数；不是面向创作者的剪辑时间线错误码时返回 None。"""
    key = _MESSAGE_KEYS.get(code)
    if key is None:
        return None
    values = dict(params)
    if "project" in values:
        values["name"] = values.pop("project")
    return key, values


__all__ = ["EditTimelineError", "EditTimelineErrorCode", "edit_timeline_message"]
