"""剧本结构校验器（纯函数）。

把「一个剧本 dict 是否结构良构」这个判断收敛到唯一一处：喂入 dict、返回
`ValidationResult`，不读磁盘、不依赖项目状态。写盘咽喉、测试、未来其它写入面都复用它。

校验对象是结构层 Pydantic 模型（`lib.script_models`），而非 FS 感知的 `DataValidator`
——后者会读磁盘并拒绝合法的半成品草稿（分镜图尚未生成）。模型已编码所有结构约束
（必填、`duration_seconds` 范围、id 格式、prompt 形状、参考单元 shots↔duration 一致性），
本校验器只负责「按模式选对模型」并把 Pydantic 的 `ValidationError` 转成 `ValidationResult`，
不复制任何约束——模型变更即校验变更（单一真相源）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from lib.data_validator import ValidationResult
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
    ReferenceVideoScript,
)


class ScriptStructureValidationError(ValueError):
    """剧本结构校验失败。携带 `ValidationResult`，供 router 转 i18n 4xx 响应。"""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("; ".join(result.errors) or "script structure invalid")


def _select_model(script: dict[str, Any]) -> type[BaseModel]:
    """按 generation_mode / content_mode / 顶层键判别该用哪个剧本模型。

    reference 分支最优先：generation_mode == "reference_video"（或存在 video_units）压过
    content_mode，即使 content_mode == "narration" 也走 ReferenceVideoScript。其余以
    content_mode 为权威：drama → Drama、narration → Narration；content_mode 缺省时才按顶层键
    存在性（而非列表真值）推断。用 "scenes" in script 而非 script.get("scenes") 真值，否则
    空场景 drama（{"content_mode": "drama", "scenes": []}，结构合法）会被误判到 Narration 拒写。
    """
    if script.get("generation_mode") == "reference_video" or "video_units" in script:
        return ReferenceVideoScript
    content_mode = script.get("content_mode")
    if content_mode == "drama":
        return DramaEpisodeScript
    if content_mode == "narration":
        return NarrationEpisodeScript
    if "scenes" in script and "segments" not in script:
        return DramaEpisodeScript
    return NarrationEpisodeScript


def _format_error(err: ErrorDetails) -> str:
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if loc else str(msg)


def validate_script_structure(script: dict[str, Any]) -> ValidationResult:
    """校验剧本 dict 的结构是否良构，返回 `ValidationResult`。

    纯函数：不读磁盘、不查文件引用、不查跨 project.json 的角色/场景名一致性
    （那些是 `DataValidator.validate_project_tree` 的归档层职责）。
    """
    model = _select_model(script)
    try:
        model.model_validate(script)
    except ValidationError as exc:
        return ValidationResult(valid=False, errors=[_format_error(e) for e in exc.errors()])
    return ValidationResult(valid=True)
