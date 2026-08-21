"""
项目封面选择器（读时计算）。

在项目大厅列出项目时，按偏好顺序挑一个可用作封面的相对资源路径：

    1. 已生成视频的首帧 `video_thumbnail`
       —— 分镜图生视频写在 `segments[*].generated_assets.video_thumbnail`，
          参考生视频写在 `video_units[*].generated_assets.video_thumbnail`，
          均由 `lib/thumbnail.extract_video_thumbnail` 在生成完成后抽出并落盘。
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

from lib.script_models import get_generated_assets

if TYPE_CHECKING:
    from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


def resolve_project_cover(
    manager: ProjectManager,
    project_name: str,
    project: dict,
    *,
    preloaded_scripts: dict[str, dict] | None = None,
) -> str | None:
    """按偏好顺序挑第一个可用的封面路径，返回 `/api/v1/files/...` URL；全无则 None。

    ``preloaded_scripts`` 允许调用方（如 list_projects）一次性加载剧本后同时喂给
    项目摘要投影，避免两路重复 JSON I/O。key 为 ``episode['script_file']``
    原值，value 为剧本 JSON dict；缺失集 (key 不在 map) 回退到 ``manager.load_script``。
    """

    def _url(rel: str) -> str:
        # 统一走 files 静态路由，不直接拼盘路径；相对路径原样透传给 FileResponse。
        return f"/api/v1/files/{project_name}/{rel.lstrip('/')}"

    # 第一趟：遍历所有 episode 的 script，先整体扫 video_thumbnail，再整体扫
    # storyboard_image。两趟分开而不是在同一 item 上并列判断，是为了保证
    # "任何一集有视频首帧" 都胜过 "仅有分镜图"，而不是被 episode 顺序锁死。
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
            # 剧本文件缺失 / 损坏 / 路径越界不阻塞项目列表；仅记 debug，继续尝试其他集。
            # OSError 覆盖 FileNotFoundError 等 I/O 失败；ValueError 覆盖 JSONDecodeError
            # 与 ProjectManager._safe_subpath 的非法路径校验。与 list_projects 的外层
            # preload except 口径保持一致。
            logger.debug("加载剧本失败 project=%s script=%s err=%s", project_name, script_file, e)
            continue

    def _iter_items(script: dict):
        # 合并分镜图生视频的 segments 与参考生视频的 video_units；两种键共存或其中一方为空壳时，
        # 任一结构都不能遮蔽另一结构的真实资产。`if thumb` / `if sb` 的 falsy 过滤忽略空壳 item。
        return [*(script.get("segments") or []), *(script.get("video_units") or [])]

    for script in scripts:
        for item in _iter_items(script):
            ga = get_generated_assets(item or {})
            thumb = ga.get("video_thumbnail")
            if thumb:
                return _url(thumb)

    for script in scripts:
        for item in _iter_items(script):
            ga = get_generated_assets(item or {})
            sb = ga.get("storyboard_image")
            if sb:
                return _url(sb)

    # 项目级资产：scene > character（见模块 docstring 的抉择）
    for data in (project.get("scenes") or {}).values():
        sheet = (data or {}).get("scene_sheet")
        if sheet:
            return _url(sheet)

    for data in (project.get("characters") or {}).values():
        sheet = (data or {}).get("character_sheet")
        if sheet:
            return _url(sheet)

    return None
