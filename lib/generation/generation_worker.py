"""
Background worker that consumes generation tasks from SQLite queue.

Per-provider × media_type scheduling separates two independent parts: CapacityTable (limits from
ConfigService user configuration) + SlotTable (runtime slot ledger).

Supported deployments start one uvicorn process; server lifespan creates its unique
GenerationWorker with matching lifetimes. Orphans arise only from process restarts. Leases prevent
repeated claims during brief process overlap and defend cross-process takeover, without making multiple uvicorn workers supported;
if supporting multiple processes, revisit orphan detection (see ``docs/adr/0007``). Cancellation is available only for queued tasks;
workers receive no cancellation signals, and running tasks always reach terminal states (see ``docs/adr/0006``).

Application assembly injects task/resumption executors. Restart recovery and resumption live in ``restart_recovery`` and
``video_resume``, held by workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import update

from lib.billing.ledger import Ledger
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService, read_video_poll_timeout_seconds
from lib.custom_provider.discovery_formats import is_comfyui_protocol
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.db import async_session_factory, safe_session_factory
from lib.db.models.task import Task
from lib.db.repositories.base import rowcount
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_queue import (
    I2I_ONLY_TASK_TYPES,
    TASK_POLL_INTERVAL_SEC,
    TASK_WORKER_HEARTBEAT_SEC,
    TASK_WORKER_LEASE_TTL_SEC,
    DispatchProviderChanged,
    GenerationQueue,
    get_generation_queue,
    resolve_video_execution_for_queued_task,
)
from lib.generation.render_lane import RENDER_LANE_CONCURRENCY, RENDER_MEDIA_TYPE, RENDER_PROVIDER_ID
from lib.generation.restart_recovery import InterruptedCallSettler, RestartRecovery
from lib.generation.task_failure import encode_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.generation.video_resume import ResumeExecutor, VideoResumeRunner, fail_task_of_deleted_project
from lib.project.project_manager import get_project_manager
from lib.project.task_project_claim import TaskProjectClaim, claim_task_project

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.config.registry import ProviderMeta

    ProviderProjection = Callable[[dict[str, Any]], Awaitable[str]]


class TaskExecutor(Protocol):
    """Task executor: execute claimed tasks by type, returning task results.

    ``claimed_provider_id`` is the claimed capacity bucket; image/audio/video execution entries check providers against it,
    raising ``DispatchProviderChanged`` on mismatch before calling providers.
    """

    def __call__(self, task: dict[str, Any], *, claimed_provider_id: str) -> Awaitable[dict[str, Any]]: ...


logger = logging.getLogger(__name__)

# Treat lease loss beyond ``lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT`` as actual owner switching
# (another worker process held leases and wrote new orphans), requiring rescans. Short renewal flaps
# do not trigger. Default lease_ttl 10s → threshold 30s. Constants support test injection/tuning.
_ORPHAN_RESCAN_LEASE_LOST_MULT = 3

# Default provider used when a task payload does not specify one.
DEFAULT_PROVIDER = "gemini-aistudio"


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


#: Default concurrency per lane for ComfyUI-protocol providers: typically one GPU behind one ComfyUI service.
COMFYUI_LANE_DEFAULT = 1


def _parse_lane_max(config: dict[str, str], key: str, default: int, provider_id: str) -> int:
    """Parse individual lane concurrency limits with per-key tolerance.

    Failed parsing falls back to defaults with warnings, preventing individual corrupt values (legacy dirty data preceding write validation)
    from breaking whole-table loading. Parseable negatives retain clamp semantics (→0, lane fails fast), with warnings.
    """
    raw = config.get(key)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("供应商 %s 的 %s 配置值非法（%r），回退默认值 %d", provider_id, key, raw, default)
        return default
    if parsed < 0:
        logger.warning("供应商 %s 的 %s 配置值为负（%r），按 0 处理（该 lane 关闭）", provider_id, key, raw)
    return max(0, parsed)


@dataclass
class CapacityTable:
    """Per-provider concurrency limits keyed by ``provider_id × media_type``.

    Pure scalar configuration table, with provider config as sole truth. Changing concurrency changes one number only,
    never the slot ledger. ``get`` has three-state semantics distinguishing known-but-unsupported (0) from unknown providers (lazy defaults).
    """

    _limits: dict[str, dict[str, int]]  # provider_id → {media_type → limit}
    _defaults: dict[str, int]  # {"image": 5, "video": 3, "audio": 10}, lazy defaults for unknown providers

    def get(self, provider_id: str, media_type: str) -> int:
        """Return concurrency limits for ``(provider, media)``.

        - Known provider + lane in table → recorded value (possibly 0 = unsupported lane)
        - Known provider + lane absent → 0 (unsupported)
        - Entirely unknown provider → ``_defaults[media_type]`` (query only, never writes table)
        """
        if media_type == RENDER_MEDIA_TYPE:
            return RENDER_LANE_CONCURRENCY if provider_id == RENDER_PROVIDER_ID else 0
        lanes = self._limits.get(provider_id)
        if lanes is None:
            return self._defaults.get(media_type, 0)
        return lanes.get(media_type, 0)

    def replace(self, new_limits: dict[str, dict[str, int]]) -> None:
        """Replace whole-table numbers (reload entry). Slot ledgers and defaults remain unaffected."""
        self._limits = new_limits

    @staticmethod
    def _lane_limits(media_types: Any, image: int, video: int, audio: int) -> dict[str, int]:
        """Project limits into lane dictionaries by supported provider media_types; unsupported lanes → 0.

        Sole capacity-loading mapping: add one line here for new lanes.
        """
        return {
            "image": image if "image" in media_types else 0,
            "video": video if "video" in media_types else 0,
            "audio": audio if "audio" in media_types else 0,
        }

    @staticmethod
    def _lane_default(meta: ProviderMeta, lane: str, global_default: int) -> int:
        """Lane fallback without user configuration: provider-registry declared defaults if present, otherwise global defaults.

        Sole middle layer in three-level fallback, shared by from_env / from_db to prevent drift. ProviderMeta.__post_init__ ensures declared defaults
        have valid lane keys and integer values >=1 at import time.
        """
        return meta.default_concurrency.get(lane, global_default)

    @staticmethod
    def _custom_lane_default(provider: Any, column: int | None, global_default: int) -> int:
        """Custom-provider lane limits: column values when present, protocol defaults when NULL.

        ComfyUI providers are users' own GPUs; one image and one video lane each fill them. Global defaults (video 3,
        image 5) make tasks compete for one card, queueing remotely and invisibly locally. Users can still explicitly raise column values.
        Other protocols target commercial APIs and use global defaults.

        Separate from :meth:`_lane_default`, which reads built-in provider registry declarations. Custom providers lack
        registry entries; declarations originate solely from protocols.
        """
        if column is not None:
            return column
        return COMFYUI_LANE_DEFAULT if is_comfyui_protocol(provider.discovery_format) else global_default

    @classmethod
    def from_env(cls) -> CapacityTable:
        """Construct from environment variables / defaults (before DB availability or for tests)."""
        image_max = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
        video_max = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)
        audio_max = _read_int_env("AUDIO_MAX_WORKERS", 10, minimum=1)
        # Loading without user configuration: per-lane declared defaults if present, otherwise global defaults.
        limits = {
            pid: cls._lane_limits(
                catalog.provider_media_types(pid),
                cls._lane_default(meta, "image", image_max),
                cls._lane_default(meta, "video", video_max),
                cls._lane_default(meta, "audio", audio_max),
            )
            for pid, meta in PROVIDER_REGISTRY.items()
        }
        return cls(
            _limits=limits,
            _defaults={
                "image": image_max,
                "video": video_max,
                "audio": audio_max,
                "text": 1,
                RENDER_MEDIA_TYPE: RENDER_LANE_CONCURRENCY,
            },
        )

    @classmethod
    async def from_db(cls) -> CapacityTable:
        """Load capacity tables from ConfigService + PROVIDER_REGISTRY + custom providers."""
        default_image = _read_int_env("IMAGE_MAX_WORKERS", 5, minimum=1)
        default_video = _read_int_env("VIDEO_MAX_WORKERS", 3, minimum=1)
        default_audio = _read_int_env("AUDIO_MAX_WORKERS", 10, minimum=1)

        limits: dict[str, dict[str, int]] = {}
        async with safe_session_factory() as session:
            svc = ConfigService(session)
            all_configs = await svc.get_all_provider_configs()
            for provider_id, meta in PROVIDER_REGISTRY.items():
                config = all_configs.get(provider_id, {})
                # Without user lane configuration, _parse_lane_max falls back to supplied defaults:
                # declared defaults if present, otherwise global; configured user values override after parsing.
                image_max = _parse_lane_max(
                    config, "image_max_workers", cls._lane_default(meta, "image", default_image), provider_id
                )
                video_max = _parse_lane_max(
                    config, "video_max_workers", cls._lane_default(meta, "video", default_video), provider_id
                )
                audio_max = _parse_lane_max(
                    config, "audio_max_workers", cls._lane_default(meta, "audio", default_audio), provider_id
                )
                # _lane_limits handles unsupported lanes → 0 uniformly, shared by all three loading paths.
                limits[provider_id] = cls._lane_limits(
                    catalog.provider_media_types(provider_id), image_max, video_max, audio_max
                )

            repo = CustomProviderRepository(session)
            get_custom_endpoint = CustomEndpointRepository(session).get
            # One endpoint can serve multiple rows/providers. Cache media types by endpoint key, reading the DB once for the entire table.
            media_type_by_endpoint: dict[str, str] = {}
            for provider, models in await repo.list_providers_with_models():
                pid = provider.provider_id  # "custom-{id}"
                # Model rows can use ce- endpoints, whose media types are in their definitions; parse specs to read them.
                media_types: set[str] = set()
                for m in models:
                    if not m.is_enabled:
                        continue
                    media_type = media_type_by_endpoint.get(m.endpoint)
                    if media_type is None:
                        try:
                            media_type = (await resolve_endpoint_spec(m.endpoint, get_custom_endpoint)).media_type
                        except ValueError:
                            # Missing endpoints guarantee generation failure: never open lanes from these rows or invalidate whole-table refreshes.
                            logger.warning("无法解析模型 endpoint，容量表跳过该行: endpoint=%r", m.endpoint)
                            continue
                        media_type_by_endpoint[m.endpoint] = media_type
                    media_types.add(media_type)
                # Custom providers lack built-in registry declarations: two-level fallback uses column values when present,
                # protocol defaults when NULL (see _custom_lane_default). _lane_limits still handles
                # unsupported lanes uniformly.
                image_max = cls._custom_lane_default(provider, provider.image_max_workers, default_image)
                video_max = cls._custom_lane_default(provider, provider.video_max_workers, default_video)
                audio_max = cls._custom_lane_default(provider, provider.audio_max_workers, default_audio)
                limits[pid] = cls._lane_limits(media_types, image_max, video_max, audio_max)

        logger.info("从 DB 加载供应商容量表: %s", limits)
        return cls(
            _limits=limits,
            _defaults={
                "image": default_image,
                "video": default_video,
                "audio": default_audio,
                "text": 1,
                RENDER_MEDIA_TYPE: RENDER_LANE_CONCURRENCY,
            },
        )


@dataclass
class _Occupant:
    """One occupant: execution task + phase flag.

    Only video semaphore-throttled dispatchers produce ``pending=True`` during subtask queueing;
    image / audio lack semaphore dispatchers (audio backends are synchronous), always ``pending=False``. promote
    flips only this flag (naturally atomic), avoiding windows where tasks are momentarily in neither pending nor inflight containers.
    """

    task: asyncio.Future[Any]
    pending: bool = False


class SlotTable:
    """Slot ledger: ``(provider_id, media_type)`` → ``{task_id: _Occupant}``.

    Passive in-memory data structure, **capacity-independent**: callers supply ``capacity`` to ``has_room``.
    Never writes DB, resolves providers, chooses orphan policies or touches state-machine guards.

    ``capacity`` of ``0`` means only unsupported lanes, an internal sentinel. User-facing concurrency
    limits are integers ≥1 or blank; users cannot supply ``0`` (see ``docs/adr/0043``).

    **No residual empty buckets (by design)**: ``release`` / ``drain_finished`` delete ``(provider,media)``
    buckets along with their last occupants, ensuring ``occupied_providers`` never
    returns emptied providers (the basis for full-pool blacklists).
    """

    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], dict[str, _Occupant]] = {}

    def register(
        self,
        provider: str,
        media: str,
        task_id: str,
        task: asyncio.Future[Any],
        *,
        pending: bool = False,
    ) -> None:
        """Register occupancy, idempotently replacing; create buckets when absent."""
        self._slots.setdefault((provider, media), {})[task_id] = _Occupant(task=task, pending=pending)

    def promote(self, provider: str, media: str, task_id: str) -> None:
        """PENDING→INFLIGHT: call after sem.acquire succeeds, flipping only ``pending``.

        Occupants already reference the same subtask (``asyncio.current_task()`` is the originally registered task),
        so never replace tasks. No-op when absent.
        """
        bucket = self._slots.get((provider, media))
        if bucket is None:
            return
        occ = bucket.get(task_id)
        if occ is not None:
            occ.pending = False

    def release(self, provider: str, media: str, task_id: str) -> None:
        """Release regardless of phase, idempotently; delete emptied ``(provider,media)`` buckets."""
        bucket = self._slots.get((provider, media))
        if bucket is None:
            return
        bucket.pop(task_id, None)
        if not bucket:
            del self._slots[(provider, media)]

    def has_room(self, provider: str, media: str, capacity: int) -> bool:
        """``capacity>0`` and occupant count (including pending) < capacity."""
        if capacity <= 0:
            return False
        bucket = self._slots.get((provider, media))
        return (0 if bucket is None else len(bucket)) < capacity

    def occupied(self, provider: str, media: str) -> int:
        """Current occupant count (including pending)."""
        bucket = self._slots.get((provider, media))
        return 0 if bucket is None else len(bucket)

    def occupied_providers(self, media: str) -> set[str]:
        """Providers with ≥1 occupant under ``media``; omit empty buckets (blacklist source, including unknown providers)."""
        return {provider for (provider, m), bucket in self._slots.items() if m == media and bucket}

    def drain_finished(self) -> list[tuple[str, asyncio.Future[Any]]]:
        """Remove and return all done INFLIGHT occupants (leave pending untouched). ``(task_id, task)``."""
        finished: list[tuple[str, asyncio.Future[Any]]] = []
        for key in list(self._slots.keys()):
            bucket = self._slots[key]
            done_ids = [tid for tid, occ in bucket.items() if not occ.pending and occ.task.done()]
            finished.extend((tid, bucket.pop(tid).task) for tid in done_ids)
            if not bucket:
                del self._slots[key]
        return finished

    def active_task_ids(self) -> set[str]:
        """All occupant task_id values (pending+inflight), for self-active scans."""
        return {tid for bucket in self._slots.values() for tid in bucket}

    def all_active_tasks(self) -> list[asyncio.Future[Any]]:
        """All occupant execution tasks (pending+inflight), for shutdown waits."""
        return [occ.task for bucket in self._slots.values() for occ in bucket.values()]

    def clear(self) -> None:
        """Clear entire tables (shutdown cleanup)."""
        self._slots.clear()


async def _extract_provider(task: dict[str, Any]) -> str:
    """Extract a provider_id from a claimed task, used **only** for rate-limit slot routing.

    Image/audio/video tasks call once only when queue records are missing (or recorded providers no longer support the media type),
    persisting results. Text/render providers are fixed and resolved on every claim.

    Thin resolution-chain projection: dispatch media lanes (``media_type``) to ``resolve_video_backend`` /
    ``resolve_image_backend`` and take ``.provider_id``. Image tasks use ``generation_type="t2i"`` for a
    **representative** provider: real task types are unknown at claim time (see ``docs/adr/0001``). This approximation does not affect
    generation correctness (execution independently resolves precisely again). ``I2I_ONLY_TASK_TYPES`` (image edits/derivative asset images) are the
    sole exception (necessarily i2i, known at enqueue time), resolved through exact i2i slots. Both video modes ignore legacy enqueue payload identities. Storyboard buckets share
    ``video_bucket_for_queued_task`` with enqueue derivation; reference_video rereads latest
    project/script/unit and calls shared request projection with actual available assets.
    This provider projection serves slot ownership/rate-limit routing only, never execution identity; executors materialize requests
    from latest state at startup, requeueing mismatches with claimed slots.
    Catalog errors retain providers from exceptions; other resolution failures (unconfigured providers) fall back to DEFAULT_PROVIDER solely for rate limiting, without blocking claims.
    """
    project_name = task.get("project_name")
    raw_payload = task.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if task.get("task_type") == "reference_video" and isinstance(task.get("script_file"), str):
        # Task.script_file is the immutable queue coordinate. The payload copy is retained only for old/direct
        # callers and must not be able to redirect claim-time current-state projection to a different script.
        payload = {**payload, "script_file": task["script_file"]}
    # Distinguish video / audio / image by media lanes: task_types such as reference_video share the video lane.
    is_text = task.get("media_type") == "text"
    is_video = task.get("media_type") == "video" or task.get("task_type") in ("video", "reference_video")
    is_audio = task.get("media_type") == "audio" or task.get("task_type") == "tts"
    if is_text:
        return "text"
    if task.get("media_type") == RENDER_MEDIA_TYPE:
        return RENDER_PROVIDER_ID

    # Whole-operation fallback: any failure, including project loading (queued tasks may reference deleted/unreadable projects, where load_project raises
    # FileNotFoundError), falls back to DEFAULT_PROVIDER without escaping to block claim loops (see docstring).
    try:
        project: dict | None = None
        if project_name:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        resolver = ConfigResolver(async_session_factory)
        if is_video:
            resolved, _generation_type = await resolve_video_execution_for_queued_task(
                resolver=resolver,
                project=project,
                project_name=project_name,
                task_type=task.get("task_type", ""),
                payload=payload,
                resource_id=task.get("resource_id"),
            )
        elif is_audio:
            resolved = await resolver.resolve_audio_backend(project, payload)
        else:
            generation_type = "i2i" if task.get("task_type") in I2I_ONLY_TASK_TYPES else "t2i"
            resolved = await resolver.resolve_image_backend(project, payload, generation_type=generation_type)
    except ModelNotInCatalogError as exc:
        return exc.provider_id
    except Exception:
        logger.debug("provider 解析失败，回退 DEFAULT_PROVIDER 仅供限流路由", exc_info=True)
        return DEFAULT_PROVIDER
    return resolved.provider_id or DEFAULT_PROVIDER


class GenerationWorker:
    """Queue worker with per-provider image/video/audio/text lanes, the local render lane and single-active lease."""

    def __init__(
        self,
        queue: GenerationQueue | None = None,
        lease_name: str = "default",
        capacity: CapacityTable | None = None,
        slots: SlotTable | None = None,
        provider_projection: ProviderProjection = _extract_provider,
        lanes: tuple[str, ...] = ("image", "video", "audio", "text", RENDER_MEDIA_TYPE),
        *,
        executor: TaskExecutor,
        resume_executor: ResumeExecutor,
        settle_interrupted_calls: InterruptedCallSettler | None = None,
    ):
        self.queue = queue or get_generation_queue()
        # Derive current project providers only for unrecorded legacy tasks, fixed-provider text/render lanes and restart recovery.
        self._provider_projection = provider_projection
        self._executor = executor
        self._lanes = lanes
        self.lease_name = lease_name
        self.owner_id = f"worker-{uuid.uuid4().hex[:10]}"

        # Separate capacity tables (user-configured limits) from slot ledgers (runtime accounting): the former is configuration truth,
        # reload replacing numbers only; the latter holds inflight/pending with stable container references, never rebuilt.
        self._capacity: CapacityTable = capacity or CapacityTable.from_env()
        self._slots: SlotTable = slots or SlotTable()
        logger.info("Worker 初始容量表: %s", self._capacity._limits)
        self.lease_ttl = max(1.0, float(TASK_WORKER_LEASE_TTL_SEC))
        self.heartbeat_interval = max(0.5, float(TASK_WORKER_HEARTBEAT_SEC))
        self.poll_interval = max(0.1, float(TASK_POLL_INTERVAL_SEC))

        self._main_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._owns_lease = False
        # One-shot scan switch: under exclusive single-lease architecture, once processes scan orphans they do not rescan;
        # _lease_lost_monotonic thresholds clear it on actual owner switches, never short flaps.
        self._orphan_handled_once: bool = False
        # Worker construction time precedes Web request acceptance. Initial settlement includes taskless calls started
        # before it only; later calls still run in this process. Subsequent rescans occur during process life and never touch taskless rows.
        self._constructed_at = datetime.now(UTC)
        self._startup_settled: bool = False
        self._lease_lost_monotonic: float | None = None

        self._resume = VideoResumeRunner(
            queue=self.queue,
            resume_executor=resume_executor,
            provider_projection=provider_projection,
        )
        self._recovery = RestartRecovery(
            queue=self.queue,
            capacity=self._capacity,
            slots=self._slots,
            stop_event=self._stop_event,
            reload_limits=lambda: self.reload_limits(),
            provider_projection=provider_projection,
            resume=self._resume,
            # Startup settlement entry (public accounting-layer method). Default uses the queue's persistence wiring.
            settle_interrupted_calls=settle_interrupted_calls or self._settle_interrupted_calls_via_ledger,
        )

    # ------------------------------------------------------------------
    # Capacity management
    # ------------------------------------------------------------------

    async def reload_limits(self) -> None:
        """Reload per-provider concurrency limits from DB into the CapacityTable.

        Replace capacity numbers only (``replace``), never slot ledgers. Inflight/pending container references remain
        stable, eliminating fragile moving of running tasks during reload. Deleted providers' ongoing occupancy
        drains normally; new-task capacity uses ``CapacityTable.get``: unsupported lanes
        become 0 (fail fast), entirely missing providers use lazy defaults (already unresolvable,
        tasks fall back to DEFAULT_PROVIDER or fail execution, never actually occupying missing providers at default capacity).
        """
        try:
            new = await CapacityTable.from_db()
        except Exception:
            logger.warning("从 DB 加载供应商配置失败，保持当前配置", exc_info=True)
            return
        self._capacity.replace(new._limits)
        logger.info("已更新供应商容量表: %s", self._capacity._limits)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._main_task = asyncio.create_task(self._run_loop(), name="generation-worker")

    async def stop(self) -> None:
        self._stop_event.set()
        self.wake()
        if self._main_task:
            await self._main_task
            self._main_task = None

    def wake(self) -> None:
        """Wake the local worker without changing cross-process polling."""
        self._wake_event.set()

    async def _wait_for_wake(self, timeout: float) -> None:  # noqa: ASYNC109 -- local wakeup wait limit implemented by asyncio.wait_for; repository has no trio/anyio cancel scope
        # No local wake is normal; cross-process work is discovered on the polling timeout.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        self._wake_event.clear()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_cycle()
                except Exception:
                    # Errors in any cycle step (lease renewal, finishing, claiming) must not exit main loops: finally would release
                    # leases, leaving no worker in the process and every enqueue reporting worker offline until process restart.
                    # Typical causes are peak-load connection-pool acquisition timeouts; retrying after a heartbeat interval recovers.
                    logger.exception("worker 主循环本轮出错，%.1fs 后重试", self.heartbeat_interval)
                    # Failed cycles may leave running rows: errors after claims but before slot registration leave tasks outside local slots;
                    # repeated renewal errors can let another process take leases then crash. Rescan/settle orphans on the next held lease;
                    # handle_orphans excludes locally occupied tasks. Leave _startup_settled unchanged: rescans during process life
                    # settle task-bound call rows only.
                    self._orphan_handled_once = False
                    await self._wait_for_wake(self.heartbeat_interval)

            await self._wait_inflight_completion()
        finally:
            if self._owns_lease:
                await self.queue.release_worker_lease(name=self.lease_name, owner_id=self.owner_id)
            self._owns_lease = False

    async def _run_cycle(self) -> None:
        """One main-loop cycle: renew leases, finish completed tasks, settle orphans as needed and claim new tasks while holding leases."""
        had_lease = self._owns_lease
        self._owns_lease = await self.queue.acquire_or_renew_worker_lease(
            name=self.lease_name,
            owner_id=self.owner_id,
            ttl_seconds=self.lease_ttl,
        )

        if self._owns_lease and not had_lease:
            logger.info("获得 worker lease (owner=%s)", self.owner_id)
        if had_lease and not self._owns_lease:
            logger.warning("失去 worker lease (owner=%s)", self.owner_id)

        await self._drain_finished_tasks()

        # Track lease transitions: timestamp initial loss; after takeover, distinguish
        # actual owner switches (>= 3× ttl), resetting switches, from renewal flaps (< 3× ttl), retaining them.
        if had_lease and not self._owns_lease and self._lease_lost_monotonic is None:
            self._lease_lost_monotonic = time.monotonic()
        if self._owns_lease and self._lease_lost_monotonic is not None:
            lost_duration = time.monotonic() - self._lease_lost_monotonic
            if lost_duration > self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT:
                logger.info(
                    "lease 丢失 %.1fs（> %d×ttl=%.1fs），认为另一进程曾持过 lease，重扫 orphan",
                    lost_duration,
                    _ORPHAN_RESCAN_LEASE_LOST_MULT,
                    self.lease_ttl * _ORPHAN_RESCAN_LEASE_LOST_MULT,
                )
                self._orphan_handled_once = False
            self._lease_lost_monotonic = None

        # One-shot scan: scan orphans once after acquiring leases; subsequent main-loop cycles do not rescan.
        # Single-lease exclusion prevents concurrent scans by other workers; thresholds above defend cross-process takeover.
        if self._owns_lease and not self._orphan_handled_once:
            await self._recovery.handle_orphans()
            # Initial settlement occurs at startup: taskless pending rows (text calls, endpoint trials) started before construction
            # cannot resume and become terminal too. Rescans after prolonged lease loss occur during process life; taskless rows
            # may still be running locally, so settle task-bound rows only.
            await self._recovery.settle_interrupted_calls(
                taskless_started_before=None if self._startup_settled else self._constructed_at
            )
            self._startup_settled = True
            self._orphan_handled_once = True

        if not self._owns_lease:
            await self._wait_for_wake(self.heartbeat_interval)
            return

        claimed_any = await self._claim_tasks()

        if claimed_any:
            await asyncio.sleep(0.05)
        else:
            await self._wait_for_wake(self.poll_interval)

    def _pool_full_providers(self, media_type: str) -> frozenset[str]:
        """Return provider_id sets full for this cycle's ``media_type`` (blacklists for claim SQL).

        Blacklists originate from **occupied providers** (``SlotTable.occupied_providers``), never all known capacity-table
        providers: only occupied slots can be full. Empty providers already have room,
        so never blacklist them. Unknown-but-occupied providers (startup before first reload / custom providers deleted
        during execution) remain eligible for blacklisting, preventing full-pool claim→requeue churn each cycle.

        Retain ``cap > 0`` guards: ``has_room`` also returns False for ``cap == 0``. Without guards,
        unsupported providers enter blacklists, causing SQL to silently drop their tasks
        rather than using the worker's second-check ``cap == 0`` fail-fast mark_failed path.
        """
        return frozenset(
            pid
            for pid in self._slots.occupied_providers(media_type)
            if (cap := self._capacity.get(pid, media_type)) > 0 and not self._slots.has_room(pid, media_type, cap)
        )

    async def _claim_tasks(self) -> bool:
        """Claim tasks from queue and route to per-provider slots.

        SQL filters full pools with ``pool_full_providers`` blacklists; full-pool tasks remain ``queued``.
        Slot ownership uses queued provider records only, never current-project derivation at claim time. Full-pool claim-cycle costs
        therefore do not depend on queued-task counts. If projects switch providers while queued, execution entries detect drift, rewrite records and
        requeue in place; new providers acquire slots on the next claim. Legacy unrecorded tasks resolve once on claim and persist.
        Text/render providers are fixed and still resolve normally.
        """
        claimed_any = False

        for media_type in self._lanes:
            while True:
                # Recompute full-pool sets every iteration: newly claimed tasks can fill provider pools.
                pool_full = self._pool_full_providers(media_type)
                task = await self.queue.claim_next_task(media_type=media_type, pool_full_providers=pool_full)
                if not task:
                    break

                provider_id, recorded = await self._slot_provider(task, media_type)
                cap = self._capacity.get(provider_id, media_type)

                if cap <= 0:
                    # Providers unsupported for this media type (capacity ≤ 0) fail directly (matching has_room guards).
                    logger.warning(
                        "供应商 %s 不支持 %s 生成，任务 %s 标记失败",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    await self.queue.mark_task_failed(
                        task["task_id"],
                        encode_failure(
                            "provider_unsupported_media",
                            provider_id=provider_id,
                            media_type=media_type,
                        ),
                    )
                    claimed_any = True
                    continue

                if not self._slots.has_room(provider_id, media_type, cap):
                    # Postclaim-derived providers bypassed SQL filters (legacy NULL tasks, etc.) and are full:
                    # requeue for the next cycle (queued_at preserves FIFO order).
                    # Never mark_failed: NULL does not mean invalid tasks; this path must remain retryable.
                    logger.info(
                        "供应商 %s 的 %s 池满，task %s 回队等待下一 cycle",
                        provider_id,
                        media_type,
                        task["task_id"],
                    )
                    # Persist derived providers before requeueing so next-cycle SQL blacklists can filter them,
                    # preventing repeated claim → requeue every cycle. Failed persistence only reduces filtering accuracy.
                    if not recorded:
                        recorded = await self._record_provider(task["task_id"], provider_id)
                    await self._requeue_single_task(task["task_id"])
                    if recorded:
                        # Records now point to full providers; next-cycle SQL excludes them, continuing to runnable tasks from other providers.
                        continue
                    break

                # Dispatch: register INFLIGHT occupancy; register automatically creates buckets.
                claimed_any = True
                self._slots.register(
                    provider_id,
                    media_type,
                    task["task_id"],
                    asyncio.create_task(
                        self._process_task(task, claimed_provider_id=provider_id),
                        name=f"generation-{media_type}-{task['task_id']}",
                    ),
                )
                if media_type in ("text", RENDER_MEDIA_TYPE):
                    break

        return claimed_any

    async def _slot_provider(self, task: dict[str, Any], media_type: str) -> tuple[str, bool]:
        """Task slot ownership: return ``(provider_id, 任务行是否已记录该 provider)``.

        Use recorded providers directly when they support this media type. Legacy unrecorded tasks, or recorded providers
        no longer supporting this media type, resolve current project state once and persist; later claims do not resolve again. Text/render
        providers are fixed, resolving normally without persistence.
        """
        if media_type in ("text", RENDER_MEDIA_TYPE):
            return await self._provider_projection(task), False
        recorded = task.get("provider_id")
        if isinstance(recorded, str) and recorded and self._capacity.get(recorded, media_type) > 0:
            return recorded, True
        provider_id = await self._provider_projection(task)
        return provider_id, await self._record_provider(task["task_id"], provider_id)

    async def _record_provider(self, task_id: str, provider_id: str) -> bool:
        """Best-effort task-row provider persistence, returning whether successful."""
        try:
            await self.queue.persist_execution_provider_id(task_id, provider_id)
        except Exception:
            logger.warning("provider 记录写回失败 task_id=%s provider=%s", task_id, provider_id, exc_info=True)
            return False
        return True

    async def _requeue_single_task(self, task_id: str) -> bool:
        """Put a claimed (running) task back to queued status.

        Full pools normally filter at claim through ``pool_full_providers`` SQL; when postclaim-derived providers
        are full (for example legacy ``provider_id IS NULL`` tasks), requeue
        for the next cycle (never mark_failed: NULL does not mean invalid tasks). Execution-entry provider drift
        reuses the same guarded UPDATE, using its result to determine safe exit from current execution slots.
        Requeue preserves ``queued_at`` and original queue positions.
        """
        try:
            async with safe_session_factory() as session:
                result = await session.execute(
                    update(Task)
                    .where(Task.task_id == task_id, Task.status == "running")
                    .values(
                        status="queued",
                        started_at=None,
                        updated_at=datetime.now(UTC),
                    )
                )
                affected = rowcount(result)
                await session.commit()
            if affected == 0:
                logger.info("回队任务 %s 未命中 running 状态", task_id)
                return False
            logger.debug("回队任务 %s (供应商池已满)", task_id)
            return True
        except Exception:
            logger.warning("回队任务 %s 失败", task_id, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def _drain_finished_tasks(self) -> None:
        for task_id, finished_task in self._slots.drain_finished():
            # Check cancellation/errors synchronously: drain_finished() returns done() tasks only, requiring no awaits.
            # Without awaits there are no suspension points, preventing accidental swallowing of _run_loop's own cancellation.
            if finished_task.cancelled():
                # Subtasks interrupted for process-level reasons: normally _process_task already persisted terminal states and reraised;
                # however, interruption can precede _process_task's try entry (coroutines not yet running), leaving
                # no chance for subtasks to settle terminal states. Drain-side fallback handles this:
                # SQL guards allow only queued / running; terminal rows return 0 without side effects, preventing permanently
                # running tasks repeatedly revived by restart recovery.
                try:
                    await self.queue.mark_task_interrupted(task_id)
                except Exception:
                    logger.warning("drain 兜底落终态失败 task_id=%s", task_id, exc_info=True)
                continue
            try:
                finished_task.result()
            except Exception:
                logger.debug("已处理的任务异常已在 _process_task 中记录")

    async def _wait_inflight_completion(self) -> None:
        # Shutdown: await restart-recovery dispatchers' final subtasks, then every active task.
        await self._recovery.wait_dispatchers()

        active_tasks = self._slots.all_active_tasks()
        if not active_tasks:
            return
        await asyncio.gather(*active_tasks, return_exceptions=True)
        self._slots.clear()

    async def _process_task(self, task: dict[str, Any], *, claimed_provider_id: str) -> None:
        """Run a generation task to a terminal state.

        Running tasks cannot cancel and persist outcomes normally. Coroutines only interrupt for process-level reasons (event-loop teardown,
        shutdown timeouts, etc.), settling cancelled through ``mark_task_interrupted`` to avoid stuck
        running tasks repeatedly revived by restart recovery. All DB writes use ``asyncio.shield``,
        allowing UPDATE completion before propagating interruptions during awaits.

        Execute under task project claims. Project deletion invalidates claims during execution; executors abort before persistence.
        Regardless of executor outcomes, tasks fail with ``project_deleted_during_task``.
        """
        task_id = task["task_id"]
        with claim_task_project(task_id, task.get("project_name")) as claim:
            await self._run_claimed_task(task, claim, claimed_provider_id=claimed_provider_id)

    async def _run_claimed_task(
        self, task: dict[str, Any], claim: TaskProjectClaim, *, claimed_provider_id: str
    ) -> None:
        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        provider_id = claimed_provider_id
        logger.info("开始处理任务 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

        try:
            if task_type in ("video", "reference_video"):
                # At dispatch, write current global polling timeouts into task dicts, read by both task and resume executors.
                task["video_poll_timeout_seconds"] = await read_video_poll_timeout_seconds()
            result = await self._executor(task, claimed_provider_id=provider_id)
        except asyncio.CancelledError:
            # Process-level interruption: user cancellation never reaches running tasks.
            await asyncio.shield(self.queue.mark_task_interrupted(task_id))
            raise
        except DispatchProviderChanged as exc:
            if claim.revoked:
                await fail_task_of_deleted_project(self.queue, task)
                return
            # Execution entries resolve current project providers; if drifted from claimed slots,
            # never submit to new providers while occupying old slots. Refresh advisory columns and requeue so next cycles
            # validate new-slot capacities before execution. No provider calls yet, so no duplicate charges.
            try:
                await asyncio.shield(self.queue.persist_execution_provider_id(task_id, exc.actual_provider_id))
            except Exception:
                logger.warning(
                    "执行 provider 漂移后的投影刷新失败 task_id=%s provider=%s",
                    task_id,
                    exc.actual_provider_id,
                    exc_info=True,
                )
            requeued = await asyncio.shield(self._requeue_single_task(task_id))
            if not requeued:
                logger.error(
                    "任务 %s 执行 provider 从 %s 变为 %s，但回队失败",
                    task_id,
                    exc.claimed_provider_id,
                    exc.actual_provider_id,
                )
                await asyncio.shield(
                    self.queue.mark_task_failed(
                        task_id,
                        encode_failure(
                            "dispatch_provider_requeue_failed",
                            claimed_provider_id=exc.claimed_provider_id,
                            actual_provider_id=exc.actual_provider_id,
                        ),
                    )
                )
                return
            logger.info(
                "任务 %s 执行 provider 从 %s 变为 %s，已回队等待新槽",
                task_id,
                exc.claimed_provider_id,
                exc.actual_provider_id,
            )
            return
        except Exception as exc:
            if claim.revoked:
                await fail_task_of_deleted_project(self.queue, task)
                return
            logger.exception("任务失败 %s (type=%s, provider=%s)", task_id, task_type, provider_id)
            await asyncio.shield(self.queue.mark_task_failed(task_id, encode_task_failure_message(exc)))
            return

        if claim.revoked:
            await fail_task_of_deleted_project(self.queue, task)
            return
        try:
            await asyncio.shield(self.queue.mark_task_succeeded(task_id, result))
        except asyncio.CancelledError:
            # Process-level interruption during mark_succeeded: shield lets inner finish; if inner already persisted succeeded,
            # fallback hits terminal rows, returning 0 without side effects.
            await asyncio.shield(self.queue.mark_task_interrupted(task_id))
            raise
        except Exception:
            # mark_succeeded itself raises (DB timeout / OperationalError): upstream _drain_finished_tasks
            # only swallows exceptions into debug logs, losing stack traces; explicitly logger.exception here to retain evidence.
            logger.exception("标记任务成功失败 %s", task_id)
            raise
        logger.info("任务完成 %s (type=%s, provider=%s)", task_id, task_type, provider_id)

    # ------------------------------------------------------------------
    # Restart recovery
    # ------------------------------------------------------------------

    async def _settle_interrupted_calls_via_ledger(self, *, taskless_started_before: datetime | None) -> int:
        # Share queue session factories: task rows and their call rows must persist in the same database.
        ledger = Ledger(session_factory=self.queue.session_factory)
        return await ledger.settle_interrupted_calls(taskless_started_before=taskless_started_before)

    async def read_video_poll_timeout_seconds(self) -> int:
        """Resolve global polling timeouts before dispatch.

        Callers must read before task-state transitions: committed states have no rollback point, and later dispatcher errors leave tasks permanently
        running without owners (like ``provider_id``, an eligibility condition; see ``retry_artifact_download``).
        """
        return await read_video_poll_timeout_seconds()

    async def retry_artifact_download(self, task: dict[str, Any], *, poll_timeout_seconds: int) -> None:
        """Dispatch one download recovery against original provider jobs, never resubmit provider tasks."""
        await self._recovery.retry_artifact_download(task, poll_timeout_seconds=poll_timeout_seconds)
