"""Execution-sensitive fingerprints for exact video-request reuse.

These digests intentionally include provider request configuration and sound
inputs. They support same-tier reuse and immutable execution checkpoints; they are
not canonical visual-content bases for the Artifact Manifest. That separate
contract lives in :mod:`lib.visual_artifact_provenance`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from lib.artifact_manifest import ArtifactBasis
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    normalize_video_prompt,
    strip_voice_profiles,
)


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
    provider_id: str,
    model_id: str,
    resolution: str | None,
    seed: object,
    requested_generate_audio: bool,
    content_mode: str,
    utterances: object,
    has_utterances: bool,
    voice_characters: object,
) -> ArtifactBasis:
    """Describe the request facts that determine one storyboard video prompt and frames."""

    effective_prompt = prompt
    if isinstance(prompt, dict):
        effective_prompt = strip_voice_profiles(prompt)
        if content_mode == "drama":
            characters = voice_characters if isinstance(voice_characters, dict) else None
            effective_prompt = (
                build_drama_video_prompt(effective_prompt, utterances, characters=characters)
                if has_utterances
                else build_drama_video_prompt_from_legacy_dialogue(effective_prompt, characters=characters)
            )
    provider_prompt = normalize_video_prompt(effective_prompt)
    files = [("storyboard", storyboard_image)]
    if end_frame_image is not None:
        files.append(("end_frame", end_frame_image))
    return _build_video_visual_basis(
        "storyboard",
        semantics={
            "prompt": provider_prompt,
            "aspect_ratio": aspect_ratio,
            "request_context": {
                "provider_id": provider_id,
                "model_id": model_id,
                "resolution": resolution,
                "seed": seed,
                "requested_generate_audio": requested_generate_audio,
            },
            "content_mode": content_mode,
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
