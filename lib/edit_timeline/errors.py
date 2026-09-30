"""剪辑时间线命令的领域错误：问题码稳定，HTTP 与 Agent 工具各自映射。"""

from __future__ import annotations

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


__all__ = ["EditTimelineError", "EditTimelineErrorCode"]
