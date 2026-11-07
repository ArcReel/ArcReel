"""Connection tests: submit real generation and poll to terminals, incurring costs.

Images/videos share paths, differing only in request types, ``call_type``, and artifact
filenames. Submission/polling/retrieval/accounting/writes/cancellation are media-independent.

Use production backends, ``poll_with_retry`` (video global
``video_poll_timeout_seconds``, consecutive-failure budgets/backoff/``Retry-After``), and download paths. Only carriers differ:
process asyncio tasks, outside worker queues; artifacts enter neither projects nor libraries. Passing tests therefore
means actual model-row usability, not another test-only path succeeding.

State lives in memory until terminals, then persists to ``DataRootLayout.trial_runs_dir/{id}/`` and leaves memory; readers
use disk. Delete whole directories after 24h. Cancellation stops local polling and settles failed; remote stopping belongs to backend
cancellation paths, independent of settlement, because costs may already exist and cannot be erased by user cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from arcreel_market_core.endpoint_definition import definition_media_type
from arcreel_market_core.video_backend_contract import (
    ProviderResponseStage,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.backend_assembly.specs import effective_builtin_video_capabilities
from lib.backends.container_sniff import CONTAINER_HEAD_BYTES, sniff_container
from lib.backends.image_backends.base import (
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.backends.image_capabilities import gate_image_request
from lib.backends.providers import CALL_TYPE_IMAGE, CALL_TYPE_VIDEO, CallPurpose
from lib.backends.video_frame_slots import resolve_first_frame_aspect_ratio, resolve_video_capabilities
from lib.billing.ledger import Ledger
from lib.config.resolver import ConfigResolver
from lib.custom_provider import is_custom_provider
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.declarative_backend import DeclarativeRuntimeError, DeclarativeVideoBackend
from lib.custom_provider.declarative_image_backend import DeclarativeImageBackend
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.usage_repo import bound_provider_response
from lib.generation.task_failure import encode_failure
from lib.infra.async_thread import run_noninterruptible_async, run_noninterruptible_sync, run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout

from .check import STAGES, check_response, stage_report_payload
from .inputs import EndpointTestCredentials, EndpointTestParameters
from .modes import EndpointTestMode, supports_test_mode

logger = logging.getLogger(__name__)

#: Result-directory TTL, cleaned at startup/creation without timers.
TRIAL_RUN_TTL_SECONDS = 24 * 3600

#: Poll-response count retained in results.
MAX_POLL_RESPONSES = 20

#: Four result stages: three runtime evidence phases, then artifact persistence.
TRIAL_RUN_STAGES = ("submit", "poll", "result", "artifact")

_RESULT_FILE = "result.json"

#: ``media_type`` → artifact filenames. Extensions distinguish both media in shared result directories,
#: alongside read-side MIME determined from bytes.
_ARTIFACT_FILE_BY_MEDIA_TYPE: Mapping[str, str] = {"video": "artifact.mp4", "image": "artifact.png"}

#: ``start`` run_id form (``uuid4().hex``).
_RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class TrialRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TrialRunBusyError(Exception):
    """Users already have running connection tests."""


@dataclass(frozen=True)
class TrialRunTarget:
    """Connection-test targets: accounting identities + backend construction.

    Inline definitions/model rows converge into identical values, sharing accounting/polling/writes/cancellation.
    This convergence directly yields shared resources/results/accounting for built-ins/custom providers.
    ``definition`` exists only for declarative targets, inline or model-attached custom/built-in definitions,
    enabling rendered requests/per-stage extraction; Python endpoints leave both sections empty.
    """

    provider: str
    model: str
    build_backend: Callable[[], Awaitable[Any]]
    definition: Mapping[str, Any] | None = None
    #: Endpoint video/image type determines request/call_type/artifact filename.
    #: Declarative/ComfyUI definitions declare media through ``definition_media_type``.
    media_type: str = "video"
    #: Whether to run production presubmission gates: all paid channels do, rejecting violations before payment. ComfyUI costs
    #: fixed zero and derives capabilities from bindings unavailable to inline entries; gates would mislabel missing first-frame
    #: bindings as capability rejections, while users test precisely to see ComfyUI's own response.
    gate_capabilities: bool = True
    #: Whether video capabilities use built-in effective evaluation with entries/user-added overrides
    #: (docs/adr/0104). True for built-in model rows only; CustomVideoBackend evaluates custom models, inline definitions lack rows.
    builtin_model_row: bool = False


def _as_float(value: object) -> float | None:
    """Enforce declared disk-field types; ``get`` maps invalid values to controlled 404 failures, never 500."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"不是数值：{value!r}")
    return float(value)


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"不是整数：{value!r}")
    return int(value)


