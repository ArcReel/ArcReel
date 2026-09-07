"""按脚本规划机械转为正式脚本。

内容确认之后不经文本模型直接落正式剧本：条目内容层按脚本规划投影，drama / narration 的
``image_prompt`` / ``video_prompt`` 以 ``None`` 落盘等待补写（参考生视频的单元正文即提示词，
逐字复制即算已编写）。已有正式剧本时只做集合同步（新增 / 移出 / 改序），失效条目连同内容、
提示词与指纹原样保留；``entry_ids`` 点名的失效条目「采用新内容」。

内容确认门禁与 ``generate_episode_script`` 共用同一道预检（``episode_generation_preflight``）：
未确认、待修复草稿在场、脚本规划缺失时同样拒绝。REST 路由与 MCP 工具都经本模块，两个 host
同一入口。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from lib.config.resolver import ConfigResolver
from lib.project_manager import ProjectManager, get_project_manager
from lib.script_generator import (
    ScriptGenerator,
    ScriptPlanConversionPreview,
    ScriptPlanConversionReceipt,
    ScriptPlanNotFoundError,
)
from server.text_generation import episode_generation_preflight


async def convert_script_plan(
    project_name: str,
    episode: int,
    *,
    entry_ids: Iterable[str] | None = None,
    projects: ProjectManager | None = None,
    config_resolver: ConfigResolver | None = None,
) -> ScriptPlanConversionReceipt:
    """把第 ``episode`` 集的脚本规划机械转为正式剧本，返回新增 / 采用新内容 / 移出三组条目 id。

    ``projects`` / ``config_resolver`` 供工具运行时把已解析的实例注入进来；REST 路由用进程默认值。
    内容确认未通过时抛 ``server.text_generation.TextGenerationError``。
    """
    manager = projects if projects is not None else get_project_manager()
    project_path = manager.get_project_path(project_name)
    await asyncio.to_thread(episode_generation_preflight, project_path, episode, enforce_review_gate=True)
    generator = await asyncio.to_thread(ScriptGenerator, project_path, config_resolver=config_resolver)
    return await generator.convert_script_plan(episode, entry_ids=entry_ids)


async def preview_script_plan_conversion(
    project_name: str,
    episode: int,
    *,
    projects: ProjectManager | None = None,
    config_resolver: ConfigResolver | None = None,
) -> ScriptPlanConversionPreview:
    """只读预演第 ``episode`` 集的机械转换：新增 / 失效 / 移出三组条目 id，不落盘、不经内容确认门禁。

    脚本规划缺失抛 ``ScriptPlanNotFoundError``；项目或项目元数据缺失仍是普通 ``FileNotFoundError``。
    """
    manager = projects if projects is not None else get_project_manager()
    project_path = manager.get_project_path(project_name)
    generator = await asyncio.to_thread(ScriptGenerator, project_path, config_resolver=config_resolver)
    return await generator.preview_script_plan_conversion(episode)


__all__ = [
    "ScriptPlanConversionPreview",
    "ScriptPlanConversionReceipt",
    "ScriptPlanNotFoundError",
    "convert_script_plan",
    "preview_script_plan_conversion",
]
