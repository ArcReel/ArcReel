"""角色 / 场景 / 道具 / 商品资产图最终提示词的预览渲染。

回答「现在点生成，会送进图像模型的 prompt 逐字是什么」。与执行路径
（``server.services.generation_tasks.execute_character_task`` /
``execute_design_task``）共用同一份 ``lib.prompt_builders`` 出口，description / style /
style_description 的取值逻辑与 ``server.routers.generate._enqueue_asset_generation`` 同源，
避免预览与执行漂移。

只读：不产生费用、不入队、不写产物清单。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from lib.asset_types import ASSET_SPECS, resolve_asset_key
from lib.project_manager import ProjectManager, get_project_manager
from lib.prompt_builders import (
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)

_PROMPT_BUILDERS: dict[str, Any] = {
    "character": build_character_prompt,
    "scene": build_scene_prompt,
    "prop": build_prop_prompt,
    "product": build_product_prompt,
}


class AssetNotFound(LookupError):
    """项目里不存在该资产。"""


@dataclass(frozen=True)
class AssetPromptPreview:
    asset_type: str
    resource_id: str
    prompt: str


async def preview_asset_prompt(
    project_name: str,
    asset_type: str,
    resource_name: str,
    *,
    projects: ProjectManager | None = None,
) -> AssetPromptPreview:
    """渲染该资产现在生成会用到的完整 prompt 文本。

    ``projects`` 供调用方注入已解析的 ProjectManager（REST 路由用进程默认实例）。
    """
    spec = ASSET_SPECS[asset_type]
    builder = _PROMPT_BUILDERS[asset_type]

    def _load() -> AssetPromptPreview:
        manager = projects if projects is not None else get_project_manager()
        project = manager.load_project(project_name)
        bucket = project.get(spec.bucket_key)
        resolved = resolve_asset_key(bucket, resource_name)
        if resolved is None:
            raise AssetNotFound(resource_name)
        entry = bucket.get(resolved) if isinstance(bucket, dict) else None
        description = entry.get("description", "") if isinstance(entry, dict) else ""
        style = project.get("style", "")
        style_description = project.get("style_description", "")
        prompt = builder(
            resolved,
            description if isinstance(description, str) else "",
            style if isinstance(style, str) else "",
            style_description if isinstance(style_description, str) else "",
        )
        return AssetPromptPreview(asset_type=asset_type, resource_id=resolved, prompt=prompt)

    return await asyncio.to_thread(_load)


__all__ = ["AssetNotFound", "AssetPromptPreview", "preview_asset_prompt"]
