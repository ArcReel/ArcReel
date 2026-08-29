"""条目最终提示词的预览渲染。

回答「这个条目现在生成，送进图像 / 视频模型的提示词逐字是什么」。渲染函数与执行路径同一个
出口（``lib.prompt_builders.render_storyboard_image_prompt`` /
``lib.prompt_utils.render_storyboard_video_prompt``），本模块只负责把执行期读的那些输入
——项目风格、商品参考、创作类型、声音绑定——按同一口径备齐。

只读：不向供应商发请求、不产生费用、不写产物清单。参考生视频路径的 unit 正文本身即提示词
主体，其预览走 ``lib.reference_video.script_preview``，不经本模块。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifact_activation import active_artifact_currency_resolver
from lib.project_manager import ProjectManager, get_project_manager
from lib.prompt_builders import render_storyboard_image_prompt
from lib.prompt_utils import render_storyboard_video_prompt
from lib.script_models import resolve_content_mode
from lib.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.generation_tasks import (
    collect_shot_product_references,
)
from server.services.video_batch_admission import resolve_voice_context

#: 条目没有该提示词字段时的不可用原因。
UNAVAILABLE_MISSING = "prompt_preview_missing"
#: 提示词字段在场但形状不合规、渲染函数拒绝时的不可用原因。
UNAVAILABLE_INVALID = "prompt_preview_invalid"


class ScriptItemNotFound(LookupError):
    """剧本里没有该条目 id。"""


@dataclass(frozen=True)
class RenderedPrompt:
    """一侧提示词的渲染结果：``text`` 与 ``unavailable`` 恰有一个非 ``None``。"""

    text: str | None = None
    unavailable: str | None = None
    is_text_form: bool = False


@dataclass(frozen=True)
class ItemPromptPreview:
    item_id: str
    content_mode: str
    storyboard_image: RenderedPrompt
    video: RenderedPrompt


def _render(prompt: object, render: Any) -> RenderedPrompt:
    is_text_form = isinstance(prompt, str)
    if prompt is None or (is_text_form and not prompt.strip()) or prompt == {}:
        return RenderedPrompt(unavailable=UNAVAILABLE_MISSING, is_text_form=is_text_form)
    try:
        return RenderedPrompt(text=render(prompt), is_text_form=is_text_form)
    except (ValueError, TypeError, KeyError):
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=is_text_form)


async def preview_item_prompts(
    project_name: str,
    script_file: str,
    item_id: str,
    *,
    projects: ProjectManager | None = None,
) -> ItemPromptPreview:
    """渲染一个分镜条目的分镜图与视频最终提示词文本。

    ``projects`` 供工具运行时把已解析的 ProjectManager 注入进来（REST 路由用进程默认实例）。
    """

    def _load() -> tuple[dict[str, Any], Path, dict[str, Any], str]:
        manager = projects if projects is not None else get_project_manager()
        project = manager.load_project(project_name)
        project_path = manager.get_project_path(project_name)
        script = manager.load_script(project_name, script_file)
        items, id_field, *_ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, item_id)
        if resolved is None:
            raise ScriptItemNotFound(item_id)
        return project, project_path, resolved[0], resolve_content_mode(script, project)

    project, project_path, item, content_mode = await asyncio.to_thread(_load)
    # 声音绑定按项目当前的视频能力档解析（非 drama 或无声一律 None），与执行期同一判据。
    voice_characters = await resolve_voice_context(project, content_mode)

    def _render_both() -> ItemPromptPreview:
        style = project.get("style", "")
        style_description = project.get("style_description", "")
        # 商品分镜的高保真指令只点名实际会注入参考图的商品，与执行期读同一份装配结果。
        resolver = active_artifact_currency_resolver(project_path, project)
        product_names = [
            reference["name"]
            for reference in collect_shot_product_references(project, project_path, item, currency_resolver=resolver)
        ]
        image = _render(
            item.get("image_prompt"),
            lambda prompt: render_storyboard_image_prompt(
                prompt,
                style=style if isinstance(style, str) else "",
                style_description=style_description if isinstance(style_description, str) else "",
                product_names=list(dict.fromkeys(product_names)) or None,
            ),
        )
        video = _render(
            item.get("video_prompt"),
            lambda prompt: render_storyboard_video_prompt(
                prompt,
                item,
                content_mode=content_mode,
                voice_characters=voice_characters,
            ),
        )
        return ItemPromptPreview(
            item_id=item_id,
            content_mode=content_mode,
            storyboard_image=image,
            video=video,
        )

    return await asyncio.to_thread(_render_both)


__all__ = [
    "UNAVAILABLE_INVALID",
    "UNAVAILABLE_MISSING",
    "ItemPromptPreview",
    "RenderedPrompt",
    "ScriptItemNotFound",
    "preview_item_prompts",
]
