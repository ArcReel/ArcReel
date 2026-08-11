"""Canonical direct-input bases for structured content artifacts.

These builders intentionally accept the full project mapping but project only formal
content semantics. Execution configuration belongs to a future explicit generation,
not to the currency of an existing structured artifact.
"""

from __future__ import annotations

from collections.abc import Mapping

from lib.artifact_manifest import ArtifactBasis

_STRUCTURED_CONTENT_MODES = frozenset({"narration", "drama"})
_GENERATION_MODES = frozenset({"storyboard", "reference_video"})
_SOURCE_KINDS = frozenset({"novel", "screenplay"})


def build_step1_basis(source_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the formal source inputs consumed by one episode's step1 artifact."""

    content_mode, generation_mode = _content_axes(project)
    raw_source_kind = project.get("source_kind")
    source_kind = "novel" if raw_source_kind is None else raw_source_kind
    if not isinstance(source_kind, str) or source_kind not in _SOURCE_KINDS:
        raise ValueError(f"unsupported source_kind: {source_kind!r}")
    source_language = project.get("source_language")
    if source_language is not None and (not isinstance(source_language, str) or not source_language):
        raise ValueError(f"source_language must be a non-empty string or null, got {source_language!r}")
    return ArtifactBasis.build(
        "structured-content/step1",
        kind_version=1,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "source_content": source_content,
            "source_kind": source_kind,
            "source_language": source_language,
        },
    )


def build_episode_script_basis(step1_content: object, *, project: Mapping[str, object]) -> ArtifactBasis:
    """Describe the formal step1 input consumed by one episode's script artifact."""

    content_mode, generation_mode = _content_axes(project)
    return ArtifactBasis.build(
        "structured-content/episode-script",
        kind_version=1,
        inputs={
            "content_mode": content_mode,
            "generation_mode": generation_mode,
            "step1_content": step1_content,
        },
    )


def _content_axes(project: Mapping[str, object]) -> tuple[str, str]:
    content_mode = project.get("content_mode")
    if not isinstance(content_mode, str) or content_mode not in _STRUCTURED_CONTENT_MODES:
        raise ValueError(f"structured content basis does not support content_mode: {content_mode!r}")
    generation_mode = project.get("generation_mode")
    if not isinstance(generation_mode, str) or generation_mode not in _GENERATION_MODES:
        raise ValueError(f"unsupported generation_mode: {generation_mode!r}")
    return content_mode, generation_mode
