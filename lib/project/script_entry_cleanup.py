"""被整份替换的正式脚本条目的媒体清理：当前文件与版本历史一并消失。

内容确认覆盖正式脚本时，新脚本里沿用旧编号的条目是一个全新的身份，旧条目名下的产物已在覆盖清单里
列为丢失。留着它们的版本历史或当前文件，下一次生成会把旧内容接成新条目的历史（版本记录延续旧编号，
盘上的旧文件被登记为第一版），回滚就能回到不属于它的画面。

单列一个模块是因为它同时需要 :mod:`lib.artifacts.version_manager`（历史）与
:mod:`lib.project.resource_paths`（当前文件路径），与 :mod:`lib.project.asset_derivative_cleanup` 同构。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from lib.artifacts.version_manager import VersionManager
from lib.infra.path_safety import try_safe_join
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path

logger = logging.getLogger(__name__)

#: 按条目编号命名的媒体资源类型：分镜图、尾帧快照、分镜视频、参考生视频单元视频、旁白配音。
ENTRY_MEDIA_RESOURCE_TYPES: tuple[str, ...] = (
    "storyboards",
    END_FRAME_RESOURCE_TYPE,
    "videos",
    "reference_videos",
    "audio",
)


def purge_replaced_entry_media(project_dir: Path, entry_ids: Iterable[str]) -> None:
    """清掉这些条目编号名下的版本历史与当前媒体文件。

    版本历史先于当前文件清：中途失败留下的是「无人引用的字节」，而不是「指向已删文件的历史」。
    文件删不掉只记日志——正式脚本与产物清单已经不再引用它。
    """
    ids = tuple(dict.fromkeys(entry_ids))
    if not ids:
        return
    versions = VersionManager(project_dir)
    for entry_id in ids:
        for resource_type in ENTRY_MEDIA_RESOURCE_TYPES:
            versions.purge_resource(resource_type, entry_id)
    for entry_id in ids:
        for resource_type in ENTRY_MEDIA_RESOURCE_TYPES:
            # 编号来自磁盘上的旧剧本，不可信任：越界的编号解析不出路径，跳过。
            path = try_safe_join(project_dir, resource_relative_path(resource_type, entry_id))
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("被替换条目 %s 的 %s 文件删除失败，已解除引用", entry_id, resource_type, exc_info=True)
