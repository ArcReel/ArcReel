"""
MediaGenerator middleware

Wrap GeminiClient + VersionManager with version management transparent to callers.
Callers only provide project_path and resource_id; version management is automatic.

Covered resource types:
- storyboards: storyboard images (scene_E1S01.png)
- videos: videos (scene_E1S01.mp4)
- characters: character asset images (姜月茴.png)
- scenes: scene asset images (庙宇.png)
- props: prop asset images (玉佩.png)
- grids: grid images (grid_xxx.png)
"""

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from PIL import Image

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import VideoGenerationResult
    from lib.asset_library.generation_inputs import ReferenceAssetChoice, ReferenceImageSource
    from lib.backends.audio_backends.base import AudioBackend
    from lib.backends.image_backends.base import ImageBackend
    from lib.config.resolver import ConfigResolver
    from lib.references.reference_compression import CompressedRef, PayloadLimits, ReferenceSpec

from lib.artifacts.version_manager import PaidVersionCommit, VersionManager
from lib.backends.gemini_shared import RateLimiter
from lib.backends.providers import CallPurpose, CallType, require_provider_pair
from lib.billing.ledger import Ledger
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.db.base import DEFAULT_USER_ID
from lib.generation.deleted_target import trash_result_if_target_deleted
from lib.generation.video_request_facts import DEFAULT_PLANNED_DURATION_SECONDS
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project.resource_paths import resource_relative_path
from lib.speech.audio_utils import probe_reference_audio_total_seconds

logger = logging.getLogger(__name__)


def task_media_staging_path(output_path: Path, task_id: str) -> Path:
    """Return the bounded, deterministic formal-output path owned by one task."""

    if not task_id:
        raise ValueError("formal media output requires a task_id")
    token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return output_path.with_name(f".{token}.task-output{output_path.suffix}")


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def task_video_staging_path(output_path: Path, task_id: str) -> Path:
    """Compatibility name for video task staging."""

    return task_media_staging_path(output_path, task_id)


def task_image_staging_path(output_path: Path, task_id: str) -> Path:
    """Return the bounded staging path for one formal image task."""

    return task_media_staging_path(output_path, task_id)


def _remove_task_staging_path(path: Path) -> None:
    """Remove a task-owned staging entry without traversing links or arbitrary directories."""

    if path.is_symlink():
        path.unlink(missing_ok=True)  # hard-delete: temp -- generation-task staged outputs
    elif _is_junction(path):
        path.rmdir()  # hard-delete: temp -- generation-task staging directories
    elif os.path.lexists(path):
        if not path.is_file():
            raise ValueError("formal media staging path is not a regular file")
        path.unlink()  # hard-delete: temp -- generation-task staged outputs


_remove_task_video_staging_path = _remove_task_staging_path


def cleanup_staged_video_output(
    project_path: Path,
    resource_type: str,
    resource_id: str,
    task_id: str,
) -> None:
    """Remove the deterministic interrupted output owned by one video task."""

    output_path = safe_join(project_path, resource_relative_path(resource_type, resource_id))
    _remove_task_video_staging_path(task_video_staging_path(output_path, task_id))


async def _discard_staged_output(path: Path | None) -> None:
    """Delete staging files in worker threads, returning after deletion settles.

    Cleanup/failure paths only: introduce no new cancellation points; success still returns results, failure still propagates original exceptions.
    """

    if path is not None:
        await run_noninterruptible_sync(
            path.unlink, missing_ok=True
        )  # hard-delete: temp -- generation-task staged outputs


@asynccontextmanager
async def _remove_staged_output_on_error(path: Path | None):
    """Remove a formal-output staging file whenever the guarded operation aborts."""

    try:
        yield
    except BaseException:
        await _discard_staged_output(path)
        raise


