"""Canonical direct-input bases for structured content artifacts.

These builders intentionally accept the full project mapping but project only formal
content semantics. Execution configuration does not participate in the currency of an
existing structured artifact.
"""

from __future__ import annotations

from collections.abc import Mapping

from lib.artifact_manifest import ArtifactBasis

_STRUCTURED_CREATION_TYPES = frozenset({"narration", "drama"})
_GENERATION_MODES = frozenset({"storyboard", "reference_video"})
_SOURCE_FILE_TYPES = frozenset({"novel", "screenplay"})
_DEFAULT_SOURCE_LANGUAGE = "中文"


def build_step1_basis(source_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the formal source inputs consumed by one episode's step1 artifact."""

    creation_type, generation_mode = _creation_axes(project)
    raw_source_file_type = project.get("source_file_type")
    source_file_type = "novel" if raw_source_file_type is None else raw_source_file_type
    if not isinstance(source_file_type, str) or source_file_type not in _SOURCE_FILE_TYPES:
        raise ValueError(f"unsupported source_file_type: {source_file_type!r}")
    raw_source_language = project.get("source_language")
    source_language = raw_source_language or _DEFAULT_SOURCE_LANGUAGE
    if not isinstance(source_language, str):
        raise ValueError(f"source_language must be a non-empty string or null, got {source_language!r}")
    return ArtifactBasis.build(
        "structured-content/step1",
        kind_version=1,
        inputs={
            "creation_type": creation_type,
            "generation_mode": generation_mode,
            "source_content": source_content,
            "source_file_type": source_file_type,
            "source_language": source_language,
        },
    )


def build_episode_script_basis(step1_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the formal step1 input consumed by one episode's script artifact."""

    creation_type, generation_mode = _creation_axes(project)
    return ArtifactBasis.build(
        "structured-content/episode-script",
        kind_version=1,
        inputs={
            "creation_type": creation_type,
            "generation_mode": generation_mode,
            "step1_content": step1_content,
        },
    )


def _creation_axes(project: Mapping[str, object]) -> tuple[str, str]:
    creation_type = project.get("creation_type")
    if not isinstance(creation_type, str) or creation_type not in _STRUCTURED_CREATION_TYPES:
        raise ValueError(f"structured content basis does not support creation_type: {creation_type!r}")
    generation_mode = project.get("generation_mode")
    if not isinstance(generation_mode, str) or generation_mode not in _GENERATION_MODES:
        raise ValueError(f"unsupported generation_mode: {generation_mode!r}")
    return creation_type, generation_mode
