"""Result construction, generation-result hooks and request-field types shared by media-tool handlers."""

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
"""Generation-tool success value: an immediately returned remote batch handle, or terminal result from :func:`generation_result_outcome`."""


def generation_structured(value: GenerationToolValue) -> dict[str, Any]:
    """Structured successful values for generation tools.

    Batch handles live under ``generation_batch``; terminal results place ``generation_result`` and additional fields at the top level.
    ``generation_result`` matches generation results attached to terminal batch queries. ``summary`` is summary text only."""
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    payload = json_value(value)
    payload.pop("summary", None)
    return payload


def generation_summary(value: GenerationToolValue) -> str | None:
    return None if isinstance(value, GenerationBatchReadModel) else value.get("summary")


def generation_is_error(value: GenerationToolValue) -> bool:
    """Terminal results fail unless all items succeeded; admission awaiting user confirmation and batch handles do not count as failures."""
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
"""Bare script filename; empty values or path separators fail request validation."""

RequestedIds = Annotated[list[str], Field(min_length=1)]
"""Explicit target IDs; an empty array is invalid. Omit the argument to fill only missing items."""
