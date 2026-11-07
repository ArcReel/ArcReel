"""Host-independent Agent tool declarations and shared invocation entry.

A declaration provides a tool’s full contract shared by both hosts: name, complete description, request model (from which schema is derived, with parameter descriptions on fields), migration-blocking policy, long-task flag, domain key, handler, and optional summary. Both adapters invoke the handler through :func:`invoke_declaration` (or :func:`invoke_unscoped_declaration` without scope), keeping request validation and migration admission in one implementation.

:class:`ToolDeclaration` acts on an existing project; :class:`UnscopedToolDeclaration` does not (listing and creating projects). Adapters dispatch by declaration type."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    """Reject at entry when project migration failed, without calling the handler."""


@dataclass(frozen=True, slots=True)
class Exempt:
    """Admit despite migration failure; ``reason`` states why this entry need not be blocked."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("A migration-blocking exemption requires a reason")


@dataclass(frozen=True, slots=True)
class ReadCheck:
    """Read-only tools report migration state themselves: entry admits and the handler returns migration problems in its result."""


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
    """Omit Pydantic titles generated from class/field names; field descriptions alone express argument semantics."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        del schema
        return False

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        json_schema = super().model_schema(schema)
        json_schema.pop("title", None)
        return json_schema


def request_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Derive the argument schema from the request model, always with top-level ``type`` and ``properties``.

    Tool purpose appears only in the declaration description; request-model docstrings do not enter the schema."""
    schema = model.model_json_schema(schema_generator=_UntitledJsonSchema)
    schema.pop("description", None)
    schema.setdefault("properties", {})
    return schema


@dataclass(frozen=True, slots=True)
class ToolImage:
    """A result image, delivered to the Agent as an MCP image content block on both hosts, not a file path."""

    data: bytes
    mime_type: str

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDeclaration[RequestT: BaseModel, ResultT]:
    """An Agent tool acting on an existing project.

    Optional result-encoding hooks act on the handler’s success value and are shared by both hosts:

    - ``summary``: human-readable summary; an empty result omits the summary block.
    - ``projection``: structured result, defaulting to ``{domain_key: JSON projection of the value}``.
    - ``is_error``: True when the value itself represents failure (such as an entirely blocked batch); defaults to False.
    - ``images``: images delivered to the Agent after text blocks; none by default."""

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
    images: Callable[[ResultT], Sequence[ToolImage]] | None = None

    @property
    def input_schema(self) -> dict[str, Any]:
        return request_json_schema(self.request_model)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnscopedToolDeclaration[RequestT: BaseModel, ResultT]:
    """An Agent tool not acting on an existing project.

    The handler receives no ``ProjectScope``: the embedded host injects no session project and the remote schema adds no ``project``.
    Without a target project there is no migration decision to query, so no migration-blocking policy is attached."""

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

MIGRATION_REFUSAL_NOTE = "Reject execution after project-data upgrade failure, returning project_migration_failed."

#: Shared by text-model tools: output-truncation problem code and recovery guidance.
TEXT_OUTPUT_TRUNCATED_NOTE = (
    "If text-model output is truncated by its output limit, returns text_output_truncated, with params.model naming the model: "
    "when params.custom_model is true, ask the user to register its maximum output length in settings; otherwise ask for a different text model. Retrying unchanged will truncate again."
)


def tool_description(declaration: AgentToolDeclaration) -> str:
    """Shared tool description: append a migration-refusal note for declarations with ``BLOCKED`` policy."""
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
    """Validate requests, enforce migration policy, and invoke the handler by declaration; both hosts share this entry."""
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
    """Validate requests and invoke the handler for unscoped declarations; both hosts share this entry."""
    request = _validated_request(declaration.request_model, arguments)
    if isinstance(request, ToolProblem):
        return ToolOutcome(problem=request)
    return await declaration.handler(ToolRequest(request), caller, services)


__all__ = [
    "BLOCKED",
    "MIGRATION_REFUSAL_NOTE",
    "READ_CHECK",
    "TEXT_OUTPUT_TRUNCATED_NOTE",
    "AgentToolDeclaration",
    "Blocked",
    "Exempt",
    "MigrationPolicy",
    "ReadCheck",
    "ScopedHandler",
    "ToolDeclaration",
    "ToolImage",
    "UnscopedHandler",
    "UnscopedToolDeclaration",
    "invalid_request_problem",
    "invoke_declaration",
    "invoke_unscoped_declaration",
    "request_json_schema",
    "tool_description",
]
