"""资产图草稿提示词预览：草稿描述替换进生成输入后走执行期同一渲染出口，不提交生成或保存描述。

与执行器取同一份生成输入（``lib.artifacts.generation_input``）：生成会被拒时（描述为空、原图读不到、
衍生的本体资产图不可用）预览不可用并带缺口码；否则按与执行器相同的图像后端参考图上限裁剪后渲染，
裁剪发生时带上与执行期任务结果同形的 warning。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.generation_input import (
    AssetSheetInput,
    DerivativeSheetInput,
    InputRefused,
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
    """项目载荷的副本，目标条目的描述换成草稿；只复制草稿所在的那条路径。"""

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
    """按资产执行口径渲染草稿描述：衍生只编辑本体外观，画风由本体图承载。

    ``asset_key`` / ``derivative_key`` 是资产表与衍生表里的落盘真名。
    """

    draft = _with_draft_description(
        project, asset_type=asset_type, asset_key=asset_key, derivative_key=derivative_key, description=description
    )

    def _assemble() -> AssetSheetInput | DerivativeSheetInput | InputRefused:
        observation = project_input_observation(project_path)
        if derivative_key is None:
            return asset_sheet_input(draft, asset_type=asset_type, name=asset_key, observation=observation)
        return derivative_sheet_input(draft, owner=asset_key, derivative=derivative_key, observation=observation)

    try:
        generation_input = await asyncio.to_thread(_assemble)
    except (ValueError, TypeError, KeyError):
        return RenderedPrompt(unavailable=UNAVAILABLE_INVALID, is_text_form=True)
    if isinstance(generation_input, InputRefused):
        refusal = input_refusal_error(generation_input)
        return RenderedPrompt(unavailable=refusal.key, unavailable_params=dict(refusal.params), is_text_form=True)
    max_reference_images, model = (
        await reference_image_limit(project_name, dict(project), project_path)
        if generation_input.references
        else (0, "")
    )
    rendered = generation_input.render(max_reference_images=max_reference_images, model=model)
    return RenderedPrompt(text=rendered.prompt, is_text_form=True, warnings=rendered.warnings)
