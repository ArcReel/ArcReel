"""条目最终提示词的预览渲染。

回答「这个条目现在生成，送进图像 / 视频模型的提示词逐字是什么」。渲染函数与执行路径同一个
出口（``lib.prompts.prompt_builders.render_storyboard_image_prompt`` /
``lib.prompts.prompt_utils.render_storyboard_video_prompt``）。分镜图侧与执行器取同一份生成输入
（``lib.artifacts.generation_input``）：生成会被拒时该侧不可用并带缺口码，否则按与执行器相同的
图像后端参考图上限校验后渲染，「图N」编号与实发一致。视频侧按执行期同一口径备齐创作类型与
声音绑定。

只读：不向供应商发请求、不产生费用、不写产物清单。参考生视频路径的 unit 正文本身即提示词
主体，最终文本复用参考视频请求投影与共享预览 renderer。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_input_claims import resolve_usable_episode_script_input
from lib.artifacts.generation_input import (
    StoryboardImageInput,
    project_input_observation,
    storyboard_image_input,
)
from lib.backends.image_errors import ImageCapabilityError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager, get_project_manager, is_reference_video_project
from lib.prompts.prompt_utils import render_storyboard_video_prompt
from lib.prompts.reference_image_numbering import PREVIOUS_STORYBOARD_ROLE
from lib.script.reference_video.request_projection import project_reference_unit_request
from lib.script.script_editor import resolve_items
from lib.script.script_models import resolve_content_mode
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.admission.image_references import reference_image_limit
from server.services.admission.reference_admission import input_refusal_error
from server.services.admission.reference_prompt_preview import reference_prompt_preview_facts
from server.services.admission.video_batch_admission import resolve_voice_context

logger = logging.getLogger(__name__)

UNAVAILABLE_PENDING = "prompt_preview_pending"
UNAVAILABLE_MISSING = "prompt_preview_missing"
UNAVAILABLE_INVALID = "prompt_preview_invalid"


class ScriptItemNotFound(LookupError):
    """剧本里没有该条目 id。"""


@dataclass(frozen=True)
class RenderedPrompt:
    """一侧提示词的渲染结果：``text`` 与 ``unavailable`` 恰有一个非 ``None``。

    ``warnings`` 是渲染这份文本时产生的非阻断提示，与任务
    ``result.warnings`` 的 ``{key, params}`` 条目同形；只在 ``text`` 渲染成功时出现。
    """

    text: str | None = None
    unavailable: str | None = None
    unavailable_params: dict[str, object] = field(default_factory=dict)
    is_text_form: bool = False
    warnings: tuple[dict[str, Any], ...] = ()
    reference_count: int | None = None
    max_reference_images: int | None = None
    previous_storyboard: str | None = None


@dataclass(frozen=True)
class ItemPromptPreview:
    item_id: str
    content_mode: str
    storyboard_image: RenderedPrompt
    video: RenderedPrompt


def _unavailable_before_rendering(prompt: object) -> str | None:
    """渲染之前就能判定的不可用原因：待生成或为空；``None`` 表示该提示词值得渲染。"""
    if prompt is None:
        return UNAVAILABLE_PENDING
    if (isinstance(prompt, str) and not prompt.strip()) or prompt == {}:
        return UNAVAILABLE_MISSING
    return None


def _render(prompt: object, render: Any) -> RenderedPrompt:
    is_text_form = isinstance(prompt, str)
    if (reason := _unavailable_before_rendering(prompt)) is not None:
        return RenderedPrompt(unavailable=reason, is_text_form=is_text_form)
    try:
        return RenderedPrompt(text=render(prompt), is_text_form=is_text_form)
    except (ValueError, TypeError, KeyError):
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=is_text_form)


async def _render_reference_unit_video(
    project: dict[str, Any],
    script: dict[str, Any],
    unit: dict[str, Any],
    project_path: Path,
) -> RenderedPrompt:
    """参考视频单元的视频侧：与 Web 预览同一份请求投影与 renderer。"""
    try:
        projection = await project_reference_unit_request(
            project=project,
            script=script,
            unit=unit,
            project_path=project_path,
        )
        rendered = await asyncio.to_thread(
            reference_prompt_preview_facts,
            project=project,
            unit=unit,
            project_path=project_path,
            projection=projection,
        )
    except (ValueError, TypeError, KeyError):
        logger.warning("参考视频单元 %s 的字段不合规，无法渲染预览", unit.get("unit_id"), exc_info=True)
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=True)
    return RenderedPrompt(
        text=rendered["text"],
        unavailable=rendered["unavailable"],
        unavailable_params=rendered["unavailable_params"],
        is_text_form=rendered["is_text_form"],
        warnings=tuple(rendered["warnings"]),
    )


async def preview_item_prompts(
    project_name: str,
    script_file: str,
    item_id: str,
    *,
    projects: ProjectManager | None = None,
) -> ItemPromptPreview:
    """渲染分镜条目或参考视频单元的最终提示词文本。

    ``projects`` 供工具运行时把已解析的 ProjectManager 注入进来（REST 路由用进程默认实例）。
    """

    def _load() -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any], str, bool]:
        manager = projects if projects is not None else get_project_manager()
        project = manager.load_project(project_name)
        project_path = manager.get_project_path(project_name)
        script = manager.load_script(project_name, script_file)
        is_reference_unit = is_reference_video_project(project) and "video_units" in script
        items, id_field, *_ = (
            resolve_items(script, kind="video_units") if is_reference_unit else get_storyboard_items(script)
        )
        resolved = find_storyboard_item(items, id_field, item_id)
        if resolved is None:
            raise ScriptItemNotFound(item_id)
        content_mode = resolve_content_mode(script, project)
        return project, project_path, script, resolved[0], content_mode, is_reference_unit

    project, project_path, script, item, content_mode, is_reference_unit = await run_sync_transaction(_load)
    if is_reference_unit:
        return ItemPromptPreview(
            item_id=item_id,
            content_mode=content_mode,
            storyboard_image=RenderedPrompt(unavailable="storyboard_script_required"),
            video=await _render_reference_unit_video(project, script, item, project_path),
        )
    voice_characters = await resolve_voice_context(project, content_mode)

    def _assemble() -> StoryboardImageInput:
        script_input = resolve_usable_episode_script_input(
            project_path=project_path,
            project=project,
            script=script,
            script_filename=script_file,
        )
        return storyboard_image_input(
            project,
            script,
            episode=script_input.episode,
            resource_id=item_id,
            observation=project_input_observation(project_path),
        )

    image: RenderedPrompt | None = None
    generation_input: StoryboardImageInput | None = None
    image_prompt = item.get("image_prompt")
    is_text_form = isinstance(image_prompt, str)
    if image_prompt is not None and (reason := _unavailable_before_rendering(image_prompt)) is not None:
        image = RenderedPrompt(unavailable=reason, is_text_form=is_text_form)
    else:
        try:
            assembled = await asyncio.to_thread(_assemble)
        except (ValueError, TypeError, KeyError):
            image = RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=is_text_form)
        else:
            if (refused := assembled.refusal) is not None:
                refusal = input_refusal_error(refused)
                image = RenderedPrompt(
                    unavailable=refusal.key,
                    unavailable_params=dict(refusal.params),
                    is_text_form=is_text_form,
                )
            else:
                generation_input = assembled
    limit = (
        await reference_image_limit(project_name, project, project_path)
        if generation_input is not None and generation_input.references
        else (None, "")
    )

    def _render_both() -> ItemPromptPreview:
        storyboard_image = image
        if storyboard_image is None:
            assert generation_input is not None
            max_reference_images, model = limit
            try:
                rendered = generation_input.render(max_reference_images=max_reference_images, model=model)
            except ImageCapabilityError as exc:
                storyboard_image = RenderedPrompt(
                    unavailable=exc.code, unavailable_params=exc.params, is_text_form=is_text_form
                )
            except (ValueError, TypeError, KeyError):
                storyboard_image = RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=is_text_form)
            else:
                storyboard_image = RenderedPrompt(
                    text=rendered.prompt,
                    is_text_form=is_text_form,
                    warnings=rendered.warnings,
                )
        if generation_input is not None:
            storyboard_image = replace(
                storyboard_image,
                reference_count=len(generation_input.references),
                max_reference_images=limit[0],
                previous_storyboard=next(
                    (
                        ref.visual.logical_id
                        for ref in generation_input.references
                        if ref.visual.role == PREVIOUS_STORYBOARD_ROLE
                    ),
                    None,
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
            storyboard_image=storyboard_image,
            video=video,
        )

    return await asyncio.to_thread(_render_both)


__all__ = [
    "UNAVAILABLE_INVALID",
    "UNAVAILABLE_MISSING",
    "UNAVAILABLE_PENDING",
    "ItemPromptPreview",
    "RenderedPrompt",
    "ScriptItemNotFound",
    "preview_item_prompts",
]