def _as_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"不是字符串：{value!r}")


@dataclass
class TrialRun:
    """Complete connection-test results; running states populate initial fields only."""

    id: str
    status: TrialRunStatus
    provider: str
    model: str
    created_at: float
    #: Video/image result media, selecting player versus image readers.
    media_type: str = "video"
    finished_at: float | None = None
    api_call_id: int | None = None
    #: Provider ids: ComfyUI ``prompt_id``, declarative ``task_id``.
    provider_job_id: str | None = None
    #: Rendered/redacted submissions; None without renderable definitions, Python/ComfyUI endpoints.
    request: dict[str, Any] | None = None
    submit_response: object | None = None
    poll_responses: list[object] = field(default_factory=list)
    result_response: object | None = None
    #: Per-stage extraction with :data:`check.STAGES` keys; empty for Python endpoints without readable definitions.
    extractions: dict[str, Any] = field(default_factory=dict)
    video_url: str | None = None
    duration_seconds: int | None = None
    error: str | None = None
    has_artifact: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "provider": self.provider,
            "model": self.model,
            "media_type": self.media_type,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "api_call_id": self.api_call_id,
            "provider_job_id": self.provider_job_id,
            "stages": self.stage_states(),
            "request": self.request,
            "submit_response": self.submit_response,
            "poll_responses": self.poll_responses,
            "result_response": self.result_response,
            "extractions": self.extractions,
            "video_url": self.video_url,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "has_artifact": self.has_artifact,
        }

    def stage_states(self) -> dict[str, str]:
        """Four stages: reached ``done``, unreached ``skipped`` on terminals / ``pending`` while running.

        Never guess failure stages. Rejected submissions already have captured submission responses; marking first unreached
        stages red would blame polling. ``error`` failure codes identify failures.

        Compute in ``to_payload``, never duplicate stored state: all evidence is already in results,
        permitting old persisted results to recompute under current semantics.
        """
        reached = {
            "submit": self.submit_response is not None,
            "poll": bool(self.poll_responses),
            "result": self.result_response is not None,
            "artifact": self.has_artifact,
        }
        settled = self.status in (TrialRunStatus.SUCCEEDED, TrialRunStatus.FAILED)
        return {
            stage: "done" if reached[stage] else ("skipped" if settled else "pending") for stage in TRIAL_RUN_STAGES
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TrialRun:
        return cls(
            id=str(payload["id"]),
            status=TrialRunStatus(payload["status"]),
            provider=str(payload.get("provider", "")),
            model=str(payload.get("model", "")),
            media_type=str(payload.get("media_type") or "video"),
            created_at=_as_float(payload.get("created_at")) or 0.0,
            finished_at=_as_float(payload.get("finished_at")),
            api_call_id=_as_int(payload.get("api_call_id")),
            provider_job_id=_as_str(payload.get("provider_job_id")),
            request=payload.get("request"),
            submit_response=payload.get("submit_response"),
            poll_responses=list(payload.get("poll_responses") or []),
            result_response=payload.get("result_response"),
            extractions=dict(payload.get("extractions") or {}),
            video_url=_as_str(payload.get("video_url")),
            duration_seconds=_as_int(payload.get("duration_seconds")),
            error=_as_str(payload.get("error")),
            has_artifact=bool(payload.get("has_artifact")),
        )


class _ResponseCapture:
    """Capture runtime-labeled responses by stage, bounding each to ledger diagnostic 64 KiB."""

    def __init__(self) -> None:
        self.submit: object | None = None
        self.polls: deque[object] = deque(maxlen=MAX_POLL_RESPONSES)
        self.result: object | None = None

    def add(self, stage: ProviderResponseStage, body: object) -> None:
        bounded = bound_provider_response(body)
        if stage == "submit":
            self.submit = bounded
        elif stage == "poll":
            self.polls.append(bounded)
        else:
            self.result = bounded


class TrialRunManager:
    """Process-local connection-test registry: concurrency gates/tasks/terminal persistence/TTL cleanup."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        ledger: Ledger | None = None,
        read_poll_timeout: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        self._root = root
        self._ledger = ledger or Ledger()
        self._read_poll_timeout = read_poll_timeout
        self._runs: dict[str, TrialRun] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, str] = {}  # run_id -> user_id
        self._staging: dict[str, Path] = {}  # run_id → temporary assets.
        self._response_writes: dict[str, set[asyncio.Task[None]]] = {}  # run_id → in-flight diagnostics.
        self._settling: set[str] = set()  # Known-outcome finishing runs resist cancellation.

    @property
    def root(self) -> Path:
        root = self._root or DataRootLayout.current().trial_runs_dir
        root.mkdir(parents=True, exist_ok=True)
        return root

    def purge_expired(self, *, now: float | None = None) -> int:
        """Remove TTL-expired result directories, returning counts; scan at startup/every creation."""
        return self._purge_expired(frozenset(self._runs), now if now is not None else time.time())

    def _purge_expired(self, active: Collection[str], moment: float) -> int:
        """``purge_expired`` scan body for threads, with active-run snapshots supplied by event-loop callers."""
        removed = 0
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.name in active:
                continue
            try:
                if not _expired(entry, moment):
                    continue
                _remove_tree(entry)
            except OSError:
                logger.warning("测试连接结果目录清理失败：%s", entry.name, exc_info=True)
                continue
            removed += 1
        return removed

    def _expired(self, run_id: str) -> bool:
        """Whether TTL expired, shared by reads/cleanup.

        Cleanup scans only startup/creation; readers check independently so 24h expiry on quiet servers
        never depends on another user starting tests.
        """
        try:
            return _expired(self.root / run_id, time.time())
        except OSError:
            return False

    async def start(
        self,
        target: TrialRunTarget,
        parameters: EndpointTestParameters,
        *,
        user_id: str = DEFAULT_USER_ID,
        assets: Mapping[str, Path | list[Path] | None] | None = None,
        staging: Path | None = None,
        request_preview: dict[str, Any] | None = None,
    ) -> TrialRun:
        """Dispatch tests, rejecting users with active runs.

        Raises:
            TrialRunBusyError: Users already have running tests.
        """
        if user_id in self._owners.values():
            raise TrialRunBusyError(user_id)
        run_id = uuid.uuid4().hex
        run = TrialRun(
            id=run_id,
            status=TrialRunStatus.QUEUED,
            provider=target.provider,
            model=target.model,
            media_type=target.media_type,
            created_at=time.time(),
            request=request_preview,
        )
        self._runs[run_id] = run
        self._owners[run_id] = user_id
        if staging is not None:
            self._staging[run_id] = staging
        # Reserve slots/assets before creating directories: failures such as full disks/readonly mounts would leave occupied slots,
        # permanently busy tests, while cancellation sees 404 without registered tasks, like settlement-failure deadlocks.
        # Reserve before first await: no yielding between busy checks/reservations underpins concurrency gates.
        try:
            run_dir = await run_sync_transaction(self._prepare_run_dir, run_id, frozenset(self._runs))
            task = asyncio.create_task(self._execute(run, run_dir, target, parameters, assets or {}))
        except BaseException:
            await run_sync_transaction(self._discard, run_id, self._release(run_id))
            raise
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
        return run

    def get(self, run_id: str) -> TrialRun | None:
        """Read active runs from memory, terminal runs from disk; canceled/restart-interrupted runs lack result files."""
        if run_id in self._runs:
            return self._runs[run_id]
        # Caller-supplied run_id path segments validate against generated formats before disk reads, preventing ".."/Windows backslashes escaping roots.
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        if self._expired(run_id):
            return None
        path = self.root / run_id / _RESULT_FILE
        try:
            # Deserialization/disk reads share controlled failures: unusable runs always mean None.
            return TrialRun.from_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    async def wait(self, run_id: str) -> TrialRun | None:
        """Wait for stopped runs/terminal results; already terminal/missing returns immediately.

        Product paths use ``GET`` polling; this supports mandatory completion waits,
        shutdown draining and tests.
        """
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return await asyncio.to_thread(self.get, run_id)

    def artifact_path(self, run_id: str) -> Path | None:
        """Persisted artifacts with media-specific names, avoiding initial result reads just to find files."""
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            return None
        if self._expired(run_id):
            return None
        for name in _ARTIFACT_FILE_BY_MEDIA_TYPE.values():
            path = self.root / run_id / name
            if path.is_file():
                return path
        return None

    async def cancel(self, run_id: str) -> bool:
        """Stop local polling and settle cancellation.

        Remote stops belong to backends: ComfyUI stops through ``prompt_id``; unstoppable channels keep running/charging,
        while settlement always follows cancellation.

        Runs with known outcomes, provider success or determined failure, reject late cancellation with False:
        accounting already settled; directory deletion would lose billed results.
        """
        task = self._tasks.get(run_id)
        run = self._runs.get(run_id)
        if task is None or run is None or run_id in self._settling:
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Artifact persistence may consume cancellation and return success; recheck after task completion, never delete settled results.
        if run.status in (TrialRunStatus.SUCCEEDED, TrialRunStatus.FAILED):
            return False
        # Shielded diagnostic writes may outlive stopped tasks. Wait before settlement, avoiding resume_cancelled
        # concurrently updating the same row or shutdown closing DB before writes finish.
        for write in list(self._response_writes.get(run_id, ())):
            with contextlib.suppress(Exception):
                await write
        # Scopes already settle cancellation; this handles failures from writes/closed loops.
        # ``resume_cancelled`` guards ``status='pending'``, leaving settled rows unchanged. Pending
        # cannot remain for user-abandoned calls.
        # Even settlement exceptions release slots/directories: stopped tasks leaving reservations cause permanent busy,
        # while repeat cancellation gets 404 after task removal, recoverable only by restart.
        try:
            if run.api_call_id is not None:
                await self._ledger.resume_cancelled(call_id=run.api_call_id)
        finally:
            await run_sync_transaction(self._discard, run_id, self._release(run_id))
        return True

    async def _execute(
        self,
        run: TrialRun,
        run_dir: Path,
        target: TrialRunTarget,
        parameters: EndpointTestParameters,
        assets: Mapping[str, Path | list[Path] | None],
    ) -> None:
        capture = _ResponseCapture()
        run.status = TrialRunStatus.RUNNING
        try:
            poll_timeout = await self._poll_timeout()
            # Assemble backends before opening accounting: assembly failures (missing model rows/provider misconfiguration) send no bytes;
            # pending→failed rows would invent calls that never happened.
            backend = await target.build_backend()
            is_video = target.media_type == "video"
            # Capability gates/ratio overrides share production request-tier-resolved capabilities: endpoints declaring
            # ``video_capabilities_for_tier`` narrow by resolution; unnarrowed properties would overpermit versus production.
            # Tests omit ``service_tier``, sending/evaluating default tiers.
            video_caps = resolve_video_capabilities(backend, resolution=parameters.resolution) if is_video else None
            # Match production gates: effective capabilities both gate and travel with backend requests.
            if video_caps is not None and target.builtin_model_row:
                video_caps = effective_builtin_video_capabilities(video_caps, target.provider, target.model)
            # Production gates before accounting reject declared violations without paid submissions;
            # connection tests share gates. Exempt targets: ``TrialRunTarget.gate_capabilities``.
            if target.gate_capabilities:
                if video_caps is None:
                    await gate_image_request(
                        backend, provider_id=target.provider, reference_count=_reference_image_count(assets)
                    )
                else:
                    await _gate_trial_video_request(video_caps, target, parameters, assets)
            # first_frame_ratio_adaptive_only endpoints accept only adaptive when first frames exist;
            # sent/accounted values differ: ledger retains requested ratio intent, matching production responsibilities.
            request_aspect_ratio = resolve_first_frame_aspect_ratio(
                caps=video_caps,
                aspect_ratio=parameters.aspect_ratio,
                has_first_frame=_single(assets.get("start_image")) is not None,
            )
            async with self._ledger.record(
                project_name="",
                call_type=CALL_TYPE_VIDEO if is_video else CALL_TYPE_IMAGE,
                model=target.model,
                provider=target.provider,
                prompt=parameters.prompt,
                resolution=parameters.resolution,
                # Duration/audio belong to video; image accounting records neither. A fabricated 5s would show nonexistent duration,
                # while image request shapes lack audio (``ImageGenerationRequest``); copying video form switches
                # would falsely claim audible image outputs.
                duration_seconds=parameters.duration_seconds if is_video else None,
                aspect_ratio=parameters.aspect_ratio,
                generate_audio=parameters.generate_audio if is_video else False,
                purpose=CallPurpose.ENDPOINT_TRIAL,
            ) as call:
                run.api_call_id = call.call_id
                on_response = self._response_writer(run, capture, call.call_id)
                request = (
                    self._video_request(
                        run_dir,
                        parameters,
                        assets,
                        poll_timeout,
                        aspect_ratio=request_aspect_ratio,
                        video_capabilities=video_caps,
                        on_provider_response=on_response,
                    )
                    if is_video
                    else self._image_request(
                        run_dir, parameters, assets, aspect_ratio=request_aspect_ratio, on_provider_response=on_response
                    )
                )
                result = await backend.generate(request)
                # Provider success received: runs now resist cancellation, finishing successfully (``cancel``).
                self._settling.add(run.id)
                call.success(result)
                run.provider_job_id = getattr(result, "task_id", None)
                # Provider artifact URLs have media-specific names, converging into one result field.
                run.video_url = getattr(result, "video_uri", None) or getattr(result, "image_uri", None)
                run.duration_seconds = getattr(result, "duration_seconds", None)
                run.has_artifact = await run_noninterruptible_sync(_artifact_file(run_dir, target.media_type).is_file)
            await run_noninterruptible_async(self._finish(run, run_dir, target, capture, TrialRunStatus.SUCCEEDED))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Both runtimes/gates share stable code + serializable params; encode for common
            # localized read paths, leaving other exceptions as raw strings.
            structured = isinstance(
                exc, DeclarativeRuntimeError | ComfyuiError | VideoCapabilityError | ImageCapabilityError
            )
            run.error = encode_failure(exc.code, **exc.params) if structured else str(exc)
            self._settling.add(run.id)
            await run_noninterruptible_async(self._finish(run, run_dir, target, capture, TrialRunStatus.FAILED))

    def _response_writer(
        self, run: TrialRun, capture: _ResponseCapture, call_id: int
    ) -> Callable[[ProviderResponseStage, object], Awaitable[None]]:
        """Shared response diagnostic callbacks merge results/write ledger diagnostics across request types."""

        async def on_provider_response(stage: ProviderResponseStage, body: object) -> None:
            capture.add(stage, body)
            # Merge captures incrementally: four stages derive from these three fields; terminal-only merging would leave running GET
            # with four empty indicators forever.
            _merge_capture(run, capture)
            # Users may cancel during diagnostics. Interrupted writes leave half-open transactions,
            # forcing failed settlement on broken connections. Shield lets writes finish while
            # cancellation propagates outward. Register writes per run because shield protects coroutines only;
            # they outlive outer tasks, so cancellation waits before settlement (``cancel``).
            write = asyncio.ensure_future(self._ledger.record_provider_response(call_id=call_id, body=body))
            writes = self._response_writes.setdefault(run.id, set())
            writes.add(write)
            write.add_done_callback(writes.discard)
            await asyncio.shield(write)

        return on_provider_response

    def _video_request(
        self,
        run_dir: Path,
        parameters: EndpointTestParameters,
        assets: Mapping[str, Path | list[Path] | None],
        poll_timeout: int,
        *,
        aspect_ratio: str,
        video_capabilities: VideoCapabilities | None,
        on_provider_response: Callable[[ProviderResponseStage, object], Awaitable[None]],
    ) -> VideoGenerationRequest:
        reference_images = assets.get("reference_images")
        reference_audio = assets.get("reference_audio_files")
        return VideoGenerationRequest(
            prompt=parameters.prompt,
            output_path=_artifact_file(run_dir, "video"),
            aspect_ratio=aspect_ratio,
            duration_seconds=parameters.duration_seconds,
            resolution=parameters.resolution,
            start_image=_single(assets.get("start_image")),
            end_image=_single(assets.get("end_image")),
            reference_images=list(reference_images) if isinstance(reference_images, list) else None,
            reference_audio_files=list(reference_audio) if isinstance(reference_audio, list) else None,
            generate_audio=parameters.generate_audio,
            poll_timeout_seconds=poll_timeout,
            on_provider_response=on_provider_response,
            video_capabilities=video_capabilities,
        )

    def _image_request(
        self,
        run_dir: Path,
        parameters: EndpointTestParameters,
        assets: Mapping[str, Path | list[Path] | None],
        *,
        aspect_ratio: str,
        on_provider_response: Callable[[ProviderResponseStage, object], Awaitable[None]],
    ) -> ImageGenerationRequest:
        """Image test requests.

        No first/last frames/audio references: image definitions lack these semantic keys (``comfyui.bindings``),
        and UI omits controls. Poll limits likewise absent: image channels use fixed
        ``comfyui_image_backend.IMAGE_POLL_TIMEOUT_SECONDS``, never video settings.
        """
        reference_images = assets.get("reference_images")
        return ImageGenerationRequest(
            prompt=parameters.prompt,
            output_path=_artifact_file(run_dir, "image"),
            reference_images=[
                ReferenceImage(path=str(path))
                for path in (reference_images if isinstance(reference_images, list) else [])
            ],
            aspect_ratio=aspect_ratio,
            image_size=parameters.resolution,
            on_provider_response=on_provider_response,
        )

    async def _poll_timeout(self) -> int:
        if self._read_poll_timeout is not None:
            return await self._read_poll_timeout()
        from lib.config.service import read_video_poll_timeout_seconds

        return await read_video_poll_timeout_seconds()

    async def _finish(
        self,
        run: TrialRun,
        run_dir: Path,
        target: TrialRunTarget,
        capture: _ResponseCapture,
        status: TrialRunStatus,
    ) -> None:
        """Mark terminal/persist/release slots. Callers wrap entirely in ``run_noninterruptible_async``:
        midflight cancellation would leave terminal memory without disk results."""
        run.status = status
        run.finished_at = time.time()
        _merge_capture(run, capture)
        if run.provider_job_id is None:
            run.provider_job_id = _submitted_prompt_id(capture.submit)
        run.extractions = _stage_reports(target.definition, capture)
        payload = json.dumps(run.to_payload(), ensure_ascii=False, default=str)
        # Persist before releasing memory: readers fall back to disk after absent memory, otherwise temporary 404 windows appear.
        await run_sync_transaction(_settle_files, run_dir / _RESULT_FILE, payload, self._staging.get(run.id))
        self._release(run.id)

    async def shutdown(self) -> None:
        """Shutdown stops all active runs and settles cancellation.

        Event-loop destruction leaves no process for later accounting, so shutdown uses cancellation: stop tasks, whose scopes
        settle cancelled, then finalize any remaining pending rows.

        Individual settlement failures, such as unavailable DB, cannot abort draining: propagated exceptions would leave
        later runs active and skip subsequent HTTP-client/DB shutdown.

        Already-finishing runs reject cancellation; shutdown waits for results before closing DB, preserving final accounting writes.
        """
        for run_id in list(self._tasks):
            task = self._tasks.get(run_id)
            try:
                if not await self.cancel(run_id) and task is not None:
                    # ``asyncio.wait`` does not propagate task exceptions to shutdown; cancellation of shutdown itself still propagates.
                    await asyncio.wait([task])
            except Exception:
                logger.warning("关停时结算测试连接失败：%s", run_id, exc_info=True)

    def _release(self, run_id: str) -> Path | None:
        """Release user slots and hand temporary assets to caller cleanup; discard after use, never retain on disk."""
        self._runs.pop(run_id, None)
        self._owners.pop(run_id, None)
        self._response_writes.pop(run_id, None)
        self._settling.discard(run_id)
        return self._staging.pop(run_id, None)

    def _prepare_run_dir(self, run_id: str, active: Collection[str]) -> Path:
        """Purge expired directories before creating run results, in threads."""
        self._purge_expired(active, time.time())
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _discard(self, run_id: str, staging: Path | None) -> None:
        """Remove temporary assets/results of unterminated runs, in threads."""
        if staging is not None:
            _remove_tree(staging)
        _remove_tree(self.root / run_id)


def _artifact_file(run_dir: Path, media_type: str) -> Path:
    return run_dir / _ARTIFACT_FILE_BY_MEDIA_TYPE[media_type]


def _settle_files(result_file: Path, payload: str, staging: Path | None) -> None:
    """Persist terminal results and remove temporary assets, in threads."""
    try:
        result_file.write_text(payload, encoding="utf-8")
    except OSError:
        logger.warning("测试连接结果写盘失败 run_id=%s", result_file.parent.name, exc_info=True)
    if staging is not None:
        _remove_tree(staging)


async def _gate_trial_video_request(
    caps: VideoCapabilities,
    target: TrialRunTarget,
    parameters: EndpointTestParameters,
    assets: Mapping[str, Path | list[Path] | None],
) -> None:
    """Production presubmission video gate using request-tier-resolved ``caps``."""
    from lib.backends.video_frame_slots import gate_video_request, plan_frame_slots
    from lib.speech.audio_utils import probe_reference_audio_total_seconds

    reference_images = assets.get("reference_images")
    reference_audio = assets.get("reference_audio_files")
    audio_files = list(reference_audio) if isinstance(reference_audio, list) else None
    # Production-shared probing only for declared total-duration constraints. Probe failures, such as unavailable bundled ffmpeg, yield None,
    # letting gates skip unknown dimensions instead of rejecting.
    total_seconds = (
        await probe_reference_audio_total_seconds(audio_files)
        if audio_files and caps.max_reference_audio_total_seconds is not None
        else None
    )
    images = list(reference_images) if isinstance(reference_images, list) else None
    end_image = _single(assets.get("end_image"))
    # has_image uses complete production-shared slot plans: reference-driven S2V may lack first frames but contain references;
    # first-frame-only checks falsely classify every valid request text-only and reject text_to_video=false.
    slot_plan = plan_frame_slots(
        start_image=_single(assets.get("start_image")),
        end_image=end_image,
        reference_images=images,
    )
    gate_video_request(
        caps=caps,
        provider=target.provider,
        model=target.model,
        prompt=parameters.prompt,
        has_image=bool(slot_plan.specs),
        has_first_frame=slot_plan.start_index is not None,
        end_image=end_image,
        reference_images=images,
        reference_audio_files=audio_files,
        reference_audio_total_seconds=total_seconds,
    )


def declarative_target(
    definition: Mapping[str, Any],
    credentials: EndpointTestCredentials,
    parameters: EndpointTestParameters,
    *,
    provider: str | None = None,
) -> TrialRunTarget:
    """Inline targets construct declarative image/video backends directly by definition media.

    ``provider`` defaults to ``base_url`` hosts: inline credentials lack provider identities, while accounting must
    identify expenses. Definitions hardcoding all three URLs lack base_url, so use submission
    hosts, the actual payment destination.
    """
    label = provider or provider_from_base_url(credentials.base_url) or _definition_host(definition)
    media_type = definition_media_type(definition)
    backend_class = DeclarativeImageBackend if media_type == "image" else DeclarativeVideoBackend

    async def build() -> DeclarativeVideoBackend | DeclarativeImageBackend:
        return backend_class(
            api_key=credentials.api_key,
            base_url=credentials.base_url,
            model=parameters.model,
            definition=definition,
            provider=label,
        )

    return TrialRunTarget(
        provider=label,
        model=parameters.model,
        build_backend=build,
        definition=definition,
        media_type=media_type,
    )


def model_ref_target(
    provider_id: str,
    model_id: str,
    *,
    resolver: ConfigResolver,
    definition: Mapping[str, Any] | None = None,
    media_type: str = "video",
) -> TrialRunTarget:
    """Model-row targets use production assembly shared by built-ins/custom providers.

    Callers supply ``definition`` for custom/built-in declarative endpoints, enabling rendered requests/per-stage extraction;
    Python endpoints leave both empty.

    ``media_type`` selects registries from model-row image/video declarations.
    """

    async def build() -> Any:
        from lib.backends.backend_assembly import assemble_backend

        return await assemble_backend(
            provider_id=provider_id, media_type=media_type, model_id=model_id, resolver=resolver
        )

    return TrialRunTarget(
        provider=provider_id,
        model=model_id,
        build_backend=build,
        definition=definition,
        media_type=media_type,
        builtin_model_row=not is_custom_provider(provider_id),
    )


def artifact_media_type(path: Path) -> str:
    """Persisted artifact MIME from headers.

    Never extensions: this layer chooses names by ``media_type``, while provider export settings determine containers;
    same-family mp4/mov changes do not rename artifacts. Unknown bytes use generic binary, letting readers inspect;
    wrong MIME forces browsers to decode incorrectly and complicates diagnosis more than no declaration.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(CONTAINER_HEAD_BYTES)
    except OSError:
        return "application/octet-stream"
    return sniff_container(head) or "application/octet-stream"


def provider_from_base_url(base_url: str) -> str:
    return urlsplit(base_url).hostname or base_url


def _definition_host(definition: Mapping[str, Any]) -> str:
    return urlsplit(str(definition.get("submit", {}).get("url", ""))).hostname or ""


def _reference_image_count(assets: Mapping[str, Path | list[Path] | None]) -> int:
    reference_images = assets.get("reference_images")
    return len(reference_images) if isinstance(reference_images, list) else 0


def _single(value: Path | list[Path] | None) -> Path | None:
    return value if isinstance(value, Path) else None


def _merge_capture(run: TrialRun, capture: _ResponseCapture) -> None:
    """Merge newly received responses into results."""
    run.submit_response = capture.submit
    run.poll_responses = list(capture.polls)
    run.result_response = capture.result


def _submitted_prompt_id(submit: object) -> str | None:
    """Recover provider ids from submission responses after postsubmission failure.

    Recognize only ``prompt_id``, ComfyUI's sole submission key. Declarative ids vary by definition and come through
    ``result.task_id``; guessing here could misidentify unrelated same-name fields as job ids.
    """
    if isinstance(submit, Mapping):
        prompt_id = str(submit.get("prompt_id") or "").strip()
        return prompt_id or None
    return None


def _stage_reports(definition: Mapping[str, Any] | None, capture: _ResponseCapture) -> dict[str, Any]:
    """Per-stage extraction reports; empty without readable paths.

    Same gate as response-validation mode, which these reports implement. Kinds unable to explain response interpretation
    cannot report here either: ComfyUI extraction is fixed code, not configurable paths.
    """
    if definition is None or not supports_test_mode(str(definition.get("kind", "")), EndpointTestMode.CHECK_RESPONSE):
        return {}
    polls = list(capture.polls)
    bodies: dict[str, object] = {}
    if capture.submit is not None:
        bodies["submit"] = capture.submit
    if polls:
        bodies["poll"] = polls[-1]
    if capture.result is not None:
        bodies["result"] = capture.result
    reports: dict[str, Any] = {}
    for stage in STAGES:
        body = bodies.get(stage)
        if body is None:
            continue
        try:
            reports[stage] = stage_report_payload(check_response(definition, stage, body))
        except Exception:
            logger.debug("测试连接的 %s 节提取报告生成失败", stage, exc_info=True)
    return reports


def _expired(entry: Path, now: float) -> bool:
    return now - entry.stat().st_mtime > TRIAL_RUN_TTL_SECONDS


def _remove_tree(path: Path) -> None:
    shutil.rmtree(
        path, ignore_errors=True
    )  # hard-delete: out-of-scope -- endpoint trial directories under data roots, outside projects


_manager: TrialRunManager | None = None


def trial_run_manager() -> TrialRunManager:
    """Sole process registry; first access purges expired directories, including restart-interrupted runs without results."""
    global _manager
    if _manager is None:
        _manager = TrialRunManager()
        _manager.purge_expired()
    return _manager


async def shutdown_trial_runs() -> None:
    """Shutdown entry without creating never-accessed registries merely for shutdown."""
    if _manager is not None:
        await _manager.shutdown()
