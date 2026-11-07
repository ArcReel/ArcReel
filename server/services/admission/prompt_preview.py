"""Preview rendering of final entry prompts.

Show the exact image/video-model prompt if this entry were generated now. Use the same rendering functions as execution:
``lib.prompts.prompt_builders.render_storyboard_image_prompt`` /
``lib.prompts.prompt_utils.render_storyboard_video_prompt``. Storyboard images share execution’s generation input
(``lib.artifacts.generation_input``): rejected generation makes that side unavailable with gap codes; otherwise truncate
references using the same backend limit and render matching ``图N`` numbering. Truncation includes warnings shaped like
runtime results so users know before generation that some references will not be sent. Video previews resolve content mode
and voice bindings using execution rules.

Read-only: no provider requests, costs or manifest writes. Reference-video unit bodies are the prompt’s main content;
their previews use ``lib.script.reference_video.script_preview``, outside this module."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_input_claims import resolve_usable_episode_script_input
from lib.artifacts.generation_input import (
    StoryboardImageInput,
    project_input_observation,
    storyboard_image_input,
)
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.prompts.prompt_utils import render_storyboard_video_prompt
from lib.script.script_models import resolve_content_mode
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items
from server.services.admission.reference_admission import input_refusal_error
from server.services.admission.video_batch_admission import resolve_voice_context
from server.services.tasks.generation_context import ImageLaneRequest, resolve_generation_context

logger = logging.getLogger(__name__)

#: Unavailability for pending prompts, ``None`` or absent fields, not yet authored after mechanical conversion.
UNAVAILABLE_PENDING = "prompt_preview_pending"
#: Unavailability for present but empty prompt fields, empty strings/objects.
UNAVAILABLE_MISSING = "prompt_preview_missing"
#: Unavailability for present malformed prompts rejected by rendering.
UNAVAILABLE_INVALID = "prompt_preview_invalid"


class ScriptItemNotFound(LookupError):
    """The item ID does not exist in the script."""


@dataclass(frozen=True)
class RenderedPrompt:
    """Rendering result for one prompt side: exactly one of ``text`` and ``unavailable`` is non-``None``.

    ``warnings`` contains nonblocking rendering hints such as backend-limit reference truncation, shaped like task
    ``result.warnings`` ``{key, params}`` entries and present only when ``text`` renders successfully."""

    text: str | None = None
    unavailable: str | None = None
    #: Message parameters for unavailability; rejected generation input includes all gaps, shaped like runtime error details.
    unavailable_params: dict[str, object] = field(default_factory=dict)
    is_text_form: bool = False
    warnings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ItemPromptPreview:
    item_id: str
    content_mode: str
    storyboard_image: RenderedPrompt
    video: RenderedPrompt


def _unavailable_before_rendering(prompt: object) -> str | None:
    """Unavailability detectable before rendering: pending or empty; ``None`` means the prompt should be rendered."""
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


async def reference_image_limit(
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
) -> tuple[int | None, str]:
    """Image-lane reference limit and model name, sharing execution truncation rules; ``None`` means no truncation.

    Resolution constructs a backend requiring credentials. Missing credentials/providers must not prevent read-only prompt
    inspection: resolution failure disables truncation and logs info. Such projects cannot generate anyway, preserving
    preview/execution text consistency. Preview resolves current project configuration without task payloads; a queued
    payload pinning another provider with different limits may have different numbering, a known limitation because
    preview does not know that payload."""
    try:
        ctx = await resolve_generation_context(
            project_name,
            None,
            project=project,
            project_path=project_path,
            image=ImageLaneRequest(generation_type="i2i"),
        )
    except Exception as exc:
        logger.info("预览无法解析 image lane，参考图不按后端上限裁剪：%s", exc)
        return None, ""
    return ctx.image.max_reference_images, ctx.image.backend_model


async def preview_item_prompts(
    project_name: str,
    script_file: str,
    item_id: str,
    *,
    projects: ProjectManager | None = None,
) -> ItemPromptPreview:
    """Render final storyboard-image and video prompts for one storyboard entry.

    ``projects`` lets tool runtimes inject an already resolved ProjectManager; REST routes use the process default."""

    def _load() -> tuple[dict[str, Any], Path, dict[str, Any], dict[str, Any], str]:
        manager = projects if projects is not None else get_project_manager()
        project = manager.load_project(project_name)
        project_path = manager.get_project_path(project_name)
        script = manager.load_script(project_name, script_file)
        items, id_field, *_ = get_storyboard_items(script)
        resolved = find_storyboard_item(items, id_field, item_id)
        if resolved is None:
            raise ScriptItemNotFound(item_id)
        return project, project_path, script, resolved[0], resolve_content_mode(script, project)

    project, project_path, script, item, content_mode = await run_sync_transaction(_load)
    # Resolve voice bindings from current project video capabilities, None for nondrama/silent paths, matching execution.
    voice_characters = await resolve_voice_context(project, content_mode)

    def _assemble() -> StoryboardImageInput:
        # Share execution input, including previous-storyboard availability, so ``图N`` numbering matches exactly;
        # unregistered/changed script content raises ValueError as an unrenderable prompt, while Manifest failures
        # (ArtifactManifestError) remain fail-loud.
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
    # Empty prompts cannot render; generation input handles pending prompts to report them with all reference gaps.
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
            except (ValueError, TypeError, KeyError):
                storyboard_image = RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=is_text_form)
            else:
                storyboard_image = RenderedPrompt(
                    text=rendered.prompt,
                    is_text_form=is_text_form,
                    warnings=rendered.warnings,
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
