"""Immutable execution facts and task-local provider media for submitted video jobs."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, Self, cast

from lib.artifact_manifest import ArtifactBasisDescriptor
from lib.content_digest import canonical_json, canonical_json_digest, sha256_file_with_size
from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.video_artifact_facts import VideoArtifactCurrencyFacts

logger = logging.getLogger(__name__)

ProviderMediaRole = Literal["reference_image", "reference_audio", "start_image", "end_image"]
ReferenceCapability = Literal["i2v", "r2v"]

_SCHEMA_VERSION = 3
_VISUAL_BASIS_SCHEMA_VERSION = 2
_LEGACY_SCHEMA_VERSION = 1
_CHECKPOINT_KIND = "reference_video_submit"
_STORYBOARD_CHECKPOINT_KIND = "storyboard_video_submit"
_STAGING_MANIFEST_VERSION = 1
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_BASIS_DIGEST_RE = re.compile(r"^(?:sha256-v1:)?[0-9a-f]{64}$")


class ReferenceExecutionIdentityError(ValueError):
    """A submitted video task cannot be matched to its immutable execution identity."""

    code = "execution_identity_unrecoverable"

    def __init__(self, detail: str) -> None:
        self.params: dict[str, object] = {"detail": detail}
        super().__init__(detail)


class VideoResumeState(StrEnum):
    """Exhaustive checkpoint/job states for a claimed video task."""

    NO_CHECKPOINT_NO_JOB = "no_checkpoint_no_job"
    CHECKPOINT_WITHOUT_JOB = "checkpoint_without_job"
    READY = "ready"
    IDENTITY_UNRECOVERABLE = "identity_unrecoverable"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("task_id contains unsafe path characters")


def _require_nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_nonempty_string(value, field)


def _require_digest(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _HEX_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")
    return value


def _require_basis_digest(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _BASIS_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a supported basis digest")
    return value


def _require_relative_locator(value: object, field: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    locator = _require_nonempty_string(value, field)
    path = PurePosixPath(locator)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or "\\" in locator
        or path.as_posix() != locator
    ):
        raise ValueError(f"{field} must be a canonical project-relative POSIX path")
    return locator


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], description: str) -> None:
    unexpected = set(value) - expected
    missing = expected - set(value)
    if unexpected:
        raise ValueError(f"unexpected {description} fields: {sorted(unexpected)}")
    if missing:
        raise ValueError(f"missing {description} fields: {sorted(missing)}")


@dataclass(frozen=True, slots=True)
class ProviderMediaInput:
    """One local file that will actually be attached to the provider request."""

    path: Path
    role: ProviderMediaRole
    logical_type: str
    logical_name: str
    kind: str
    target_index: int | None = None

    def __post_init__(self) -> None:
        if self.role not in ("reference_image", "reference_audio", "start_image", "end_image"):
            raise ValueError(f"unsupported provider media role: {self.role!r}")
        _require_nonempty_string(self.logical_type, "logical_type")
        _require_nonempty_string(self.logical_name, "logical_name")
        _require_nonempty_string(self.kind, "kind")
        if self.target_index is not None and (
            not isinstance(self.target_index, int) or isinstance(self.target_index, bool) or self.target_index < 0
        ):
            raise ValueError("target_index must be a non-negative integer or null")
        if self.role != "reference_audio" and self.target_index is not None:
            raise ValueError(f"{self.role} cannot have target_index")


@dataclass(frozen=True, slots=True)
class StagedProviderMedia:
    """Immutable task-local copy plus the logical identity of its source."""

    index: int
    role: ProviderMediaRole
    logical_type: str
    logical_name: str
    kind: str
    source_locator: str
    staged_locator: str
    sha256: str
    size_bytes: int
    target_index: int | None = None

    _FIELDS = frozenset(
        {
            "index",
            "role",
            "logical_type",
            "logical_name",
            "kind",
            "source_locator",
            "staged_locator",
            "sha256",
            "size_bytes",
            "target_index",
        }
    )

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ValueError("media index must be a non-negative integer")
        if self.role not in ("reference_image", "reference_audio", "start_image", "end_image"):
            raise ValueError(f"unsupported provider media role: {self.role!r}")
        _require_nonempty_string(self.logical_type, "logical_type")
        _require_nonempty_string(self.logical_name, "logical_name")
        _require_nonempty_string(self.kind, "kind")
        _require_relative_locator(self.source_locator, "source_locator")
        _require_relative_locator(self.staged_locator, "staged_locator")
        _require_digest(self.sha256, "sha256")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if self.target_index is not None and (
            not isinstance(self.target_index, int) or isinstance(self.target_index, bool) or self.target_index < 0
        ):
            raise ValueError("target_index must be a non-negative integer or null")
        if self.role != "reference_audio" and self.target_index is not None:
            raise ValueError(f"{self.role} cannot have target_index")

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "role": self.role,
            "logical_type": self.logical_type,
            "logical_name": self.logical_name,
            "kind": self.kind,
            "source_locator": self.source_locator,
            "staged_locator": self.staged_locator,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "target_index": self.target_index,
        }

    @classmethod
    def from_dict(cls, value: object) -> StagedProviderMedia:
        if not isinstance(value, dict):
            raise ValueError("provider media entry must be an object")
        raw = cast(dict[str, Any], value)
        _require_exact_keys(raw, cls._FIELDS, "provider media")
        return cls(
            index=raw["index"],
            role=raw["role"],
            logical_type=raw["logical_type"],
            logical_name=raw["logical_name"],
            kind=raw["kind"],
            source_locator=raw["source_locator"],
            staged_locator=raw["staged_locator"],
            sha256=raw["sha256"],
            size_bytes=raw["size_bytes"],
            target_index=raw["target_index"],
        )


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or _is_junction(path)


def _lexical_staging_root(project_path: Path, task_id: str) -> Path:
    _require_task_id(task_id)
    return project_path.resolve() / ".arcreel" / "tasks" / task_id / "provider_media"


def _staging_root(project_path: Path, task_id: str) -> Path:
    final_dir = _lexical_staging_root(project_path, task_id)
    cursor = project_path.resolve()
    for part in (".arcreel", "tasks", task_id, "provider_media"):
        cursor = cursor / part
        if _is_link_or_junction(cursor):
            raise ValueError("provider media staging cannot traverse a symlink or junction")
    return final_dir


def _media_plan(
    project_path: Path,
    task_id: str,
    inputs: tuple[ProviderMediaInput, ...],
) -> tuple[StagedProviderMedia, ...]:
    staging_prefix = PurePosixPath(".arcreel", "tasks", task_id, "provider_media")
    planned: list[StagedProviderMedia] = []
    for index, item in enumerate(inputs):
        source = safe_join(project_path, item.path, require_file=True)
        source_locator = source.relative_to(project_path.resolve()).as_posix()
        suffix = source.suffix.lower() or ".bin"
        filename = f"{index:03d}-{item.role}{suffix}"
        digest, size = sha256_file_with_size(source)
        planned.append(
            StagedProviderMedia(
                index=index,
                role=item.role,
                logical_type=item.logical_type,
                logical_name=item.logical_name,
                kind=item.kind,
                source_locator=source_locator,
                staged_locator=(staging_prefix / filename).as_posix(),
                sha256=digest,
                size_bytes=size,
                target_index=item.target_index,
            )
        )
    return tuple(planned)


def _load_staging_manifest(path: Path) -> tuple[StagedProviderMedia, ...]:
    raw = load_json(path / "manifest.json")
    if not isinstance(raw, dict):
        raise ValueError("immutable provider media staging manifest must be an object")
    _require_exact_keys(cast(dict[str, Any], raw), frozenset({"schema_version", "media"}), "staging manifest")
    if raw["schema_version"] != _STAGING_MANIFEST_VERSION or not isinstance(raw["media"], list):
        raise ValueError("immutable provider media staging manifest is invalid")
    return tuple(StagedProviderMedia.from_dict(item) for item in raw["media"])


def _verify_staged_files(project_path: Path, records: tuple[StagedProviderMedia, ...]) -> None:
    for item in records:
        staged = safe_join(project_path, item.staged_locator, require_file=True)
        digest, size = sha256_file_with_size(staged)
        if digest != item.sha256 or size != item.size_bytes:
            raise ValueError("immutable provider media staging bytes do not match the manifest")


def stage_provider_media(
    project_path: Path,
    task_id: str,
    inputs: tuple[ProviderMediaInput, ...],
) -> tuple[StagedProviderMedia, ...]:
    """Atomically publish task-local copies of exactly the provider-bound media."""

    _require_task_id(task_id)
    if not isinstance(inputs, tuple):
        raise TypeError("provider media inputs must be a tuple")
    planned = _media_plan(project_path, task_id, inputs)
    if not planned:
        return ()

    final_dir = _staging_root(project_path, task_id)
    if os.path.lexists(final_dir):
        if not final_dir.is_dir():
            raise ValueError("immutable provider media staging is not a directory")
        existing = _load_staging_manifest(final_dir)
        if existing != planned:
            raise ValueError("immutable provider media staging conflicts with current execution inputs")
        _verify_staged_files(project_path, existing)
        return existing

    task_dir = final_dir.parent
    task_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".provider_media.", dir=task_dir))
    published = False
    try:
        for item in planned:
            source = safe_join(project_path, item.source_locator, require_file=True)
            destination = temporary_dir / Path(item.staged_locator).name
            shutil.copyfile(source, destination)
            digest, size = sha256_file_with_size(destination)
            if digest != item.sha256 or size != item.size_bytes:
                raise OSError("provider media changed while staging")
        atomic_write_json(
            temporary_dir / "manifest.json",
            {"schema_version": _STAGING_MANIFEST_VERSION, "media": [item.to_dict() for item in planned]},
        )
        try:
            os.rename(temporary_dir, final_dir)
            published = True
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} or not final_dir.is_dir():
                raise
            existing = _load_staging_manifest(final_dir)
            if existing != planned:
                raise ValueError("immutable provider media staging conflicts with a concurrent publisher") from exc
            _verify_staged_files(project_path, existing)
            return existing
        return planned
    finally:
        if not published:
            shutil.rmtree(temporary_dir, ignore_errors=True)
        # Concurrent staging or another task-owned entry can keep the shared task directory non-empty.
        with contextlib.suppress(OSError):
            task_dir.rmdir()


async def stage_provider_media_for_task(
    project_path: Path,
    task_id: str,
    inputs: tuple[ProviderMediaInput, ...],
    *,
    stage: Callable[[Path, str, tuple[ProviderMediaInput, ...]], tuple[StagedProviderMedia, ...]] | None = None,
) -> tuple[StagedProviderMedia, ...]:
    """Finish an in-flight local copy before propagating cancellation, then clean any published bytes."""

    async def _await_uninterruptibly(task: asyncio.Task[Any]) -> Any:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    staging_task = asyncio.create_task(asyncio.to_thread(stage or stage_provider_media, project_path, task_id, inputs))
    try:
        return await asyncio.shield(staging_task)
    except BaseException:
        published = False
        try:
            await _await_uninterruptibly(staging_task)
            published = True
        except BaseException:
            # The original exception owns this path; a failed staging task has not published bytes to clean up.
            pass
        if published:
            cleanup_task = asyncio.create_task(asyncio.to_thread(cleanup_staged_provider_media, project_path, task_id))
            try:
                await _await_uninterruptibly(cleanup_task)
            except Exception:
                logger.warning("provider media cancellation cleanup failed task_id=%s", task_id, exc_info=True)
        raise


def cleanup_staged_provider_media(project_path: Path, task_id: str) -> None:
    """Delete only one task's provider-media staging directory."""

    final_dir = _lexical_staging_root(project_path, task_id)
    # Never follow a reparse point during cleanup. An exact staging link is safe to unlink; a linked ancestor is
    # outside the ownership proof, so leave it untouched rather than deleting through it.
    cursor = project_path.resolve()
    for part in (".arcreel", "tasks", task_id):
        cursor = cursor / part
        if _is_link_or_junction(cursor):
            return
    if final_dir.is_symlink():
        final_dir.unlink(missing_ok=True)
    elif _is_junction(final_dir):
        # Windows directory junctions are directory reparse points: remove the entry with rmdir so neither
        # pathlib.unlink nor recursive deletion can follow or reject the linked directory.
        # Idempotent cleanup can race with another remover of the same junction entry.
        with contextlib.suppress(FileNotFoundError):
            final_dir.rmdir()
    elif os.path.lexists(final_dir):
        if final_dir.is_dir():
            shutil.rmtree(final_dir)
        else:
            final_dir.unlink(missing_ok=True)
    # Parent pruning is best effort because sibling task data or a concurrent creator may keep it in use.
    with contextlib.suppress(OSError):
        final_dir.parent.rmdir()


