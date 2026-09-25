"""把一个 ``ToolOutcome`` 编码成两宿主共用的结果信封。

结构化结果只有一份：成功为 ``{domain_key: 值}``，失败为 ``{"problem": {code, detail, action?, params?}}``。
文本块为「摘要（如有）+ 这份结构化结果的 JSON」；内嵌宿主把文本块写进 content，远程宿主另把
结构化结果写进 ``structuredContent``。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from lib.project.project_migration_failure import MIGRATION_FAILURE_CODE
from server.agent_toolset.declaration import AgentToolDeclaration
from server.tool_runtime import ToolOutcome, ToolProblem

_PROBLEM_SUMMARIES: dict[str, str] = {
    MIGRATION_FAILURE_CODE: (
        "❌ 项目数据升级未完成，生成、正式写入与剧本 revision 签发均已关闭。"
        "请按 problem 明细修复后调用 retry_project_migration。"
    ),
}


def json_value(value: Any) -> Any:
    """把 typed 领域值投影成 JSON 安全值。"""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ToolEnvelope:
    structured: dict[str, Any]
    texts: tuple[str, ...]
    is_error: bool


def problem_summary(problem: ToolProblem) -> str | None:
    return _PROBLEM_SUMMARIES.get(problem.code)


def encode_outcome(declaration: AgentToolDeclaration, outcome: ToolOutcome[Any]) -> ToolEnvelope:
    if outcome.problem is not None:
        structured = {"problem": outcome.problem.model_dump(mode="json")}
        summary = problem_summary(outcome.problem)
        is_error = True
    else:
        structured = {declaration.domain_key: json_value(outcome.value)}
        summary = declaration.summary(outcome.value) if declaration.summary is not None else None
        is_error = False
    encoded = json.dumps(structured, ensure_ascii=False)
    return ToolEnvelope(
        structured=structured,
        texts=(summary, encoded) if summary else (encoded,),
        is_error=is_error,
    )


__all__ = ["ToolEnvelope", "encode_outcome", "json_value", "problem_summary"]
