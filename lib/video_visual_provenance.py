"""Read-only provenance for deciding whether a selected video is still current."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from lib.artifact_manifest import ArtifactBasis


def resolve_video_aspect_ratio(project: Mapping[str, object], resource_type: str = "videos") -> str:
    """Resolve the effective project video ratio used by generation requests."""

    value = project.get("aspect_ratio")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and resource_type in value:
        return cast(str, value[resource_type])
    return "9:16" if project.get("content_mode", "narration") in {"narration", "ad"} else "16:9"


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_video_visual_basis(
    kind: str,
    *,
    semantics: Mapping[str, object],
    files: Sequence[tuple[str, Path]],
) -> ArtifactBasis:
    return ArtifactBasis.build(
        f"video-visual/{kind}",
        kind_version=1,
        inputs={
            "semantics": semantics,
            "files": [{"role": role, "sha256": _file_digest(path)} for role, path in files],
        },
    )


def build_storyboard_video_visual_basis(
    *,
    prompt: object,
    storyboard_image: Path,
    end_frame_image: Path | None,
    aspect_ratio: object,
    content_mode: str,
    utterances: object,
    voice_characters: object,
) -> ArtifactBasis:
    """Describe the request facts that determine one storyboard video prompt and frames."""

    files = [("storyboard", storyboard_image)]
    if end_frame_image is not None:
        files.append(("end_frame", end_frame_image))
    return _build_video_visual_basis(
        "storyboard",
        semantics={
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "content_mode": content_mode,
            "utterances": utterances,
            "voice_characters": voice_characters,
        },
        files=files,
    )


def build_reference_video_visual_basis(
    *,
    rendered_prompt: object,
    aspect_ratio: object,
    reference_images: Sequence[Path],
    reference_descriptors: Sequence[Mapping[str, object]] = (),
    reference_audio_files: Sequence[Path] = (),
    reference_audio_speakers: Sequence[str] = (),
    reference_audio_targets: Sequence[int] | None = None,
    request_context: Mapping[str, object] | None = None,
) -> ArtifactBasis:
    """Describe the exact projected reference request and its prompt-affecting inputs."""

    return _build_video_visual_basis(
        "reference",
        semantics={
            "prompt": rendered_prompt,
            "aspect_ratio": aspect_ratio,
            "request_references": list(reference_descriptors),
            "reference_audio_speakers": list(reference_audio_speakers),
            "reference_audio_targets": list(reference_audio_targets) if reference_audio_targets is not None else None,
            "request_context": dict(request_context or {}),
        },
        files=[
            *((f"reference_image_{index}", path) for index, path in enumerate(reference_images)),
            *((f"reference_audio_{index}", path) for index, path in enumerate(reference_audio_files)),
        ],
    )


__all__ = [
    "build_reference_video_visual_basis",
    "build_storyboard_video_visual_basis",
    "resolve_video_aspect_ratio",
]