@dataclass(frozen=True, slots=True)
class NarrationExecutionFacts:
    """Execution-start narration facts; the audio itself is not a provider input."""

    delivery: Literal["post_production", "use_tts"]
    tts_status: str
    artifact_path: str
    basis_digest: str | None
    actual_duration_seconds: float | None

    _FIELDS = frozenset({"delivery", "tts_status", "artifact_path", "basis_digest", "actual_duration_seconds"})

    def __post_init__(self) -> None:
        if self.delivery not in ("post_production", "use_tts"):
            raise ValueError(f"unsupported narration delivery: {self.delivery!r}")
        if self.tts_status not in {
            "not_applicable",
            "not_configured",
            "missing",
            "generating",
            "stale",
            "current",
            "unmeasurable",
            "blocked",
        }:
            raise ValueError(f"unsupported narration TTS status: {self.tts_status!r}")
        _require_relative_locator(self.artifact_path, "artifact_path", allow_empty=True)
        _require_basis_digest(self.basis_digest, "basis_digest", optional=True)
        if self.actual_duration_seconds is not None and (
            not isinstance(self.actual_duration_seconds, (int, float))
            or isinstance(self.actual_duration_seconds, bool)
            or not math.isfinite(self.actual_duration_seconds)
            or self.actual_duration_seconds <= 0
        ):
            raise ValueError("actual_duration_seconds must be positive and finite or null")
        if self.delivery == "post_production":
            if (
                self.tts_status != "not_applicable"
                or self.artifact_path
                or self.basis_digest is not None
                or self.actual_duration_seconds is not None
            ):
                raise ValueError("post-production narration cannot carry TTS execution facts")
        elif (
            self.tts_status != "current"
            or not self.artifact_path
            or self.basis_digest is None
            or self.actual_duration_seconds is None
        ):
            raise ValueError("use_tts narration requires complete current TTS execution facts")

    def to_dict(self) -> dict[str, object]:
        return {
            "delivery": self.delivery,
            "tts_status": self.tts_status,
            "artifact_path": self.artifact_path,
            "basis_digest": self.basis_digest,
            "actual_duration_seconds": self.actual_duration_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> NarrationExecutionFacts:
        if not isinstance(value, dict):
            raise ValueError("narration facts must be an object")
        raw = cast(dict[str, Any], value)
        _require_exact_keys(raw, cls._FIELDS, "narration")
        return cls(
            delivery=raw["delivery"],
            tts_status=raw["tts_status"],
            artifact_path=raw["artifact_path"],
            basis_digest=raw["basis_digest"],
            actual_duration_seconds=raw["actual_duration_seconds"],
        )


@dataclass(frozen=True, slots=True)
class _VideoSubmissionCheckpoint:
    """Strict, versioned identity shared by both video execution routes."""

    CHECKPOINT_KIND: ClassVar[str]
    ARTIFACT_VISUAL_BASIS_KIND: ClassVar[str]

    schema_version: int
    kind: str
    task_id: str
    project_name: str
    script_file: str
    unit_id: str
    capability: ReferenceCapability
    provider_id: str
    provider_model_id: str
    backend_model_id: str
    endpoint_guard: str | None
    api_call_id: int
    prompt: str
    prompt_sha256: str
    duration_seconds: int
    aspect_ratio: str
    resolution: str | None
    generate_audio: bool
    service_tier: str
    seed: int | None
    visual_basis_digest: str
    legacy_artifact_visual_basis: ArtifactBasisDescriptor | None
    artifact_currency: VideoArtifactCurrencyFacts | None
    narration: NarrationExecutionFacts
    media: tuple[StagedProviderMedia, ...]
    reference_audio_targets: tuple[int, ...] | None
    request_digest: str

    _LEGACY_FIELDS = frozenset(
        {
            "schema_version",
            "kind",
            "task_id",
            "project_name",
            "script_file",
            "unit_id",
            "capability",
            "provider_id",
            "provider_model_id",
            "backend_model_id",
            "endpoint_guard",
            "api_call_id",
            "prompt",
            "prompt_sha256",
            "duration_seconds",
            "aspect_ratio",
            "resolution",
            "generate_audio",
            "service_tier",
            "seed",
            "visual_basis_digest",
            "narration",
            "media",
            "reference_audio_targets",
            "request_digest",
        }
    )
    _VISUAL_BASIS_FIELDS = _LEGACY_FIELDS | {"artifact_visual_basis"}
    _FIELDS = _LEGACY_FIELDS | {"artifact_currency"}

    @property
    def artifact_episode(self) -> int | None:
        return self.artifact_currency.episode if self.artifact_currency is not None else None

    @property
    def artifact_visual_basis(self) -> ArtifactBasisDescriptor | None:
        if self.artifact_currency is not None:
            return self.artifact_currency.visual_descriptor
        return self.legacy_artifact_visual_basis

    @property
    def artifact_speech_basis(self) -> ArtifactBasisDescriptor | None:
        return self.artifact_currency.speech_descriptor if self.artifact_currency is not None else None

    @property
    def artifact_duration_basis(self) -> ArtifactBasisDescriptor | None:
        return self.artifact_currency.duration_descriptor if self.artifact_currency is not None else None

    @property
    def artifact_video_basis(self) -> ArtifactBasisDescriptor | None:
        return self.artifact_currency.video_descriptor if self.artifact_currency is not None else None

    @property
    def artifact_voice_style_speakers(self) -> tuple[str, ...]:
        return self.artifact_currency.voice_style_speakers if self.artifact_currency is not None else ()

    @property
    def artifact_duration_tiers(self) -> tuple[int, ...]:
        return self.artifact_currency.duration_tiers if self.artifact_currency is not None else ()

    @property
    def artifact_reference_image_limit(self) -> int | None:
        return self.artifact_currency.reference_image_limit if self.artifact_currency is not None else None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version not in {_LEGACY_SCHEMA_VERSION, _VISUAL_BASIS_SCHEMA_VERSION, _SCHEMA_VERSION}
            or self.kind != self.CHECKPOINT_KIND
        ):
            raise ValueError("unsupported video submission checkpoint version or kind")
        _require_task_id(self.task_id)
        _require_nonempty_string(self.project_name, "project_name")
        _require_relative_locator(self.script_file, "script_file")
        _require_nonempty_string(self.unit_id, "unit_id")
        if self.capability not in ("i2v", "r2v"):
            raise ValueError(f"unsupported video capability: {self.capability!r}")
        _require_nonempty_string(self.provider_id, "provider_id")
        _require_nonempty_string(self.provider_model_id, "provider_model_id")
        _require_nonempty_string(self.backend_model_id, "backend_model_id")
        _require_optional_string(self.endpoint_guard, "endpoint_guard")
        if not isinstance(self.api_call_id, int) or isinstance(self.api_call_id, bool) or self.api_call_id <= 0:
            raise ValueError("api_call_id must be a positive integer")
        _require_nonempty_string(self.prompt, "prompt")
        _require_digest(self.prompt_sha256, "prompt_sha256")
        if self.prompt_sha256 != _sha256_bytes(self.prompt.encode("utf-8")):
            raise ValueError("prompt_sha256 does not match prompt")
        if (
            not isinstance(self.duration_seconds, int)
            or isinstance(self.duration_seconds, bool)
            or self.duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        _require_nonempty_string(self.aspect_ratio, "aspect_ratio")
        _require_optional_string(self.resolution, "resolution")
        if not isinstance(self.generate_audio, bool):
            raise ValueError("generate_audio must be a boolean")
        _require_nonempty_string(self.service_tier, "service_tier")
        if self.seed is not None and (not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise ValueError("seed must be an integer or null")
        _require_basis_digest(self.visual_basis_digest, "visual_basis_digest")
        if self.schema_version == _VISUAL_BASIS_SCHEMA_VERSION:
            if not isinstance(self.legacy_artifact_visual_basis, ArtifactBasisDescriptor):
                raise ValueError("artifact_visual_basis must be a strict artifact basis descriptor")
            if self.legacy_artifact_visual_basis.kind != self.ARTIFACT_VISUAL_BASIS_KIND:
                raise ValueError("artifact_visual_basis kind does not match checkpoint kind")
        elif self.legacy_artifact_visual_basis is not None:
            raise ValueError("checkpoint schema cannot carry a legacy artifact_visual_basis")
        if self.schema_version == _SCHEMA_VERSION:
            if not isinstance(self.artifact_currency, VideoArtifactCurrencyFacts):
                raise ValueError("schema v3 checkpoint requires complete artifact currency facts")
            if self.artifact_currency.request_duration_seconds != self.duration_seconds:
                raise ValueError("artifact currency request duration does not match checkpoint request")
            if self.artifact_currency.visual_basis.kind != self.ARTIFACT_VISUAL_BASIS_KIND:
                raise ValueError("artifact currency visual kind does not match checkpoint kind")
        elif self.artifact_currency is not None:
            raise ValueError("older checkpoint cannot carry complete artifact currency facts")
        if tuple(item.index for item in self.media) != tuple(range(len(self.media))):
            raise ValueError("provider media indexes must be contiguous and ordered")
        expected_prefix = f".arcreel/tasks/{self.task_id}/provider_media/"
        if any(not item.staged_locator.startswith(expected_prefix) for item in self.media):
            raise ValueError("staged_locator does not belong to this task")
        if self.kind == _CHECKPOINT_KIND:
            if any(item.role not in ("reference_image", "reference_audio") for item in self.media):
                raise ValueError("reference checkpoint contains storyboard media")
            audio = tuple(item for item in self.media if item.role == "reference_audio")
            image_count = sum(item.role == "reference_image" for item in self.media)
            if self.reference_audio_targets is None:
                if any(item.target_index is not None for item in audio):
                    raise ValueError("media target_index requires reference_audio_targets")
            else:
                if any(
                    not isinstance(target, int) or isinstance(target, bool) or target < 0
                    for target in self.reference_audio_targets
                ):
                    raise ValueError("reference_audio_targets must contain non-negative integers")
                if tuple(item.target_index for item in audio) != self.reference_audio_targets:
                    raise ValueError("reference_audio_targets do not match staged audio identities")
                if any(target >= image_count for target in self.reference_audio_targets):
                    raise ValueError("reference audio target_index must address a staged reference image")
        else:
            roles = tuple(item.role for item in self.media)
            if roles.count("start_image") != 1 or roles.count("end_image") > 1:
                raise ValueError("storyboard checkpoint requires one start image and at most one end image")
            if any(role not in ("start_image", "end_image") for role in roles):
                raise ValueError("storyboard checkpoint contains reference media")
            if self.reference_audio_targets is not None:
                raise ValueError("storyboard checkpoint cannot have reference_audio_targets")
        _require_digest(self.request_digest, "request_digest")
        if self.request_digest != canonical_json_digest(self._request_digest_payload()):
            raise ValueError("request_digest does not match checkpoint request")

    def _request_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "task_id": self.task_id,
            "project_name": self.project_name,
            "script_file": self.script_file,
            "unit_id": self.unit_id,
            "capability": self.capability,
            "provider_id": self.provider_id,
            "provider_model_id": self.provider_model_id,
            "backend_model_id": self.backend_model_id,
            "endpoint_guard": self.endpoint_guard,
            "api_call_id": self.api_call_id,
            "prompt": self.prompt,
            "prompt_sha256": self.prompt_sha256,
            "duration_seconds": self.duration_seconds,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "generate_audio": self.generate_audio,
            "service_tier": self.service_tier,
            "seed": self.seed,
            "visual_basis_digest": self.visual_basis_digest,
            "narration": self.narration.to_dict(),
            "media": [item.to_dict() for item in self.media],
            "reference_audio_targets": (
                list(self.reference_audio_targets) if self.reference_audio_targets is not None else None
            ),
        }
        if self.schema_version == _VISUAL_BASIS_SCHEMA_VERSION:
            assert self.legacy_artifact_visual_basis is not None
            payload["artifact_visual_basis"] = self.legacy_artifact_visual_basis.to_dict()
        elif self.schema_version == _SCHEMA_VERSION:
            assert self.artifact_currency is not None
            payload["artifact_currency"] = self.artifact_currency.to_dict()
        return payload

    def _request_digest_payload(self) -> dict[str, object]:
        """Return frozen request facts without the local accounting coordinate."""

        payload = self._request_payload()
        del payload["api_call_id"]
        # Schema v2 preserved its historical provider-request digest semantics.
        # Schema v3 binds complete artifact currency evidence into the immutable
        # checkpoint so a version cannot replace typed components independently.
        if self.schema_version == _VISUAL_BASIS_SCHEMA_VERSION:
            payload.pop("artifact_visual_basis", None)
        return payload

    def to_dict(self) -> dict[str, object]:
        return {**self._request_payload(), "request_digest": self.request_digest}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        project_name: str,
        script_file: str,
        unit_id: str,
        capability: ReferenceCapability,
        provider_id: str,
        provider_model_id: str,
        backend_model_id: str,
        endpoint_guard: str | None,
        api_call_id: int,
        prompt: str,
        duration_seconds: int,
        aspect_ratio: str,
        resolution: str | None,
        generate_audio: bool,
        service_tier: str,
        seed: int | None,
        visual_basis_digest: str,
        artifact_currency: VideoArtifactCurrencyFacts,
        narration: NarrationExecutionFacts,
        media: tuple[StagedProviderMedia, ...],
        reference_audio_targets: tuple[int, ...] | None,
    ) -> Self:
        prompt_sha256 = _sha256_bytes(prompt.encode("utf-8"))
        values: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "kind": cls.CHECKPOINT_KIND,
            "task_id": task_id,
            "project_name": project_name,
            "script_file": script_file,
            "unit_id": unit_id,
            "capability": capability,
            "provider_id": provider_id,
            "provider_model_id": provider_model_id,
            "backend_model_id": backend_model_id,
            "endpoint_guard": endpoint_guard,
            "api_call_id": api_call_id,
            "prompt": prompt,
            "prompt_sha256": prompt_sha256,
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "generate_audio": generate_audio,
            "service_tier": service_tier,
            "seed": seed,
            "visual_basis_digest": visual_basis_digest,
            "artifact_currency": artifact_currency.to_dict(),
            "narration": narration.to_dict(),
            "media": [item.to_dict() for item in media],
            "reference_audio_targets": list(reference_audio_targets) if reference_audio_targets is not None else None,
        }
        digest_values = {key: value for key, value in values.items() if key != "api_call_id"}
        request_digest = canonical_json_digest(digest_values)
        return cls(
            schema_version=_SCHEMA_VERSION,
            kind=cls.CHECKPOINT_KIND,
            task_id=task_id,
            project_name=project_name,
            script_file=script_file,
            unit_id=unit_id,
            capability=capability,
            provider_id=provider_id,
            provider_model_id=provider_model_id,
            backend_model_id=backend_model_id,
            endpoint_guard=endpoint_guard,
            api_call_id=api_call_id,
            prompt=prompt,
            prompt_sha256=prompt_sha256,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            generate_audio=generate_audio,
            service_tier=service_tier,
            seed=seed,
            visual_basis_digest=visual_basis_digest,
            legacy_artifact_visual_basis=None,
            artifact_currency=artifact_currency,
            narration=narration,
            media=media,
            reference_audio_targets=reference_audio_targets,
            request_digest=request_digest,
        )

    @classmethod
    def from_json(cls, value: object) -> Self:
        if not isinstance(value, str) or not value:
            raise ValueError("execution checkpoint must be a non-empty JSON string")
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("execution checkpoint is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("execution checkpoint must be a JSON object")
        raw = cast(dict[str, Any], decoded)
        schema_version = raw.get("schema_version")
        if type(schema_version) is not int or schema_version not in {
            _LEGACY_SCHEMA_VERSION,
            _VISUAL_BASIS_SCHEMA_VERSION,
            _SCHEMA_VERSION,
        }:
            raise ValueError("unsupported video submission checkpoint version or kind")
        _require_exact_keys(
            raw,
            (
                cls._LEGACY_FIELDS
                if schema_version == _LEGACY_SCHEMA_VERSION
                else cls._VISUAL_BASIS_FIELDS
                if schema_version == _VISUAL_BASIS_SCHEMA_VERSION
                else cls._FIELDS
            ),
            "checkpoint",
        )
        targets = raw["reference_audio_targets"]
        if targets is not None and not isinstance(targets, list):
            raise ValueError("reference_audio_targets must be an array or null")
        media = raw["media"]
        if not isinstance(media, list):
            raise ValueError("checkpoint media must be an array")
        return cls(
            schema_version=raw["schema_version"],
            kind=raw["kind"],
            task_id=raw["task_id"],
            project_name=raw["project_name"],
            script_file=raw["script_file"],
            unit_id=raw["unit_id"],
            capability=raw["capability"],
            provider_id=raw["provider_id"],
            provider_model_id=raw["provider_model_id"],
            backend_model_id=raw["backend_model_id"],
            endpoint_guard=raw["endpoint_guard"],
            api_call_id=raw["api_call_id"],
            prompt=raw["prompt"],
            prompt_sha256=raw["prompt_sha256"],
            duration_seconds=raw["duration_seconds"],
            aspect_ratio=raw["aspect_ratio"],
            resolution=raw["resolution"],
            generate_audio=raw["generate_audio"],
            service_tier=raw["service_tier"],
            seed=raw["seed"],
            visual_basis_digest=raw["visual_basis_digest"],
            legacy_artifact_visual_basis=(
                ArtifactBasisDescriptor.from_dict(raw["artifact_visual_basis"])
                if schema_version == _VISUAL_BASIS_SCHEMA_VERSION
                else None
            ),
            artifact_currency=(
                VideoArtifactCurrencyFacts.from_dict(raw["artifact_currency"])
                if schema_version == _SCHEMA_VERSION
                else None
            ),
            narration=NarrationExecutionFacts.from_dict(raw["narration"]),
            media=tuple(StagedProviderMedia.from_dict(item) for item in media),
            reference_audio_targets=tuple(targets) if targets is not None else None,
            request_digest=raw["request_digest"],
        )


class ReferenceSubmissionCheckpoint(_VideoSubmissionCheckpoint):
    """Immutable submit identity for a reference-video unit."""

    __slots__ = ()
    CHECKPOINT_KIND = _CHECKPOINT_KIND
    ARTIFACT_VISUAL_BASIS_KIND = "artifact-visual/video-reference"


class StoryboardSubmissionCheckpoint(_VideoSubmissionCheckpoint):
    """Immutable submit identity for a storyboard-video unit."""

    __slots__ = ()
    CHECKPOINT_KIND = _STORYBOARD_CHECKPOINT_KIND
    ARTIFACT_VISUAL_BASIS_KIND = "artifact-visual/video-storyboard"


VideoSubmissionCheckpoint = ReferenceSubmissionCheckpoint | StoryboardSubmissionCheckpoint


def checkpoint_version_metadata(checkpoint: VideoSubmissionCheckpoint) -> dict[str, object]:
    """Source facts shared by normal and resumed paid-version registration."""

    metadata: dict[str, object] = {
        "execution_checkpoint_schema_version": checkpoint.schema_version,
        "execution_task_id": checkpoint.task_id,
        "execution_api_call_id": checkpoint.api_call_id,
        "execution_script_file": checkpoint.script_file,
        "execution_request_digest": checkpoint.request_digest,
        "execution_capability": checkpoint.capability,
        "execution_provider_id": checkpoint.provider_id,
        "execution_provider_model_id": checkpoint.provider_model_id,
        "execution_backend_model_id": checkpoint.backend_model_id,
        "execution_endpoint_guard": checkpoint.endpoint_guard,
        "execution_prompt_sha256": checkpoint.prompt_sha256,
        "execution_duration_seconds": checkpoint.duration_seconds,
        "execution_aspect_ratio": checkpoint.aspect_ratio,
        "execution_resolution": checkpoint.resolution,
        "execution_generate_audio": checkpoint.generate_audio,
        "execution_service_tier": checkpoint.service_tier,
        "execution_seed": checkpoint.seed,
        "execution_visual_basis_digest": checkpoint.visual_basis_digest,
        "execution_narration": checkpoint.narration.to_dict(),
        "execution_provider_media": [item.to_dict() for item in checkpoint.media],
        "execution_reference_audio_targets": (
            list(checkpoint.reference_audio_targets) if checkpoint.reference_audio_targets is not None else None
        ),
    }
    if checkpoint.schema_version == _VISUAL_BASIS_SCHEMA_VERSION:
        assert checkpoint.legacy_artifact_visual_basis is not None
        metadata["artifact_visual_basis"] = checkpoint.legacy_artifact_visual_basis.to_dict()
    elif checkpoint.artifact_currency is not None:
        metadata["artifact_video_currency"] = checkpoint.artifact_currency.to_dict()
    return metadata


def load_task_video_checkpoint(task: dict[str, Any]) -> VideoSubmissionCheckpoint:
    """Strictly parse a video checkpoint and bind it to its owning task-row coordinates."""

    raw = task.get("execution_checkpoint_json")
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else None
    except json.JSONDecodeError as exc:
        raise ValueError("execution checkpoint is not valid JSON") from exc
    kind = decoded.get("kind") if isinstance(decoded, dict) else None
    checkpoint_type: type[ReferenceSubmissionCheckpoint] | type[StoryboardSubmissionCheckpoint]
    if kind == _CHECKPOINT_KIND:
        checkpoint_type = ReferenceSubmissionCheckpoint
        expected_task_type = "reference_video"
    elif kind == _STORYBOARD_CHECKPOINT_KIND:
        checkpoint_type = StoryboardSubmissionCheckpoint
        expected_task_type = "video"
    else:
        raise ValueError("unsupported video submission checkpoint kind")
    checkpoint = checkpoint_type.from_json(raw)
    row_task_type = task.get("task_type")
    if row_task_type is not None and row_task_type != expected_task_type:
        raise ReferenceExecutionIdentityError(
            f"checkpoint kind={checkpoint.kind!r} does not match task type {row_task_type!r}"
        )
    expected = (
        ("task_id", checkpoint.task_id, task.get("task_id")),
        ("project_name", checkpoint.project_name, task.get("project_name")),
        ("script_file", checkpoint.script_file, task.get("script_file")),
        ("unit_id", checkpoint.unit_id, str(task.get("resource_id"))),
    )
    for field, frozen, row_value in expected:
        if frozen != row_value:
            raise ReferenceExecutionIdentityError(
                f"checkpoint {field}={frozen!r} does not match task row {row_value!r}"
            )
    return checkpoint


def load_task_reference_checkpoint(task: dict[str, Any]) -> ReferenceSubmissionCheckpoint:
    checkpoint = load_task_video_checkpoint(task)
    if not isinstance(checkpoint, ReferenceSubmissionCheckpoint):
        raise ReferenceExecutionIdentityError("task does not contain a reference-video checkpoint")
    return checkpoint


def classify_video_resume_state(
    task: dict[str, Any],
) -> tuple[VideoResumeState, VideoSubmissionCheckpoint | None]:
    """Classify all checkpoint/job combinations without consulting mutable project intent."""

    raw_checkpoint = task.get("execution_checkpoint_json")
    has_checkpoint_attempt = raw_checkpoint not in (None, "")
    has_job = bool(task.get("provider_job_id"))
    if not has_job:
        state = (
            VideoResumeState.CHECKPOINT_WITHOUT_JOB if has_checkpoint_attempt else VideoResumeState.NO_CHECKPOINT_NO_JOB
        )
        return state, None
    if not has_checkpoint_attempt:
        return VideoResumeState.IDENTITY_UNRECOVERABLE, None
    try:
        checkpoint = load_task_video_checkpoint(task)
    except (TypeError, ValueError):
        return VideoResumeState.IDENTITY_UNRECOVERABLE, None
    return VideoResumeState.READY, checkpoint


__all__ = [
    "NarrationExecutionFacts",
    "ProviderMediaInput",
    "ReferenceExecutionIdentityError",
    "ReferenceSubmissionCheckpoint",
    "StagedProviderMedia",
    "StoryboardSubmissionCheckpoint",
    "VideoResumeState",
    "VideoSubmissionCheckpoint",
    "checkpoint_version_metadata",
    "classify_video_resume_state",
    "cleanup_staged_provider_media",
    "load_task_reference_checkpoint",
    "load_task_video_checkpoint",
    "stage_provider_media",
    "stage_provider_media_for_task",
]
