"""资源路径解析器 — 「资源类型 → 项目内相对路径」的唯一真相源。

纯函数，不读盘、不持有项目状态。独家拥有各资源类型的子目录、文件名模板、
扩展名，以及 storyboards/videos 的 ``scene_`` 前缀特例。

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
    scene_prefix: bool  # storyboards/videos 的文件名带 scene_ 前缀


_PATTERNS: dict[str, ResourcePattern] = {
    "storyboards": ResourcePattern("storyboards", ".png", scene_prefix=True),
    "videos": ResourcePattern("videos", ".mp4", scene_prefix=True),
    "characters": ResourcePattern("characters", ".png", scene_prefix=False),
    "scenes": ResourcePattern("scenes", ".png", scene_prefix=False),
    "props": ResourcePattern("props", ".png", scene_prefix=False),
    "grids": ResourcePattern("grids", ".png", scene_prefix=False),
    "reference_videos": ResourcePattern("reference_videos", ".mp4", scene_prefix=False),
}

RESOURCE_TYPES: tuple[str, ...] = tuple(_PATTERNS)


def _pattern(resource_type: str) -> ResourcePattern:
    pattern = _PATTERNS.get(resource_type)
    if pattern is None:
        raise ValueError(f"不支持的资源类型: {resource_type}")
    return pattern


def resource_relative_path(resource_type: str, resource_id: str) -> str:
    """返回资源在项目内的相对路径（posix，正斜杠）。

    storyboards/videos 形如 ``storyboards/scene_{id}.png``；其余 ``{subdir}/{id}{ext}``。
    未知类型抛 ``ValueError``。
    """
    pattern = _pattern(resource_type)
    filename = f"scene_{resource_id}" if pattern.scene_prefix else resource_id
    return f"{pattern.subdir}/{filename}{pattern.extension}"


def resource_extension(resource_type: str) -> str:
    """返回资源类型的文件扩展名（含点，如 ``.png``）。未知类型抛 ``ValueError``。"""
    return _pattern(resource_type).extension
