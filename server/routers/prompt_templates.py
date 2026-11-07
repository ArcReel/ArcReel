"""Read-only prompt-template API: system settings list builtin templates by category, display individual template source/metadata, and partial bodies/references.

Route prefix: /api/v1/prompt-templates
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lib.infra.api_errors import NotFoundError
from lib.prompts.prompt_templates import PartialEntry, PromptTemplates, TemplateError, TemplateMeta, UserActionTrigger
from lib.prompts.prompt_templates.builtin import builtin_templates

router = APIRouter(prefix="/prompt-templates")


def get_prompt_templates() -> PromptTemplates:
    return builtin_templates


_AGENT_SESSION_TRIGGER = UserActionTrigger(kind="user_action", name="agent_session")

Templates = Annotated[PromptTemplates, Depends(get_prompt_templates)]


class PromptTemplateListResponse(BaseModel):
    templates: list[TemplateMeta]


class PromptTemplateDetailResponse(BaseModel):
    template: TemplateMeta
    source: str
    partials: list[PartialEntry]
    """First-reference order; variant families expand into every axis value declared by ``applies_to``."""
    output_schema: dict[str, Any] | None = Field(default=None, exclude_if=lambda value: value is None)


@router.get("")
async def list_prompt_templates(templates: Templates) -> PromptTemplateListResponse:
    """Agent language rules join session system prompts and appear in the Agent tab, not this list; detail endpoints still expose them."""
    return PromptTemplateListResponse(
        templates=[item for item in templates.list_templates() if item.invoked_by != _AGENT_SESSION_TRIGGER]
    )


# Register before template detail: its ``{template_id:path}`` captures arbitrary subpaths.
@router.get("/partials/{name:path}")
async def get_prompt_partial(name: str, templates: Templates) -> PartialEntry:
    try:
        return templates.read_partial(name)
    except TemplateError:
        raise NotFoundError("prompt_partial_not_found", id=name) from None


@router.get("/{template_id:path}")
async def get_prompt_template(template_id: str, templates: Templates) -> PromptTemplateDetailResponse:
    metadata = next((item for item in templates.list_templates() if item.id == template_id), None)
    if metadata is None:
        raise NotFoundError("prompt_template_not_found", id=template_id)
    source, partials = templates.read_source(template_id)
    return PromptTemplateDetailResponse(
        template=metadata,
        source=source,
        partials=partials,
        output_schema=_output_json_schema(metadata.output_schema),
    )


def _output_json_schema(spec: str | None) -> dict[str, Any] | None:
    """Resolve frontmatter ``module:Class`` to that Pydantic model's JSON schema."""
    if spec is None:
        return None
    module_name, _, class_name = spec.partition(":")
    model = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"output_schema 不是 pydantic 模型: {spec}")
    return model.model_json_schema()
