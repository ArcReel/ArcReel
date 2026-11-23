"""
项目封面选择器（读时计算）。

在项目大厅列出项目时，按偏好顺序挑一个可用作封面的相对资源路径：

    1. 已生成视频的首帧 `video_thumbnail`
       —— 写在剧本条目的 `generated_assets.video_thumbnail`：分镜图生视频按创作类型落在
          `segments`（narration）/ `scenes`（drama）/ `shots`（ad），参考生视频落在
          `video_units`；条目数组键取自骨架注册表 `SKELETONS`。均由
          `lib/infra/thumbnail.extract_video_thumbnail` 在生成完成后抽出并落盘。
       最能代表"项目当前产出进度"的资产，优先级最高。

    2. 已生成的分镜图 `storyboard_image`
       —— 分镜图生视频还没完成视频、但已出分镜图时的次优选择；
          参考生视频该字段永远为 None，自然跳过。

    3. 场景参考图 `scene_sheet`
       —— 参考生视频核心兜底：即使一次视频都没生成，也能用一张场景资产图
          展现项目美术风格。scene > character 是因为环境/空间感更像"封面"。

    4. 角色参考图 `character_sheet`
       —— 最后兜底，仅剧情向项目才可能缺 scene 保留 character。

    5. None —— 前端渲染占位 FolderOpen 图标。

与项目摘要投影同走"读时计算、不存冗余"的约定：不往 project.json
里写 `cover_thumbnail` 字段，每次 list_projects 按当前磁盘真相重新解析。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.infra.path_safety import try_safe_join
from lib.script.script_models import get_generated_assets
from lib.script.script_skeleton import SKELETONS

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)


def resolve_project_cover(
    manager: ProjectManager,
    project_name: str,
    project: dict,
    *,
    preloaded_scripts: dict[str, dict] | None = None,
) -> str | None:
    """按偏好顺序挑第一个可用的封面路径，返回 `/api/v1/files/...` URL（文件在场时带 ``?v=``）；全无则 None。

    ``preloaded_scripts`` 允许调用方（如 list_projects）一次性加载剧本后同时喂给
    项目摘要投影，避免两路重复 JSON I/O。key 为 ``episode['script_file']``
    原值，value 为剧本 JSON dict；缺失集 (key 不在 map) 回退到 ``manager.load_script``。
    """

    def _url(rel: str) -> str:
        url = f"/api/v1/files/{project_name}/{rel.lstrip('/')}"
        try:
            project_dir = manager.get_project_path(project_name)
        except (OSError, ValueError):
            return url
        resolved = try_safe_join(project_dir, rel, require_file=True)
        if resolved is None:
            return url
        try:
            mtime_ns = resolved.stat().st_mtime_ns
        except OSError:
            return url
        return f"{url}?v={mtime_ns}"

    scripts: list[dict] = []
    for ep in project.get("episodes") or []:
        script_file = ep.get("script_file")
        if not script_file:
            continue
        if preloaded_scripts is not None and script_file in preloaded_scripts:
            scripts.append(preloaded_scripts[script_file])
            continue
        try:
            scripts.append(manager.load_script(project_name, script_file))
        except (OSError, ValueError) as e:
            logger.debug("加载剧本失败 project=%s script=%s err=%s", project_name, script_file, e)
            continue

    def _iter_items(script: dict) -> list[dict]:
        items: list[dict] = []
        for kind in SKELETONS:
            raw = script.get(kind)
            if isinstance(raw, list):
                items.extend(item for item in raw if isinstance(item, dict))
        return items

    for script in scripts:
        for item in _iter_items(script):
            thumb = get_generated_assets(item).get("video_thumbnail")
            if isinstance(thumb, str) and thumb:
                return _url(thumb)

    for script in scripts:
        for item in _iter_items(script):
            sb = get_generated_assets(item).get("storyboard_image")
            if isinstance(sb, str) and sb:
                return _url(sb)

    for data in (project.get("scenes") or {}).values():
        sheet = (data or {}).get("scene_sheet")
        if sheet:
            return _url(sheet)

    for data in (project.get("characters") or {}).values():
        sheet = (data or {}).get("character_sheet")
        if sheet:
            return _url(sheet)

    return None
