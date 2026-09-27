"""媒体工具 handler 共用的结果构造、生成类结果钩子与请求字段类型。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import AfterValidator, Field

from lib.generation.generation_batch import GenerationBatchReadModel
from lib.generation.generation_result import GenerationBatchResult, render_generation_result
from lib.infra.schema_guards import is_str
from server.agent_toolset.envelope import json_value
from server.tool_runtime import ToolOutcome, ToolProblem


def tool_error(name: str, exc: BaseException, log: list[str] | None = None) -> ToolOutcome[Any]:
    """Return a typed handler failure for host adapters to encode."""
    msg = f"{name} 失败: {exc}"
    text = "\n".join([msg, *log]) if log else msg
    return ToolOutcome(problem=ToolProblem("internal_error", text))


def tool_problem(
    detail: str, *, code: str = "invalid_request", params: dict[str, Any] | None = None
) -> ToolOutcome[Any]:
    return ToolOutcome(problem=ToolProblem(code, detail, params=params))


def generation_result_outcome(
    result: GenerationBatchResult,
    log: list[str] | None = None,
    **extra: Any,
) -> ToolOutcome[Any]:
    """Return one generation batch contract for host adapters to encode.

    ``generation_result`` is the machine-readable payload; the text block is a
    rendering of the same fields, so no consumer has to parse it to decide
    whether to retry.
    """
    payload: dict[str, Any] = {
        "generation_result": result,
        "summary": render_generation_result(result, log=log or ()),
        **extra,
    }
    return ToolOutcome(value=payload)


def generation_batch_submission_outcome(result: GenerationBatchReadModel) -> ToolOutcome[GenerationBatchReadModel]:
    return ToolOutcome(value=result)


type GenerationToolValue = GenerationBatchReadModel | dict[str, Any]
"""生成类工具的成功值：远程即返的批次句柄，或 :func:`generation_result_outcome` 给出的终态结果。"""


def generation_structured(value: GenerationToolValue) -> dict[str, Any]:
    """生成类工具成功值的结构化结果。

    批次句柄放在 ``generation_batch`` 下；终态结果把 ``generation_result`` 与附带字段平铺在顶层，
    其中 ``generation_result`` 与批次终态查询附带的生成结果同形。``summary`` 只作摘要文本。
    """
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    payload = json_value(value)
    payload.pop("summary", None)
    return payload


def generation_summary(value: GenerationToolValue) -> str | None:
    return None if isinstance(value, GenerationBatchReadModel) else value.get("summary")


def generation_is_error(value: GenerationToolValue) -> bool:
    """终态结果未全部成功即为失败；等待用户确认的准入不算失败，批次句柄也不算。"""
    if isinstance(value, GenerationBatchReadModel):
        return False
    result = value.get("generation_result")
    admission = value.get("batch_admission")
    return (
        isinstance(result, GenerationBatchResult)
        and not result.ok
        and not (isinstance(admission, dict) and admission.get("decision") == "confirmation_required")
    )


def validate_script_filename(value: str) -> str:
    """Reject any agent-provided ``script`` arg that is not a bare basename.

    Agents must reference scripts by filename only (e.g. ``episode_1.json``);
    the project root is bound by the caller's ``ProjectScope`` and the ``scripts/``
    subdir is fixed inside ``ProjectManager.load_script``. Any path separator —
    including a ``scripts/`` prefix or ``..`` segments — is rejected.
    """
    if not is_str(value) or not value:
        raise ValueError("script 文件名不能为空")
    if "/" in value or "\\" in value or value in (".", ".."):
        raise ValueError(f"script 必须是纯文件名，禁止路径分隔符: {value!r}")
    return value


ScriptFilename = Annotated[str, AfterValidator(validate_script_filename)]
"""剧本纯文件名；带路径分隔符或为空时请求校验失败。"""

RequestedIds = Annotated[list[str], Field(min_length=1)]
"""显式点名的目标 ID；空数组无效——省略参数才表示只补缺失项。"""
