"""Agent 工具的宿主无关声明与统一调用入口。

一条声明给出一个工具在两宿主之间共用的全部契约：名字、中文完整描述、请求模型（schema 由它派生，
参数说明写在字段 description 上）、迁移阻断策略、长任务标记、domain key、handler 与可选摘要。
两个 adapter 都经 :func:`invoke_declaration`（无 scope 声明经 :func:`invoke_unscoped_declaration`）
调用 handler，请求校验与迁移阻断因此只有一处实现。

声明分两种：作用于某个既有项目的 :class:`ToolDeclaration`，以及不作用于既有项目的
:class:`UnscopedToolDeclaration`（列出、创建项目）。adapter 按声明类型分派。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    migration_gate,
)


@dataclass(frozen=True, slots=True)
class Blocked:
    """项目迁移裁决为失败时在入口拒绝，不调用 handler。"""


@dataclass(frozen=True, slots=True)
class Exempt:
    """迁移失败时照常放行；``reason`` 写明该入口为什么不必阻断。"""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("豁免迁移阻断必须写明理由")


@dataclass(frozen=True, slots=True)
class ReadCheck:
    """只读工具自行报告迁移状态：入口放行，由 handler 在结果中返回迁移 problem。"""


BLOCKED = Blocked()
READ_CHECK = ReadCheck()

type MigrationPolicy = Blocked | Exempt | ReadCheck

type ScopedHandler[RequestT, ResultT] = Callable[
    [ToolRequest[RequestT], ProjectScope, CallerContext, Services], Awaitable[ToolOutcome[ResultT]]
]

type UnscopedHandler[RequestT, ResultT] = Callable[
    [ToolRequest[RequestT], CallerContext, Services], Awaitable[ToolOutcome[ResultT]]
]


class _UntitledJsonSchema(GenerateJsonSchema):
    """省去 pydantic 按类名、字段名自动生成的 title；参数语义只由字段 description 表达。"""

    def field_title_should_be_set(self, schema: Any) -> bool:
        del schema
        return False

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        json_schema = super().model_schema(schema)
        json_schema.pop("title", None)
        return json_schema


def request_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """由请求模型派生工具的参数 schema（顶层恒含 ``type`` 与 ``properties``）。

    工具用途只写在声明的 description 上，请求模型的 docstring 不进入 schema。
    """
    schema = model.model_json_schema(schema_generator=_UntitledJsonSchema)
    schema.pop("description", None)
    schema.setdefault("properties", {})
    return schema


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDeclaration[RequestT: BaseModel, ResultT]:
    """作用于某个既有项目的 Agent 工具。

    结果编码的三个可选钩子都作用于 handler 的成功值，两宿主共用：

    - ``summary``：人类可读的摘要文本，返回空值时不出摘要块；
    - ``projection``：结构化结果，缺省为 ``{domain_key: 值的 JSON 投影}``；
    - ``is_error``：值本身表示失败时（如批次全部被阻断）返回 True，缺省恒为 False。
    """

    name: str
    description: str
    request_model: type[RequestT]
    migration: MigrationPolicy
    domain_key: str
    handler: ScopedHandler[RequestT, ResultT]
    long_task: bool = False
    summary: Callable[[ResultT], str | None] | None = None
    projection: Callable[[ResultT], dict[str, Any]] | None = None
    is_error: Callable[[ResultT], bool] | None = None

    @property
    def input_schema(self) -> dict[str, Any]:
        return request_json_schema(self.request_model)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnscopedToolDeclaration[RequestT: BaseModel, ResultT]:
    """不作用于某个既有项目的 Agent 工具。

    handler 不接 ``ProjectScope``：内嵌宿主不注入会话项目，远程宿主的 schema 不追加 ``project``。
    没有目标项目也就没有迁移裁决可查，因此不带迁移阻断策略。
    """

    name: str
    description: str
    request_model: type[RequestT]
    domain_key: str
    handler: UnscopedHandler[RequestT, ResultT]
    summary: Callable[[ResultT], str] | None = None

    @property
    def input_schema(self) -> dict[str, Any]:
        return request_json_schema(self.request_model)


type AgentToolDeclaration = ToolDeclaration[Any, Any] | UnscopedToolDeclaration[Any, Any]

MIGRATION_REFUSAL_NOTE = "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"


def tool_description(declaration: AgentToolDeclaration) -> str:
    """两宿主共用的工具描述：迁移阻断策略为 ``BLOCKED`` 的声明在末尾追加迁移拒绝说明。"""
    if isinstance(declaration, ToolDeclaration) and isinstance(declaration.migration, Blocked):
        return declaration.description + MIGRATION_REFUSAL_NOTE
    return declaration.description


def invalid_request_problem(exc: ValidationError) -> ToolProblem:
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
    detail = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}" for error in errors
    )
    return ToolProblem("invalid_request", detail, params={"errors": errors})


def _validated_request[RequestT: BaseModel](
    request_model: type[RequestT], arguments: Mapping[str, Any]
) -> RequestT | ToolProblem:
    try:
        return request_model.model_validate(dict(arguments))
    except ValidationError as exc:
        return invalid_request_problem(exc)


async def invoke_declaration(
    declaration: ToolDeclaration[Any, Any],
    arguments: Mapping[str, Any],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    """按声明校验请求、执行迁移阻断策略并调用 handler；两宿主共用这一个入口。"""
    request = _validated_request(declaration.request_model, arguments)
    if isinstance(request, ToolProblem):
        return ToolOutcome(problem=request)
    if isinstance(declaration.migration, Blocked) and (problem := await migration_gate(scope, services)) is not None:
        return ToolOutcome(problem=problem)
    return await declaration.handler(ToolRequest(request), scope, caller, services)


async def invoke_unscoped_declaration(
    declaration: UnscopedToolDeclaration[Any, Any],
    arguments: Mapping[str, Any],
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    """按无 scope 声明校验请求并调用 handler；两宿主共用这一个入口。"""
    request = _validated_request(declaration.request_model, arguments)
    if isinstance(request, ToolProblem):
        return ToolOutcome(problem=request)
    return await declaration.handler(ToolRequest(request), caller, services)


__all__ = [
    "BLOCKED",
    "MIGRATION_REFUSAL_NOTE",
    "READ_CHECK",
    "AgentToolDeclaration",
    "Blocked",
    "Exempt",
    "MigrationPolicy",
    "ReadCheck",
    "ScopedHandler",
    "ToolDeclaration",
    "UnscopedHandler",
    "UnscopedToolDeclaration",
    "invalid_request_problem",
    "invoke_declaration",
    "invoke_unscoped_declaration",
    "request_json_schema",
    "tool_description",
]
