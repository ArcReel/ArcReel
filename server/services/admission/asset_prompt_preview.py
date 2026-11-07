"""Asset-image draft prompt preview: substitute the draft description into generation input and use the runtime rendering path without submitting generation or saving the description.

Use the same generation input as the executor (``lib.artifacts.generation_input``). When generation would be rejected
(an empty description, unreadable original image, or unavailable base asset image for a derivative), the preview is unavailable
with a gap code. Otherwise, truncate references to the same image-backend limit as the executor before rendering.
When truncation occurs, include a warning with the same shape as runtime task results."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.generation_input import (
    AssetSheetInput,
    DerivativeSheetInput,
    asset_sheet_input,
    derivative_sheet_input,
    project_input_observation,
)
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from server.services.admission.prompt_preview import UNAVAILABLE_INVALID, RenderedPrompt, reference_image_limit
from server.services.admission.reference_admission import input_refusal_error


def _with_draft_description(
    project: Mapping[str, Any],
    *,
    asset_type: str,
    asset_key: str,
    derivative_key: str | None,
    description: str,
) -> dict[str, Any]:
    """Copy the project payload with the target description replaced by the draft; copy only the path containing the draft."""

    bucket_key = ASSET_SPECS[asset_type].bucket_key
    draft = dict(project)
    bucket = draft[bucket_key] = dict(project[bucket_key])
    entry = bucket[asset_key] = dict(bucket[asset_key])
    if derivative_key is None:
        entry["description"] = description
    else:
        table = entry[DERIVATIVES_FIELD] = dict(entry[DERIVATIVES_FIELD])
        table[derivative_key] = {**table[derivative_key], "description": description}
    return draft


async def render_asset_prompt(
    project_name: str,
    project: Mapping[str, Any],
    project_path: Path,
    *,
    asset_type: str,
    asset_key: str,
    derivative_key: str | None,
    description: str,
) -> RenderedPrompt:
    """Render a draft description using asset execution rules: derivatives edit only the base appearance; the base image carries the style.

    ``asset_key`` / ``derivative_key`` are the actual persisted names in the asset and derivative tables."""

    def _assemble() -> AssetSheetInput | DerivativeSheetInput:
        draft = _with_draft_description(
            project, asset_type=asset_type, asset_key=asset_key, derivative_key=derivative_key, description=description
        )
        observation = project_input_observation(project_path)
        if derivative_key is None:
            return asset_sheet_input(draft, asset_type=asset_type, name=asset_key, observation=observation)
        return derivative_sheet_input(draft, owner=asset_key, derivative=derivative_key, observation=observation)

    try:
        generation_input = await asyncio.to_thread(_assemble)
    except (ValueError, TypeError, KeyError):
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=True)
    if (refused := generation_input.refusal) is not None:
        refusal = input_refusal_error(refused)
        return RenderedPrompt(unavailable=refusal.key, unavailable_params=dict(refusal.params), is_text_form=True)
    max_reference_images, model = (
        await reference_image_limit(project_name, dict(project), project_path)
        if generation_input.references
        else (None, "")
    )
    rendered = generation_input.render(max_reference_images=max_reference_images, model=model)
    return RenderedPrompt(text=rendered.prompt, is_text_form=True, warnings=rendered.warnings)
