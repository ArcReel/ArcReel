"""提示词模版只读 API：系统设置页按类别列出内置模版，并展示单个模版的源文与元数据。

路由前缀: /api/v1/prompt-templates
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lib.api_errors import NotFoundError
from lib.prompt_templates import PromptTemplates, TemplateMeta
from lib.prompt_templates.builtin import builtin_templates

router = APIRouter(prefix="/prompt-templates")


def get_prompt_templates() -> PromptTemplates:
    return builtin_templates


Templates = Annotated[PromptTemplates, Depends(get_prompt_templates)]


class PromptTemplateListResponse(BaseModel):
    templates: list[TemplateMeta]


class PromptTemplatePartial(BaseModel):
    name: str
    source: str


class PromptTemplateDetailResponse(BaseModel):
    template: TemplateMeta
    source: str
    partials: list[PromptTemplatePartial]
    """按首次引用顺序；变体族展开为 ``applies_to`` 声明的全部轴值。"""
    output_schema: dict[str, Any] | None = Field(default=None, exclude_if=lambda value: value is None)


@router.get("")
async def list_prompt_templates(templates: Templates) -> PromptTemplateListResponse:
    return PromptTemplateListResponse(templates=templates.list_templates())


@router.get("/{template_id:path}")
async def get_prompt_template(template_id: str, templates: Templates) -> PromptTemplateDetailResponse:
    metadata = next((item for item in templates.list_templates() if item.id == template_id), None)
    if metadata is None:
        raise NotFoundError("prompt_template_not_found", id=template_id)
    source, partials = templates.read_source(template_id)
    return PromptTemplateDetailResponse(
        template=metadata,
        source=source,
        partials=[PromptTemplatePartial(name=name, source=text) for name, text in partials.items()],
        output_schema=_output_json_schema(metadata.output_schema),
    )


def _output_json_schema(spec: str | None) -> dict[str, Any] | None:
    """把 frontmatter 的 ``module:Class`` 解析为该 pydantic 模型的 JSON schema。"""
    if spec is None:
        return None
    module_name, _, class_name = spec.partition(":")
    model = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"output_schema 不是 pydantic 模型: {spec}")
    return model.model_json_schema()
