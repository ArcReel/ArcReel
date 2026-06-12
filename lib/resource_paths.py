"""资源路径解析器 — 「资源类型 → 项目内相对路径」的唯一真相源。

纯函数，不读盘、不持有项目状态。独家拥有各资源类型的子目录、文件名模板、
扩展名，以及 storyboards/videos（``scene_``）、audio（``segment_``）的文件名前缀。

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


_PATTERNS: dict[str, ResourcePattern] = {
    "storyboards": ResourcePattern("storyboards", ".png", prefix="scene_"),
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

    storyboards/videos 形如 ``storyboards/scene_{id}.png``、audio 形如 ``audio/segment_{id}.wav``；
    其余 ``{subdir}/{id}{ext}``。未知类型抛 ``ValueError``。
    """
    pattern = _pattern(resource_type)
    filename = f"{pattern.prefix}{resource_id}"
    return f"{pattern.subdir}/{filename}{pattern.extension}"


def resource_extension(resource_type: str) -> str:
    """返回资源类型的文件扩展名（含点，如 ``.png``）。未知类型抛 ``ValueError``。"""
    return _pattern(resource_type).extension