def _is_413(exc: BaseException) -> bool:
    """Identify oversized request bodies (HTTP 413).

    First extract status codes from common exception attributes: ``status_code`` (OpenAI/xai SDK + normalized vidu/dashscope) /
    ``response.status_code`` (httpx.HTTPStatusError) / ``code`` (google-genai APIError),
    covering default provider gemini and SDK backends, beyond httpx alone (matching lib/backends/video_backends/ark.py
    status extraction). Missing statuses fall back to phrase matching, without bare "413" substrings that could
    falsely match byte counts / request IDs (for example "41300 bytes").
    """
    status = (
        getattr(exc, "status_code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
        or getattr(exc, "code", None)
    )
    # Defensive int conversion: some SDKs / mocks supply status strings "413",
    # making direct ``== 413`` always False. Nonnumeric statuses fall back to phrase matching below.
    try:
        if status is not None and int(status) == 413:
            return True
    except (ValueError, TypeError):
        pass
    msg = str(exc).lower()
    return "request entity too large" in msg or "payload too large" in msg


# Accounting segment_id classification: per-call_type resource_type allowlists determine whether resource_id can serve as segment_id
# at this single point, shared by all image/video/audio accounting paths.
# Frontend `frontend/src/components/usage/usage-record-view.ts` `SEGMENT_RESOURCE_TYPES`
# holds the same allowlist for storyboard labels on running task rows; update it alongside resource-type additions/removals here,
# or target labels disagree before and after task persistence.
_SEGMENT_ID_ALLOWED_RESOURCE_TYPES: dict[CallType, frozenset[str]] = {
    "image": frozenset({"storyboards", "videos", "grids"}),
    "video": frozenset({"storyboards", "videos", "reference_videos"}),
}


def segment_id_for(call_type: CallType, resource_type: str, resource_id: str) -> str | None:
    """Determine segment_id by accounting call/resource types; audio has no allowlist and passes through unconditionally."""
    if call_type == "audio":
        return resource_id
    allowed = _SEGMENT_ID_ALLOWED_RESOURCE_TYPES.get(call_type)
    if allowed is None:
        raise ValueError(f"unknown ledger channel: {call_type!r}")
    return resource_id if resource_type in allowed else None


def _project_relative_path(project_path: Path, value: object) -> str | None:
    """Normalize call-record file paths (input materials or outputs) to project-relative paths (POSIX separators).

    Readers locate files within projects, and records must survive data-root moves, so paths are project-relative;
    preserve paths outside projects (temporary materials, absolute-path references). Nonpath values (PIL Image, etc.) return None;
    call sites decide whether to record them.
    """
    if not isinstance(value, (str, Path)):
        return None
    path = Path(value)
    try:
        # Make both paths absolute before relativizing: relative project roots or material paths (local debugging, CLI) normalize too.
        return path.absolute().relative_to(project_path.absolute()).as_posix()
    except ValueError:
        return path.as_posix()


def _ledger_inputs(**sections: object) -> dict[str, Any] | None:
    """``inputs`` after removing empty groups; None if all empty, leaving accounting columns empty rather than storing empty objects."""
    kept = {key: value for key, value in sections.items() if value not in (None, [], {})}
    return kept or None


def _aligned_reference_asset_uris(
    backend: object,
    reference_images: list[Path] | None,
    asset_uris: list[str | None] | None,
    *,
    model: str,
) -> list[str | None] | None:
    """Validate asset references aligned with reference images; None if none exist, sending only image contents as usual.

    Asset references require backends declaring asset references for reference-image slots: others ignore this field and silently send image contents instead.
    """
    from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, provider_asset_roles

    if asset_uris is None or not any(asset_uris):
        return None
    if len(asset_uris) != len(reference_images or []):
        raise ValueError("reference_image_asset_uris must align with reference_images")
    if REFERENCE_IMAGE_ROLE not in provider_asset_roles(backend):
        raise ValueError(f"video backend model {model} does not accept provider asset references")
    return list(asset_uris)


def _merge_result_provenance(version_metadata: dict[str, Any], result: "VideoGenerationResult") -> None:
    """Merge facts known only after generation into version metadata before persistence.

    Other metadata is finalized before submission (part of requests), but actual seeds and submitted workflow fingerprints
    exist only after backend completion. ComfyUI ``policy: "random"`` randomizes at submission; without recording it,
    that version can never be reproduced.

    Override ``seed`` only when backends report actual sent values: most channels echo request values, making replacement identical;
    those reporting different values (provider-chosen seeds) record what generation truly used.
    """
    if result.seed is not None:
        version_metadata["seed"] = result.seed
    version_metadata.update(result.provenance or {})


def _collect_result_warnings(warnings: list[dict[str, Any]] | None, result: "VideoGenerationResult") -> None:
    """Merge backend execution notices into caller collectors for persistence in task ``result.warnings``.

    Callers supply collectors rather than receiving them here: both video routes return fixed four-tuples, and adding an element affects every
    call site and many tests. Callers already hold warning lists for results.
    """
    if warnings is None or not result.warnings:
        return
    warnings.extend(dict(warning) for warning in result.warnings)


def _commit_formal_image_output(
    commit_formal_output: Callable[[Path, Path, Mapping[str, Any]], int],
    staged_output_path: Path,
    output_path: Path,
    metadata: Mapping[str, Any],
) -> int:
    """Validate staging outputs and commit formal images in worker threads; delete staging after successful commits."""

    if not staged_output_path.is_file():
        raise RuntimeError("image backend completed without a regular staged output file")
    new_version = commit_formal_output(staged_output_path, output_path, metadata)
    if type(new_version) is not int or new_version < 1:
        raise RuntimeError("formal image commit did not return a positive version")
    staged_output_path.unlink(missing_ok=True)  # hard-delete: temp -- committed staged outputs
    return new_version


class MediaGenerator:
    """
    Media-generation middleware

    Wrap GeminiClient + VersionManager with automatic version management.
    """

    def __init__(
        self,
        project_path: Path,
        rate_limiter: RateLimiter | None = None,
        image_backend: Optional["ImageBackend"] = None,
        video_backend=None,
        audio_backend: Optional["AudioBackend"] = None,
        *,
        config_resolver: Optional["ConfigResolver"] = None,
        user_id: str = DEFAULT_USER_ID,
        image_provider_id: str | None = None,
        video_provider_id: str | None = None,
        audio_provider_id: str | None = None,
    ):
        """
        Initialize MediaGenerator

        Args:
            project_path: Project root directory
            rate_limiter: Optional rate-limiter instance
            image_backend: Optional ImageBackend instance (image generation)
            video_backend: Optional VideoBackend instance (video generation)
            audio_backend: Optional AudioBackend instance (speech synthesis)
            config_resolver: ConfigResolver instance for runtime configuration reads
            user_id: User ID
            image_provider_id: Image registry provider_id. Determines per-provider reference-image compression limits
                and is this lane's sole accounting-provider truth. Must be a registry ID (for example "gemini-aistudio"),
                not backend.name; pair with image_backend, raising if either is missing
            video_provider_id: Video registry provider_id (as above, for I2V/R2V and video accounting)
            audio_provider_id: Audio registry provider_id (narration accounting), paired with audio_backend
        """
        require_provider_pair("image", image_backend, image_provider_id)
        require_provider_pair("video", video_backend, video_provider_id)
        require_provider_pair("audio", audio_backend, audio_provider_id)

        self.project_path = Path(project_path)
        self.project_name = self.project_path.name
        self._rate_limiter = rate_limiter
        self._image_backend = image_backend
        self._video_backend = video_backend
        self._audio_backend = audio_backend
        self._config = config_resolver
        self._user_id = user_id
        self._image_provider_id = image_provider_id
        self._video_provider_id = video_provider_id
        self._audio_provider_id = audio_provider_id
        self.versions = VersionManager(project_path)

        # Initialize accounting ledgers (global async session factory).
        self.ledger = Ledger()

    @staticmethod
    def _prepare_video_output(
        output_path: Path,
        *,
        formal_output: bool,
        task_id: str | None,
    ) -> tuple[Path | None, Path]:
        """Return an optional same-directory staging path and the path the backend must write."""

        if not formal_output:
            return None, output_path
        if task_id is None:
            raise ValueError("formal video output requires a task_id")
        staged_output_path = task_video_staging_path(output_path, task_id)
        # A process can die after the backend starts writing. Reusing one task-addressable path bounds crash residue
        # and removes the prior attempt before normal generation or resume writes the same provider result again.
        _remove_task_video_staging_path(staged_output_path)
        return staged_output_path, staged_output_path

    @staticmethod
    def _prepare_image_output(
        output_path: Path,
        *,
        formal_output: bool,
        task_id: str | None,
    ) -> tuple[Path | None, Path]:
        """Return the task staging path that the image backend may write."""

        if not formal_output:
            return None, output_path
        # Direct calls without queued tasks have no queue identity: assign disposable staging identities, never invent task_id.
        # Accounting task_id must resolve in tasks; leave empty if no real identity exists.
        staging_token = task_id or f"inline-{uuid.uuid4().hex}"
        staged_output_path = task_image_staging_path(output_path, staging_token)
        _remove_task_staging_path(staged_output_path)
        return staged_output_path, staged_output_path

    def _prepare_image_target_sync(
        self,
        *,
        resource_type: str,
        resource_id: str,
        formal_output: bool,
        task_id: str | None,
        prompt: str,
        tracked_metadata: Mapping[str, Any],
    ) -> tuple[Path, Path | None, Path]:
        """Locate image outputs in worker threads: create directories, clean task staging and register unrecorded legacy files."""

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)
        staged_output_path, backend_output_path = self._prepare_image_output(
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if staged_output_path is None and output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt=prompt,
                **tracked_metadata,
            )
        return output_path, staged_output_path, backend_output_path

    def _prepare_video_target_sync(self, resource_type: str, resource_id: str, *, track_existing: bool) -> Path:
        """Locate video outputs and create directories in worker threads; ``track_existing`` registers unrecorded legacy files.

        These prompt / duration / provider options belong to upcoming new requests, never legacy current artifacts with unknown provenance;
        otherwise, after rejected new artifacts roll back, old videos impersonate new request tiers and are incorrectly treated
        as reusable outputs. Unknown facts stay unknown; new artifacts record full request metadata at add_version.
        """

        output_path = self._get_output_path(resource_type, resource_id)
        self._ensure_parent_dir(output_path)
        if track_existing and output_path.exists():
            self.versions.ensure_current_tracked(
                resource_type=resource_type,
                resource_id=resource_id,
                current_file=output_path,
                prompt="",
            )
        return output_path

    def _prepare_audio_staging_sync(self, resource_id: str) -> tuple[Path, Path]:
        """Locate audio outputs in worker threads, reserving unique same-directory staging filenames without placeholder files."""

        output_path = self._get_output_path("audio", resource_id)
        self._ensure_parent_dir(output_path)
        fd, staged_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}.",
            suffix=output_path.suffix,
            dir=output_path.parent,
        )
        os.close(fd)
        staged_path = Path(staged_name)
        staged_path.unlink()  # hard-delete: temp -- mkstemp placeholder files
        return output_path, staged_path

    def _commit_video_output_version_sync(
        self,
        *,
        resource_type: str,
        resource_id: str,
        prompt: str,
        output_path: Path,
        staged_output_path: Path | None,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
    ) -> PaidVersionCommit:
        """Register a normal or resumed video through one identical formal-output commit path."""

        if staged_output_path is None:
            return PaidVersionCommit(
                version=self.versions.add_version(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    source_file=output_path,
                    duration_seconds=duration_seconds,
                    **version_metadata,
                ),
                selected=True,
            )
        try:
            if commit_formal_output is not None:
                return commit_formal_output(
                    staged_output_path,
                    output_path,
                    duration_seconds,
                    version_metadata,
                )
            self.versions.commit_staged_paid_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                staged_file=staged_output_path,
                current_file=output_path,
                select_current=False,
                duration_seconds=duration_seconds,
                **version_metadata,
            )
            raise RuntimeError("formal video output requires an artifact commit callback")
        finally:
            staged_output_path.unlink(missing_ok=True)  # hard-delete: temp -- committed staged outputs

    async def _commit_video_output_version(
        self,
        *,
        resource_type: str,
        resource_id: str,
        prompt: str,
        output_path: Path,
        staged_output_path: Path | None,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
    ) -> PaidVersionCommit:
        """Run the shared normal/resume disk transaction without blocking the event loop.

        The sync transaction cannot be interrupted safely: if a process-level
        shutdown cancels this coroutine, wait for its thread before propagating so
        the committed selection is never left half-written.
        """

        return await run_noninterruptible_sync(
            self._commit_video_output_version_sync,
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_seconds,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

    async def _prepare_formal_video_commit(
        self,
        *,
        resource_type: str,
        resource_id: str,
        prompt: str,
        output_path: Path,
        staged_output_path: Path | None,
        duration_seconds: int,
        version_metadata: Mapping[str, Any],
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Run paid-output validation without losing history when validation aborts."""

        if before_formal_commit is None:
            return
        if staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")
        try:
            await before_formal_commit(staged_output_path, duration_seconds, version_metadata)
        except (Exception, asyncio.CancelledError) as failure:
            try:
                await run_noninterruptible_sync(
                    self.versions.commit_staged_paid_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    staged_file=staged_output_path,
                    current_file=output_path,
                    select_current=False,
                    duration_seconds=duration_seconds,
                    **version_metadata,
                )
            except (Exception, asyncio.CancelledError) as archive_failure:
                failure.add_note(f"paid video history archival also failed: {archive_failure}")
            raise

    async def _trash_if_target_deleted(
        self,
        *,
        resource_type: str,
        resource_id: str,
        staged_file: Path | None,
        task_id: str | None,
        target: tuple[str, str] | None = None,
    ) -> None:
        """After cost settlement but before formal commits, recheck queued-task targets remain in projects; trash outputs and raise if deleted.

        See :mod:`lib.generation.deleted_target`. Direct calls without task identities or staging files do not recheck.
        """
        if task_id is None or staged_file is None:
            return
        await run_noninterruptible_sync(
            trash_result_if_target_deleted,
            self.project_path,
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_file,
            target=target,
        )

    def _get_output_path(self, resource_type: str, resource_id: str) -> Path:
        """
        Infer output paths from resource types and IDs

        Args:
            resource_type: Resource type (storyboards, videos, characters, clues)
            resource_id: Resource ID (E1S01, 姜月茴, 玉佩)

        Returns:
            Absolute output-file path
        """
        relative_path = resource_relative_path(resource_type, resource_id)
        try:
            return safe_join(self.project_path, relative_path)
        except PathTraversalError as exc:
            raise ValueError(f"非法资源 ID: '{resource_id}'") from exc

    def _ensure_parent_dir(self, output_path: Path) -> None:
        """Ensure output directories exist"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _reference_limits(self, provider_id: str | None) -> "PayloadLimits":
        """Resolve PayloadLimits for reference upload copies.

        Without config_resolver (zero configuration), use conservative shared defaults without DB access. Otherwise delegate uniformly to
        ConfigResolver.reference_payload_limits: None provider_id short-circuits internally to service-layer
        defaults (also without DB access), avoiding a second default source here that drifts from configuration.
        """
        from lib.references.reference_compression import PayloadLimits

        if self._config is None:
            return PayloadLimits()
        total, single = await self._config.reference_payload_limits(provider_id)
        return PayloadLimits(total_max_bytes=total, single_max_bytes=single)

    async def _run_with_reference_compression(
        self,
        *,
        specs: "list[ReferenceSpec]",
        provider_id: str | None,
        build_and_call: "Callable[[list[CompressedRef]], Awaitable[Any]]",
        before_submit: Callable[[], Awaitable[None]] | None = None,
        provider_resubmit_is_unsafe: Callable[[], bool] | None = None,
    ) -> Any:
        """Actively compress reference upload copies, preflight downshift and handle passive 413 fallback, then call backends.

        build_and_call accepts CompressedRef lists merged in original order (1:1 counts, including passthrough entries), builds provider
        requests and returns backend.generate coroutines. Without references, call once directly without downshifting (T2I/T2V 413s are
        unrelated to reference images and must not become floor errors).
        """
        from lib.references.reference_compression import (
            LADDER_STEPS,
            ReferencePayloadFloorError,
            compressed_reference_payload,
        )

        submit_boundary_crossed = False

        async def _call_once(compressed: "list[CompressedRef]") -> Any:
            nonlocal submit_boundary_crossed
            if not submit_boundary_crossed:
                # Mark before awaiting so cancellation/failure cannot cause a later retry to rewrite an immutable
                # checkpoint. The caller must abort the whole request when this hook fails.
                submit_boundary_crossed = True
                if before_submit is not None:
                    await before_submit()
            return await build_and_call(compressed)

        if not specs:
            return await _call_once([])

        limits = await self._reference_limits(provider_id)
        step = 0
        while True:
            # Compression performs CPU-intensive PIL decoding/encoding and disk writes; temporary-directory cleanup traverses/deletes files. Thread both ends
            # to avoid blocking worker event loops (heartbeats / SSE / other concurrent lanes). Drive context managers manually:
            # register __enter__ completion inside threads, since cancellation during compression loses return values; finally cleans registered resources.
            # Cleanup is uninterruptible: cancellation during cleanup after provider returns must not lose paid results.
            # Preflight floors raise within __enter__ before temporary directories exist (select_ladder_step precedes disk writes);
            # do not register; propagate directly.
            cm = compressed_reference_payload(specs, limits=limits, start_step=step)
            entered: list[tuple[int, list[CompressedRef]]] = []

            def _enter(cm=cm, entered=entered) -> None:
                entered.append(cm.__enter__())

            try:
                await run_sync_transaction(_enter)
                landed, compressed = entered[0]
                try:
                    return await _call_once(compressed)
                except Exception as e:
                    if not _is_413(e) or (provider_resubmit_is_unsafe is not None and provider_resubmit_is_unsafe()):
                        raise
                    # Resume downshifting from actual landed tiers, not requested step: active byte preflight may already
                    # downshift to landed>step; only landed+1 is strictly smaller and guarantees monotonic downshifts.
                    if landed < LADDER_STEPS:
                        step = landed + 1
                        continue
                    # Still 413 at floor → exhausted → user-visible hard error (retain 413 cause).
                    raise ReferencePayloadFloorError() from e
            finally:
                if entered:
                    await run_noninterruptible_sync(cm.__exit__, None, None, None)

    async def generate_image_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        reference_images=None,
        aspect_ratio: str = "9:16",
        image_size: str | None = None,
        formal_output: bool = False,
        task_id: str | None = None,
        commit_formal_output: Callable[[Path, Path, Mapping[str, Any]], int] | None = None,
        before_submit: Callable[[], Awaitable[None]] | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        Generate images asynchronously with automatic version management

        Args:
            prompt: Image-generation prompt
            resource_type: Resource type (storyboards, characters, clues)
            resource_id: Resource ID (E1S01, 姜月茴, 玉佩)
            reference_images: Reference-image list
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            image_size: Image size, omitted by default (backend/SDK decides)
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number) tuple
        """
        from lib.backends.image_backends.base import ImageGenerationRequest, ReferenceImage

        if formal_output and commit_formal_output is None:
            raise ValueError("formal image output requires an artifact commit callback")
        # 1. Locate outputs, clean this task's residual staging; for existing nonformal outputs, ensure old files are tracked.
        output_path, staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_image_target_sync,
            resource_type=resource_type,
            resource_id=resource_id,
            formal_output=formal_output,
            task_id=task_id,
            prompt=prompt,
            tracked_metadata={"aspect_ratio": aspect_ratio, **version_metadata},
        )

        if self._image_backend is None:
            raise RuntimeError("image_backend not configured")

        # Normalize reference_images first, discarding unsupported types such as PIL;
        # capability classification must use normalized results, avoiding invalid supplied references being
        # misclassified as I2I before actual T2I calls, falsely raising image_capability_missing_i2i.
        ref_images: list[ReferenceImage] = []
        if reference_images:
            for ref in reference_images:
                if isinstance(ref, dict):
                    ref_images.append(ReferenceImage(path=str(ref.get("image", ""))))
                elif hasattr(ref, "__fspath__") or isinstance(ref, (str, Path)):
                    ref_images.append(ReferenceImage(path=str(ref)))
                # Ignore unsupported types such as PIL Image.

        # Capability gating allows only channel-implemented and model-accepted capabilities, matching task-bucket candidates. Reference-image limits
        # share effective values with orchestration trimming (lib.backends.image_capabilities). Upstream resolver bucket selection uses the same rules;
        # guard actual request shapes here.
        from lib.backends.image_capabilities import gate_image_request

        # Pairing invariants ensure non-None backends have non-None provider_id.
        await gate_image_request(
            self._image_backend, provider_id=cast(str, self._image_provider_id), reference_count=len(ref_images)
        )

        # 2. Accounting bracket: entry persists pending; success submits backend results via call.success(result).
        #    Exception automatically transitions failed then reraises; CancelledError settles cancelled then reraises.
        async with _remove_staged_output_on_error(staged_output_path):
            async with self.ledger.record(
                project_name=self.project_name,
                call_type="image",
                model=self._image_backend.model,
                prompt=prompt,
                resolution=image_size,
                aspect_ratio=aspect_ratio,
                # Accounting uses resolved provider_id; pairing invariants ensure non-None backends have non-None provider_id.
                provider=cast(str, self._image_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("image", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    reference_images=[
                        {"path": rel, "label": None, "role": "array"}
                        for ref in ref_images
                        if (rel := _project_relative_path(self.project_path, ref.path)) is not None
                    ]
                ),
            ) as call:
                from lib.references.reference_compression import ReferenceSpec, RefRole

                image_backend = self._image_backend
                # All image references use array roles (full baseline + downshift ladder + byte budgets).
                specs = [ReferenceSpec(source=Path(r.path), role=RefRole.ARRAY) for r in ref_images]

                def _call_image(compressed: "list[CompressedRef]"):
                    return image_backend.generate(
                        ImageGenerationRequest(
                            prompt=prompt,
                            output_path=backend_output_path,
                            reference_images=[ReferenceImage(path=str(c.path)) for c in compressed],
                            aspect_ratio=aspect_ratio,
                            image_size=image_size,
                            project_name=self.project_name,
                        )
                    )

                result = await self._run_with_reference_compression(
                    specs=specs,
                    provider_id=self._image_provider_id,
                    build_and_call=_call_image,
                    before_submit=before_submit,
                )
                call.success(result)

            await self._trash_if_target_deleted(
                resource_type=resource_type,
                resource_id=resource_id,
                staged_file=staged_output_path,
                task_id=task_id,
            )
            metadata: dict[str, Any] = {"aspect_ratio": aspect_ratio, **version_metadata}
            if staged_output_path is None:
                new_version = await run_noninterruptible_sync(
                    self.versions.add_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=prompt,
                    source_file=output_path,
                    **metadata,
                )
            else:
                assert commit_formal_output is not None
                new_version = await run_noninterruptible_sync(
                    _commit_formal_image_output,
                    commit_formal_output,
                    staged_output_path,
                    output_path,
                    metadata,
                )

        return output_path, new_version

    async def generate_audio_async(
        self,
        text: str,
        resource_id: str,
        voice: str,
        language_type: str = "Chinese",
        speed: float | None = None,
        task_id: str | None = None,
        before_submit: Callable[[], Awaitable[None]] | None = None,
        before_commit: Callable[[Path], Awaitable[None]] | None = None,
        commit_staged: Callable[[Path, Path], int | PaidVersionCommit] | None = None,
        target_resource: tuple[str, str] | None = None,
        **version_metadata,
    ) -> tuple[Path, int]:
        """
        Synthesize speech asynchronously with automatic version management

        Unlike images/videos, TTS backends are synchronous (no submit-poll-resume), with the simplest logic.

        Args:
            text: Text to synthesize (original narration)
            resource_id: Resource ID (segment, for example E1S01)
            voice: Voice (for example Cherry)
            language_type: Language, default Chinese
            speed: Reserved speech rate (synchronous models ignore it)
            task_id: Generation task owning this synthesis; accounting links back through it, omitted for direct calls without queued tasks
            before_submit: Async admission hook run once immediately before initial provider submission
            target_resource: Owning resource (resource type, resource ID), defaulting to the output itself; deleted owners send outputs to trash
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number) tuple
        """
        from lib.backends.audio_backends.base import AudioSynthesisRequest

        resource_type = "audio"
        if self._audio_backend is None:
            raise RuntimeError("audio_backend not configured")

        # Backends write only unique same-directory staging files. Promote atomically through replace to canonical only after synchronous
        # synthesis fully succeeds and produces regular files; failure/cancellation never touches old paid audio.
        output_path, staged_path = await run_sync_transaction(self._prepare_audio_staging_sync, resource_id)

        # Audio synthesized-character → billable-token semantic conversion lives in ledger union dispatch (_settlement_from_result);
        # simply submit backend results to call.success here.
        try:
            async with self.ledger.record(
                project_name=self.project_name,
                call_type="audio",
                model=self._audio_backend.model,
                prompt=text,
                # Accounting uses resolved provider_id; pairing invariants ensure non-None backends have non-None provider_id.
                provider=cast(str, self._audio_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("audio", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    voice=voice,
                    parameters=_ledger_inputs(language_type=language_type, speed=speed),
                ),
            ) as call:
                request = AudioSynthesisRequest(
                    text=text,
                    output_path=staged_path,
                    voice=voice,
                    language_type=language_type,
                    speed=speed,
                )
                if before_submit is not None:
                    await before_submit()
                result = await self._audio_backend.synthesize(request)
                # Providers returned: output checks introduce no new cancellation points that could prevent recording paid results as success.
                if not await run_noninterruptible_sync(staged_path.is_file):
                    raise RuntimeError("audio backend completed without a regular output file")
                call.success(result)

            await self._trash_if_target_deleted(
                resource_type=resource_type,
                resource_id=resource_id,
                staged_file=staged_path,
                task_id=task_id,
                target=target_resource,
            )
            if before_commit is not None:
                await before_commit(staged_path)
            commit = commit_staged
            selected_current = True
            if commit is None:
                version = await run_noninterruptible_sync(
                    self.versions.commit_staged_version,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    prompt=text,
                    staged_file=staged_path,
                    current_file=output_path,
                    **version_metadata,
                )
            else:
                committed = await run_noninterruptible_sync(commit, staged_path, output_path)
                if isinstance(committed, PaidVersionCommit):
                    version = committed.version
                    selected_current = committed.selected
                else:
                    version = committed
            if selected_current and not await run_noninterruptible_sync(output_path.is_file):
                raise RuntimeError("audio commit completed without a regular formal output file")
            return output_path, version
        finally:
            await _discard_staged_output(staged_path)

    async def choose_reference_image_assets(
        self, sources: "Sequence[ReferenceImageSource]"
    ) -> "tuple[ReferenceAssetChoice | None, ...]":
        """Choose provider asset associations for outgoing reference images using actual video-backend inference credentials.

        Same length/order as ``sources``; usable associations supply asset references, None without applicable associations. Unavailable or failed-ingestion associations raise
        :class:`~lib.asset_library.generation_inputs.ProviderAssetLinkUnavailableError`, never switching to image contents.
        """
        from lib.asset_library.generation_inputs import choose_reference_image_assets
        from lib.db import async_session_factory

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")
        return await choose_reference_image_assets(
            async_session_factory,
            backend=self._video_backend,
            project_name=self.project_name,
            project_path=self.project_path,
            sources=sources,
        )

    async def generate_video_async(
        self,
        prompt: str,
        resource_type: str,
        resource_id: str,
        start_image: str | Path | Image.Image | None = None,
        end_image: Path | None = None,
        reference_images: list[Path] | None = None,
        reference_image_asset_uris: list[str | None] | None = None,
        reference_audio_files: list[Path] | None = None,
        reference_audio_targets: list[int] | None = None,
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
        task_id: str | None = None,
        before_submit: Callable[[], Awaitable[Mapping[str, object] | None]] | None = None,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """
        Generate videos asynchronously with automatic version management

        Args:
            prompt: Video-generation prompt (including unified text negatives assembled upstream by prompt_builders)
            resource_type: Resource type (videos)
            resource_id: Resource ID (E1S01)
            start_image: Starting-frame image (image-to-video mode)
            end_image: Ending-frame image (first_last mode)
            reference_images: Reference-image list (multi-reference mode)
            reference_image_asset_uris: Same length/order as reference_images; non-None item i sends image i as that provider
                asset reference, without content upload, compression or byte preflight. Requires backends declaring asset references for reference-image slots
            reference_audio_files: Reference-audio list (voice cloning), ordered as prompt references 音频N
            reference_audio_targets: Same length/order as reference_audio_files; item i is that audio segment's corresponding
                reference_images index (0-based). Read only when backends declare reference_audio_per_image;
                see VideoGenerationRequest.reference_audio_targets
            aspect_ratio: Aspect ratio, default 9:16 (portrait)
            duration_seconds: Video duration, choices "4", "6", "8"
            resolution: Resolution, omitted by default (backend/SDK decides)
            before_submit: Async persistence hook run once immediately before initial provider submission;
                merge returned values into this version's metadata
            formal_output: Write provider outputs to same-directory temporary files first, then commit with version history on success
            warnings: Caller-owned warning collector; append backend execution notices in place,
                then callers persist them into task ``result.warnings``
            **version_metadata: Additional metadata

        Returns:
            (output_path, version_number, video_ref, video_uri) four-tuple
        """
        # Normalize duration to int first: upstream may pass float strings "8.0"; direct int("8.0") raises ValueError,
        # triggering fallback that silently loses actual values ("10.0" becomes 8). float() then int() preserves semantics.
        # Do this before every ensure_current_tracked / add_version / VideoGenerationRequest,
        # keeping duration_seconds types identical in version metadata and provider requests (both int),
        # avoiding versions.json strings versus ApiCall ints.
        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else DEFAULT_PLANNED_DURATION_SECONDS
        except (ValueError, TypeError):
            duration_int = DEFAULT_PLANNED_DURATION_SECONDS

        # 1. Track existing old files (unknown-provenance legacy files only, see _prepare_video_target_sync).
        output_path = await run_sync_transaction(
            self._prepare_video_target_sync,
            resource_type,
            resource_id,
            track_existing=not formal_output,
        )

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        # Normalize empty end_image to None: legacy/direct Python callers may use "" for absent ending frames
        # (kling _build_payload truthiness fixes this compatibility meaning; see
        # tests/integration/lib/backends/video_backends/test_kling_video_backend.py::test_image2video_empty_end_frame_is_omitted）。
        # gating below uses is not None; unnormalized "" would be mistaken for real ending frames.
        if not end_image:
            end_image = None

        model_name = self._video_backend.model

        # Capability validation and slot assembly precede accounting brackets: unsupported capabilities must fail without charges.
        # Raising inside also settles nothing but leaves failed ApiCall rows; both lack side effects, and preceding is cleanest.
        from lib.backends.backend_assembly.specs import effective_builtin_video_capabilities
        from lib.backends.video_frame_slots import (
            gate_video_request,
            plan_frame_slots,
            resolve_first_frame_aspect_ratio,
            resolve_video_capabilities,
        )
        from lib.custom_provider import is_custom_provider

        # Prompt-length checks apply to every request (unlike optional ending frames/references/reference audio), so capability queries do not
        # trigger lazily by optional paths. Queries synchronously read backend declarations without I/O.
        video_caps = resolve_video_capabilities(
            self._video_backend,
            service_tier=version_metadata.get("service_tier", "default"),
            resolution=resolution,
        )
        # Built-in providers evaluate tier-aware channel declarations and model entries, adding capability overrides for user-added models, matching planning
        # (docs/adr/0104). CustomVideoBackend already resolves custom-provider effective capabilities by the same rules.
        if self._video_provider_id is not None and not is_custom_provider(self._video_provider_id):
            video_caps = effective_builtin_video_capabilities(video_caps, self._video_provider_id, model_name)
        # Total-duration validation needs audio metadata reads, so probe here where file paths are available, then pass results to pure
        # gate_video_request. Failed probing (unavailable bundled ffmpeg, etc.) passes None and uses existing degradation to skip
        # that validation without blocking requests. Probe only when caps declare total-duration constraints; undeclaring backends
        # (for example wan2.7) need no extra probe subprocess for every request.
        reference_audio_total_seconds = (
            await probe_reference_audio_total_seconds(reference_audio_files)
            if reference_audio_files and video_caps.max_reference_audio_total_seconds is not None
            else None
        )
        asset_uris = _aligned_reference_asset_uris(
            self._video_backend, reference_images, reference_image_asset_uris, model=model_name
        )
        slot_plan = plan_frame_slots(
            start_image=start_image,
            end_image=end_image,
            reference_images=reference_images,
        )
        if asset_uris is not None and slot_plan.reference_start_index is not None:
            for offset, uri in enumerate(asset_uris):
                if uri is not None:
                    slot_plan.specs[slot_plan.reference_start_index + offset].provider_asset = True
        gate_video_request(
            caps=video_caps,
            provider=self._video_backend.name,
            model=model_name,
            prompt=prompt,
            has_image=bool(slot_plan.specs),
            has_first_frame=slot_plan.start_index is not None,
            end_image=end_image,
            reference_images=reference_images,
            reference_audio_files=reference_audio_files,
            reference_audio_total_seconds=reference_audio_total_seconds,
        )
        # Only first_frame_ratio_adaptive_only backends are affected; sent values stay separate from callers' original
        # aspect_ratio (retained for accounting/storyboard-image generation), without overwriting upstream variables.
        request_aspect_ratio = resolve_first_frame_aspect_ratio(
            caps=video_caps,
            aspect_ratio=aspect_ratio,
            has_first_frame=slot_plan.start_index is not None,
        )

        if self._config is not None:
            configured_generate_audio = await self._config.video_generate_audio(self.project_name)
        else:
            from lib.config.resolver import ConfigResolver

            configured_generate_audio = ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

        staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_video_output,
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

        # Conversion of actual video billable duration (result.duration_seconds) overriding requested durations lives in ledger union
        # dispatch (_settlement_from_result); submit backend result objects only here.
        video_ref = None
        video_uri: str | None = None
        async with (
            _remove_staged_output_on_error(staged_output_path),
            self.ledger.record(
                project_name=self.project_name,
                call_type="video",
                model=model_name,
                prompt=prompt,
                resolution=resolution,
                duration_seconds=duration_int,
                aspect_ratio=aspect_ratio,
                generate_audio=effective_generate_audio,
                # Accounting uses resolved provider_id; pairing invariants ensure non-None backends have non-None provider_id.
                provider=cast(str, self._video_provider_id),
                user_id=self._user_id,
                segment_id=segment_id_for("video", resource_type, resource_id),
                output_path=_project_relative_path(self.project_path, output_path),
                task_id=task_id,
                purpose=CallPurpose.GENERATION_TASK,
                inputs=_ledger_inputs(
                    reference_images=[
                        {"path": rel, "label": None, "role": "array"}
                        | ({"provider_asset": uri} if uri is not None else {})
                        for ref, uri in zip(
                            reference_images or [], asset_uris or [None] * len(reference_images or []), strict=True
                        )
                        if (rel := _project_relative_path(self.project_path, ref)) is not None
                    ],
                    start_image=_project_relative_path(self.project_path, start_image),
                    end_image=_project_relative_path(self.project_path, end_image),
                    reference_audio=[
                        rel
                        for audio in (reference_audio_files or [])
                        if (rel := _project_relative_path(self.project_path, audio)) is not None
                    ],
                    parameters=_ledger_inputs(service_tier=version_metadata.get("service_tier")),
                ),
            ) as call,
        ):
            from arcreel_market_core.video_backend_contract import VideoGenerationRequest
            from lib.backends.video_backends.registry import backend_metered_usage

            video_backend = self._video_backend
            # FRAME (start/end frames, never resize) + ARRAY (references, full ladder) form ordered
            # specs (see lib/backends/video_frame_slots.py); restore three request fields by index after compression.
            specs = slot_plan.specs
            start_spec_idx = slot_plan.start_index
            end_spec_idx = slot_plan.end_index
            ref_start_idx = slot_plan.reference_start_index
            provider_resubmit_unsafe = False
            # Last actually sent request: compression/downshift retries rebuild requests; meter the final sent one.
            sent_request: list[VideoGenerationRequest] = []

            def _mark_provider_resubmit_unsafe() -> None:
                nonlocal provider_resubmit_unsafe
                provider_resubmit_unsafe = True

            def _call_video(compressed: "list[CompressedRef]"):
                start_arg = compressed[start_spec_idx].path if start_spec_idx is not None else None
                end_arg = compressed[end_spec_idx].path if end_spec_idx is not None else None
                # Array references always end specs (after appended start/end), so [ref_start_idx:] selects them exactly;
                # without compressible array entries, fall back to original reference_images (preserving None / [] semantics).
                ref_arg = (
                    [c.path for c in compressed[ref_start_idx:]] if ref_start_idx is not None else reference_images
                )
                request = VideoGenerationRequest(
                    prompt=prompt,
                    output_path=backend_output_path,
                    aspect_ratio=request_aspect_ratio,
                    duration_seconds=duration_int,
                    resolution=resolution,
                    start_image=start_arg,
                    end_image=end_arg,
                    reference_images=ref_arg,
                    # Compression preserves order/count; asset-reference entries pass through original paths, so references aligned to reference_images also align to ref_arg.
                    reference_image_asset_uris=asset_uris,
                    # Audio bypasses compressors (specs contain only images), so pass original lists directly: their order matches prompt
                    # 音频N references; reordering assigns character A's voice to character B.
                    reference_audio_files=reference_audio_files,
                    # Array-reference compression preserves order/count, so targets calculated by callers from uncompressed
                    # reference_images indices remain valid for compressed ref_arg.
                    reference_audio_targets=reference_audio_targets,
                    generate_audio=effective_generate_audio,
                    poll_timeout_seconds=poll_timeout_seconds,
                    project_name=self.project_name,
                    task_id=task_id,
                    on_provider_resubmit_unsafe=_mark_provider_resubmit_unsafe,
                    on_provider_response=lambda _stage, body: self.ledger.record_provider_response(
                        call_id=call.call_id, body=body
                    ),
                    service_tier=version_metadata.get("service_tier", "default"),
                    seed=version_metadata.get("seed"),
                    video_capabilities=video_caps,
                )
                sent_request[:] = [request]
                return video_backend.generate(request)

            async def _before_first_submit() -> None:
                if before_submit is not None:
                    checkpoint_metadata = await before_submit()
                    if checkpoint_metadata is not None:
                        version_metadata.update(checkpoint_metadata)

            try:
                result = await self._run_with_reference_compression(
                    specs=specs,
                    provider_id=self._video_provider_id,
                    build_and_call=_call_video,
                    before_submit=_before_first_submit if before_submit is not None else None,
                    provider_resubmit_is_unsafe=lambda: provider_resubmit_unsafe,
                )
            except BaseException:
                await _discard_staged_output(staged_output_path)
                raise
            video_uri = result.video_uri
            _merge_result_provenance(version_metadata, result)
            _collect_result_warnings(warnings, result)
            call.success(result, usage=backend_metered_usage(video_backend, sent_request[0], result))

        await self._trash_if_target_deleted(
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_output_path,
            task_id=task_id,
        )
        await self._prepare_formal_video_commit(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            before_formal_commit=before_formal_commit,
        )

        # 5. Record new versions.
        committed = await self._commit_video_output_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

        return output_path, committed.version, video_ref, video_uri

    async def resume_video_async(
        self,
        *,
        job_id: str,
        resource_type: str,
        resource_id: str,
        prompt: str = "",
        aspect_ratio: str = "9:16",
        duration_seconds: str | int = "8",
        resolution: str | None = None,
        task_id: str | None = None,
        submitted_base_url: str | None = None,
        poll_timeout_seconds: int = DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS,
        formal_output: bool = False,
        before_formal_commit: Callable[[Path, int, Mapping[str, Any]], Awaitable[None]] | None = None,
        commit_formal_output: Callable[[Path, Path, int, Mapping[str, Any]], PaidVersionCommit] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        **version_metadata,
    ) -> tuple[Path, int, Any, str | None]:
        """Resume already submitted provider video jobs through backend.resume_video, never generate.

        Differences from generate_video_async:
        - No new accounting bracket (no new pending row): initial submissions already recorded. ResumeExpired / crash windows
          must not write ApiCall again (prevent double billing). Look up unsettled calls through ``api_calls.task_id``;
          ledger.resume_success / resume_failed precisely transition pending → success/failed.
          Missing calls (no task identity or already settled) log logger.warning without blocking.
        - Successful resume always records new versions; ``formal_output=True`` first downloads into
          same-directory temporary files, then commits with version history without overwriting current early.
        - prompt / start_image / reference_images only affect logging/version metadata, never provider results.
        - ``submitted_base_url`` is named rather than version_metadata: replay the submission-time domain
          only for backend polling; persisting it in version metadata pollutes versions.json.

        Returns: (output_path, version_number, video_ref, video_uri) four-tuple.
        """
        output_path = await run_sync_transaction(
            self._prepare_video_target_sync,
            resource_type,
            resource_id,
            track_existing=False,
        )

        # Normalize duration to int first: upstream may pass float strings "8.0"; direct int("8.0") raises ValueError,
        # triggering fallback that silently loses actual values ("10.0" becomes 8). float() then int() preserves semantics.
        # Do this before VideoGenerationRequest / add_version so version metadata and
        # provider-request duration_seconds types agree (both int, avoiding versions.json strings).
        try:
            duration_int = int(float(duration_seconds)) if duration_seconds else DEFAULT_PLANNED_DURATION_SECONDS
        except (ValueError, TypeError):
            duration_int = DEFAULT_PLANNED_DURATION_SECONDS

        if self._video_backend is None:
            raise RuntimeError("video_backend not configured")

        # Look up unsettled call rows by task identity; ``api_calls.task_id`` is the sole truth for task/call associations.
        api_call_id = await self.ledger.pending_call_id_for_task(task_id) if task_id is not None else None

        if self._config is not None:
            configured_generate_audio = await self._config.video_generate_audio(self.project_name)
        else:
            from lib.config.resolver import ConfigResolver

            configured_generate_audio = ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
        effective_generate_audio = version_metadata.get("generate_audio", configured_generate_audio)

        staged_output_path, backend_output_path = await run_sync_transaction(
            self._prepare_video_output,
            output_path,
            formal_output=formal_output,
            task_id=task_id,
        )
        if before_formal_commit is not None and staged_output_path is None:
            raise ValueError("before_formal_commit requires formal video output")

        from arcreel_market_core.video_backend_contract import ResumeExpiredError, VideoGenerationRequest
        from lib.backends.video_backends.registry import backend_metered_usage

        request = VideoGenerationRequest(
            prompt=prompt,
            output_path=backend_output_path,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_int,
            resolution=resolution,
            generate_audio=effective_generate_audio,
            poll_timeout_seconds=poll_timeout_seconds,
            project_name=self.project_name,
            task_id=task_id,
            on_provider_response=(
                (lambda _stage, body: self.ledger.record_provider_response(call_id=api_call_id, body=body))
                if api_call_id is not None
                else None
            ),
            service_tier=version_metadata.get("service_tier", "default"),
            seed=version_metadata.get("seed"),
            submitted_base_url=submitted_base_url,
        )

        try:
            result = await self._video_backend.resume_video(job_id, request)
        except ResumeExpiredError as exc:
            # Transition pending ApiCall to failed rather than leaving pending, avoiding unterminated rows accumulating in /api/v1/usage reports;
            # cost_amount=0 adds no charges (resume never double charges, matching the no-proactive-charging red line).
            # Also submit expiry exceptions for settlement: original failure text is only available here; omitting it leaves bare failure.
            # Never swallow finalize failures; let worker finally mark_failed fallback run, preventing ApiCall
            # from staying pending forever and creating usage-report/settlement gaps.
            async with _remove_staged_output_on_error(staged_output_path):
                if api_call_id is not None:
                    await self.ledger.resume_failed(call_id=api_call_id, failure=exc)
                raise
        except asyncio.CancelledError:
            await _discard_staged_output(staged_output_path)
            raise
        except Exception:
            logger.exception("resume 失败 (video) task_id=%s job_id=%s", task_id, job_id)
            await _discard_staged_output(staged_output_path)
            raise

        video_ref = None
        video_uri = result.video_uri
        # Same treatment as first execution: a task's version metadata must match across restarts, or recorded actual seeds
        # would depend on when processes restarted.
        _merge_result_provenance(version_metadata, result)
        _collect_result_warnings(warnings, result)

        # Resume success precisely transitions pending → success. ledger.resume_success accepts backend result objects,
        # sharing video-channel success union dispatch (usage_tokens / generate_audio / actual billable duration).
        # Repos price costs using channel-reported resume-request metering, equivalent to generate accounting, preventing generated video with permanently
        # missing ledger records. Never swallow finalize errors; worker finally handles fallback. WHERE status='pending' protects idempotence.
        if api_call_id is not None:
            async with _remove_staged_output_on_error(staged_output_path):
                await self.ledger.resume_success(
                    call_id=api_call_id,
                    result=result,
                    usage=backend_metered_usage(self._video_backend, request, result),
                )
        else:
            logger.warning(
                "resume 未反查到待结算调用行 task_id=%s job_id=%s",
                task_id,
                job_id,
            )

        # backend.resume_video already downloaded paid results to request paths. Formal media requests write staging; commit callbacks
        # then save history and decide selection within controlled transactions. Nonformal requests append versions directly.
        await self._trash_if_target_deleted(
            resource_type=resource_type,
            resource_id=resource_id,
            staged_file=staged_output_path,
            task_id=task_id,
        )
        await self._prepare_formal_video_commit(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            before_formal_commit=before_formal_commit,
        )

        committed = await self._commit_video_output_version(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            output_path=output_path,
            staged_output_path=staged_output_path,
            duration_seconds=duration_int,
            version_metadata=version_metadata,
            commit_formal_output=commit_formal_output,
        )

        return output_path, committed.version, video_ref, video_uri
