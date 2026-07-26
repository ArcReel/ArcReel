"""资源路径解析器 — 「资源类型 → 项目内相对路径」的唯一真相源。

纯函数，不读盘、不持有项目状态。独家拥有各资源类型的子目录、文件名模板、
扩展名，以及 storyboards/end_frames/videos（``scene_``）、audio（``segment_``）的文件名前缀。

写侧（MediaGenerator）、版本回溯（versions 路由）、导入修复（project_archive）、
版本管理（VersionManager）都从这里取形状，避免副本各自漂移。越界校验不在此处，
由调用方拼绝对路径时自行负责。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourcePattern:
    """单一资源类型的路径形状。"""

    subdir: str
    extension: str
    prefix: str = ""  # 文件名前缀：storyboards/videos 用 "scene_"，audio 用 "segment_"，其余空


# 尾帧快照的资源类型名。独立导出到这个无反向依赖的纯函数模块，供
# server/services/end_frame.py（写侧）与 generation_tasks.py（读侧）共用，避免二者互相
# import 对方所在的 server.services 包造成循环依赖；同时作为 `_PATTERNS` 对应 key 的唯一
# 来源，防止两处字面量各自维护后读写侧路径口径分叉。
END_FRAME_RESOURCE_TYPE = "end_frames"

_PATTERNS: dict[str, ResourcePattern] = {
    "storyboards": ResourcePattern("storyboards", ".png", prefix="scene_"),
    # 尾帧快照与分镜图、镜头视频同按镜头 id 命名，故共用 scene_ 前缀。
    END_FRAME_RESOURCE_TYPE: ResourcePattern(END_FRAME_RESOURCE_TYPE, ".png", prefix="scene_"),
    "videos": ResourcePattern("videos", ".mp4", prefix="scene_"),
    "characters": ResourcePattern("characters", ".png"),
    "scenes": ResourcePattern("scenes", ".png"),
    "props": ResourcePattern("props", ".png"),
    "products": ResourcePattern("products", ".png"),
    "grids": ResourcePattern("grids", ".png"),
    "reference_videos": ResourcePattern("reference_videos", ".mp4"),
    "audio": ResourcePattern("audio", ".wav", prefix="segment_"),
}

RESOURCE_TYPES: tuple[str, ...] = tuple(_PATTERNS)


def _pattern(resource_type: str) -> ResourcePattern:
    pattern = _PATTERNS.get(resource_type)
    if pattern is None:
        raise ValueError(f"不支持的资源类型: {resource_type}")
    return pattern


def resource_relative_path(resource_type: str, resource_id: str) -> str:
    """返回资源在项目内的相对路径（posix，正斜杠）。

    storyboards/end_frames/videos 形如 ``storyboards/scene_{id}.png``、audio 形如 ``audio/segment_{id}.wav``；
    其余 ``{subdir}/{id}{ext}``。未知类型抛 ``ValueError``。
    """
    pattern = _pattern(resource_type)
    filename = f"{pattern.prefix}{resource_id}"
    return f"{pattern.subdir}/{filename}{pattern.extension}"


def resource_extension(resource_type: str) -> str:
    """返回资源类型的文件扩展名（含点，如 ``.png``）。未知类型抛 ``ValueError``。"""
    return _pattern(resource_type).extension
