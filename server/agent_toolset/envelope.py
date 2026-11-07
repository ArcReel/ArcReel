"""Encode a ``ToolOutcome`` into the result envelope shared by both hosts.

There is one structured result: success is ``{domain_key: value}`` (provided by ``projection`` when declared), failure is ``{problem: ...}``. The text block contains the optional summary followed by this structured result’s JSON. The embedded host writes text blocks into content; the remote host also writes the structured result into ``structuredContent``. When ``images`` is declared, MCP image content blocks follow the text block identically for both hosts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from lib.project.project_migration_failure import MIGRATION_FAILURE_CODE
from server.agent_toolset.declaration import AgentToolDeclaration, ToolDeclaration, ToolImage
from server.tool_runtime import ToolOutcome, ToolProblem

_PROBLEM_SUMMARIES: dict[str, str] = {
    MIGRATION_FAILURE_CODE: (
        "❌ Project-data upgrade is incomplete; generation, formal writes, and script revision issuance are disabled. "
        "Repair according to the problem details, then call retry_project_migration."
    ),
}


def json_value(value: Any) -> Any:
    """Project typed domain values into JSON-safe values."""
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
    images: tuple[ToolImage, ...] = ()


def problem_summary(problem: ToolProblem) -> str | None:
    return _PROBLEM_SUMMARIES.get(problem.code)


def encode_outcome(declaration: AgentToolDeclaration, outcome: ToolOutcome[Any]) -> ToolEnvelope:
    images: tuple[ToolImage, ...] = ()
    if outcome.problem is not None:
        structured = {"problem": outcome.problem.model_dump(mode="json")}
        summary = problem_summary(outcome.problem)
        is_error = True
    else:
        value = outcome.value
        scoped = declaration if isinstance(declaration, ToolDeclaration) else None
        structured = (
            scoped.projection(value)
            if scoped is not None and scoped.projection is not None
            else {declaration.domain_key: json_value(value)}
        )
        summary = declaration.summary(value) if declaration.summary is not None else None
        is_error = scoped.is_error(value) if scoped is not None and scoped.is_error is not None else False
        if scoped is not None and scoped.images is not None:
            images = tuple(scoped.images(value))
    encoded = json.dumps(structured, ensure_ascii=False)
    return ToolEnvelope(
        structured=structured,
        texts=(summary, encoded) if summary else (encoded,),
        is_error=is_error,
        images=images,
    )


__all__ = ["ToolEnvelope", "encode_outcome", "json_value", "problem_summary"]
