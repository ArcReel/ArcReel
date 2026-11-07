"""Resource paths: sole source for resource type → project-relative path.

Pure functions, no disk reads/project state. Own subdirectories, filename templates, extensions, and
prefixes for storyboards/end_frames/videos (``scene_``) and audio (``segment_``).

Writers (MediaGenerator), restoration (versions routes), import repair (project_archive), and
VersionManager share these shapes without independent drift. Callers own boundary checks when
joining absolute paths; they are outside this module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourcePattern:
    """Path shape for one resource type.

    ``id_segments`` is resource-ID segment count, default 1 (filename body). Character derivatives use 2
    (``本体名/衍生名``, base name as directory)."""

    subdir: str
    extension: str
    prefix: str = ""  # Prefixes: storyboards/videos "scene_", audio "segment_", others empty.
    id_segments: int = 1


# End-frame resource type, exported from this dependency-free pure module for
# server/services/project/end_frame.py writers and generation_tasks.py readers, avoiding mutual
# server.services imports/cycles. Also the sole source for its `_PATTERNS` key,
# preventing independent literals from splitting read/write path rules.
END_FRAME_RESOURCE_TYPE = "end_frames"

#: Character-derivative sheet type: shared base identity, resource ID ``本体名/衍生名``;
#: use a dedicated subdirectory, not ``characters/``, since bases may themselves be named ``derivatives``.
#: Like END_FRAME_RESOURCE_TYPE, export from this dependency-free module so writers/history/
#: artifact planning share one literal.
CHARACTER_DERIVATIVE_RESOURCE_TYPE = "character_derivatives"

_PATTERNS: dict[str, ResourcePattern] = {
    "storyboards": ResourcePattern("storyboards", ".png", prefix="scene_"),
    # End frames share storyboard IDs and scene_ prefixes with storyboard images/videos.
    END_FRAME_RESOURCE_TYPE: ResourcePattern(END_FRAME_RESOURCE_TYPE, ".png", prefix="scene_"),
    "videos": ResourcePattern("videos", ".mp4", prefix="scene_"),
    "characters": ResourcePattern("characters", ".png"),
    "scenes": ResourcePattern("scenes", ".png"),
    "props": ResourcePattern("props", ".png"),
    "products": ResourcePattern("products", ".png"),
    "grids": ResourcePattern("grids", ".png"),
    "reference_videos": ResourcePattern("reference_videos", ".mp4"),
    "audio": ResourcePattern("audio", ".wav", prefix="segment_"),
    CHARACTER_DERIVATIVE_RESOURCE_TYPE: ResourcePattern("characters/derivatives", ".png", id_segments=2),
}

RESOURCE_TYPES: tuple[str, ...] = tuple(_PATTERNS)


def _pattern(resource_type: str) -> ResourcePattern:
    pattern = _PATTERNS.get(resource_type)
    if pattern is None:
        raise ValueError(f"不支持的资源类型: {resource_type}")
    return pattern


def resource_relative_path(resource_type: str, resource_id: str) -> str:
    """Return resource project-relative POSIX paths with forward slashes.

    storyboards/end_frames/videos use ``storyboards/scene_{id}.png`` shapes; audio uses
    ``audio/segment_{id}.wav``; others ``{subdir}/{id}{ext}``. Unknown types raise ``ValueError``."""
    pattern = _pattern(resource_type)
    filename = f"{pattern.prefix}{resource_id}"
    return f"{pattern.subdir}/{filename}{pattern.extension}"


def resource_extension(resource_type: str) -> str:
    """Resource extensions including dots, e.g. ``.png``; unknown types raise ``ValueError``."""
    return _pattern(resource_type).extension


def resource_id_segments(resource_type: str) -> int:
    """Resource-ID path-segment count for this type; unknown types raise ``ValueError``."""
    return _pattern(resource_type).id_segments


def version_snapshot_dir(resource_type: str) -> str:
    """Project-relative POSIX snapshot bucket; unknown types raise ``ValueError``."""
    return f"versions/{_pattern(resource_type).subdir}"


def version_snapshot_relative_path(resource_type: str, resource_id: str, *, version: int, timestamp: str) -> str:
    """Return project-relative POSIX version-snapshot paths.

    ``versions/{subdir}/{id 的前置段}/{id 末段}_v{n}_{timestamp}{ext}`` preserves preceding ID segments
    as directories, matching current ``{subdir}/{id}{ext}`` hierarchy. Unknown types raise ``ValueError``."""
    pattern = _pattern(resource_type)
    parent, _, stem = resource_id.rpartition("/")
    parent_prefix = f"{parent}/" if parent else ""
    return f"versions/{pattern.subdir}/{parent_prefix}{stem}_v{version}_{timestamp}{pattern.extension}"


def resource_of_path(rel: str) -> tuple[str, str] | None:
    """Inverse :func:`resource_relative_path`: relative path → ``(资源类型, 资源 ID)``, or ``None`` unless a current-resource file.

    Match deeper types first: ``characters/derivatives/a/b.png`` is derivative ``a/b``, not character ``derivatives/a/b``."""
    for resource_type, pattern in sorted(_PATTERNS.items(), key=lambda item: -item[1].subdir.count("/")):
        head = f"{pattern.subdir}/{pattern.prefix}"
        if not (rel.startswith(head) and rel.endswith(pattern.extension)):
            continue
        resource_id = rel[len(head) : len(rel) - len(pattern.extension)]
        if (
            resource_id
            and resource_id.count("/") == pattern.id_segments - 1
            and resource_relative_path(resource_type, resource_id) == rel
        ):
            return resource_type, resource_id
    return None
