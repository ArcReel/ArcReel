"""成片渲染的领域错误：问题码稳定，HTTP、Agent 工具与队列任务各自映射。"""

from __future__ import annotations

from typing import Any, Literal

type FinalCutErrorCode = Literal[
    "final_cut_narration_unavailable",
    "final_cut_presentation_unavailable",
    "final_cut_blocked",
    "final_cut_content_unsupported",
    "final_cut_empty",
    "final_cut_ffmpeg_unavailable",
    "final_cut_render_failed",
    "final_cut_acceptance_failed",
]


class FinalCutError(Exception):
    """成片无法渲染或未通过验收；``code`` 供适配器映射，``params`` 携带定位信息。"""

    def __init__(self, code: FinalCutErrorCode, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code: FinalCutErrorCode = code
        self.params = params


__all__ = ["FinalCutError", "FinalCutErrorCode"]
