"""
Video project management WebUI - main FastAPI application

Startup:
    cd ArcReel
    uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

Use --reload-dir to restrict watched directories; otherwise watchfiles scans
hundreds of thousands of files in node_modules / .venv / .git / .worktrees, consuming 50%+ of one CPU core.

Do not use --reload on native Windows: enabling reload makes uvicorn switch to
SelectorEventLoop, which lacks subprocess support, preventing the embedded Agent from starting Claude Code. Use:
    uv run uvicorn server.app:app --port 1241

Alternatively use ``uv run python -m server.app``: bind from LISTEN_HOST / LISTEN_PORT,
automatically disabling reload on Windows; other platforms enable reload and watch only server and lib.
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.datastructures import Headers
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipResponder, IdentityResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lib import PROJECT_ROOT
from lib.agent.agent_session_store import default_sdk_config_dir, session_store_enabled
from lib.agent.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent.agent_session_store.key_alignment import align_session_keys
from lib.agent.agent_session_store.store import DbSessionStore
from lib.asset_library.ingest import start_asset_ingest, stop_asset_ingest
from lib.catalog.official_sync import start_official_sync, stop_official_sync
from lib.config.env_keys import PROVIDER_SECRET_KEYS
from lib.db import async_session_factory, close_db, init_db
from lib.generation.generation_worker import GenerationWorker
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from lib.infra.env_init import warn_if_env_diverges_from_file
from lib.infra.ffmpeg import log_ffmpeg_status
from lib.infra.httpx_shared import shutdown_http_client, startup_http_client
from lib.infra.logging_config import (
    attach_file_handler,
    migrate_legacy_log_dir,
    setup_logging,
    warn_if_log_dir_env_set,
)
from lib.infra.loop_stall_probe import start_loop_stall_probe, stop_loop_stall_probe
from lib.infra.path_safety import try_safe_join
from lib.material.maintenance import start_material_maintenance
from lib.project.project_migrations import cleanup_stale_backups, run_project_migrations
from lib.script.source_loader.migration import migrate_project_source_encoding, migrate_project_source_encoding_once
from server.auth import ensure_auth_password, ensure_token_secret, get_current_user, warn_if_auth_disabled
from server.cors_config import resolve_cors_policy
from server.dependencies import require_project_migration_ok, require_valid_project_name
from server.error_handlers import register_error_handlers
from server.remote_mcp import mount_remote_mcp, remote_mcp_host
from server.routers import (
    ad_script,
    agent_config,
    agent_memory,
    api_keys,
    asset_library,
    asset_sheets,
    assets,
    assistant,
    catalog,
    characters,
    cost_estimation,
    custom_endpoints,
    custom_providers,
    derivative_uploads,
    edit_timelines,
    end_frames,
    episode_drafts,
    episode_management,
    episode_planning,
    episodes_view,
    file_view,
    files,
    generate,
    grids,
    market,
    market_submissions,
    object_storage,
    official_service,
    onboarding,
    presentations,
    products,
    project_events,
    project_migration,
    project_trash,
    projects,
    prompt_authoring,
    prompt_templates,
    props,
    provider_asset_links,
    provider_models,
    providers,
    reference_videos,
    scenes,
    script_plan,
    script_review,
    shot_uploads,
    storyboard_batches,
    system,
    system_config,
    tasks,
    usage,
    versions,
)
from server.routers import auth as auth_router
from server.services.project.episode_id_records import recorded_episode_ids_on
from server.services.project.project_events import ProjectEventService
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task


def assert_no_provider_secrets_in_environ() -> None:
    """The parent process must hold no provider secrets; fail fast on violations.

    Sandboxed Bash subprocesses inherit parent environment through fork; the parent must move all provider secrets
    to the DB, injecting them explicitly into subprocesses through SDK options.env.
    """
    leaked = sorted(k for k in PROVIDER_SECRET_KEYS if os.environ.get(k))
    if leaked:
        raise RuntimeError(
            f"SECURITY: 父进程 os.environ 含 provider 密钥: {leaked}. "
            "请到 WebUI 系统配置页填写，并从 env / .env 中移除对应条目。"
        )


_APPARMOR_USERNS_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
_UNPRIV_USERNS_SYSCTL = Path("/proc/sys/kernel/unprivileged_userns_clone")
_MAX_USER_NS_SYSCTL = Path("/proc/sys/user/max_user_namespaces")


def _read_sysctl(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _diagnose_bwrap_failure() -> str:
    """Give precise bwrap remediation based on host sysctl state.

    Host procfs is shared, so host sysctl values are readable in containers too; these
    diagnostics work inside and outside Docker. Priority: Ubuntu 24.04 AppArmor restrictions >
    traditional unprivileged_userns_clone > max_user_namespaces > fallback container configuration.
    """
    parts: list[str] = []

    apparmor_userns = _read_sysctl(_APPARMOR_USERNS_SYSCTL)
    if apparmor_userns == "1":
        parts.append(
            "Detected Ubuntu 24.04+ AppArmor restriction (root cause):\n"
            "  /proc/sys/kernel/apparmor_restrict_unprivileged_userns = 1\n"
            "  Blocks ALL unprivileged user namespaces. `apparmor:unconfined`\n"
            "  in docker compose does NOT bypass this — it is a global LSM\n"
            "  switch, not a per-process profile.\n"
            "  Fix on HOST (not inside the container):\n"
            "    sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0\n"
            '    echo "kernel.apparmor_restrict_unprivileged_userns=0" '
            "| sudo tee /etc/sysctl.d/60-arcreel-bwrap.conf"
        )

    userns_clone = _read_sysctl(_UNPRIV_USERNS_SYSCTL)
    if userns_clone == "0":
        parts.append(
            "Unprivileged user namespaces disabled on host:\n"
            "  /proc/sys/kernel/unprivileged_userns_clone = 0\n"
            "  Fix on HOST: sudo sysctl -w kernel.unprivileged_userns_clone=1"
        )

    max_userns = _read_sysctl(_MAX_USER_NS_SYSCTL)
    if max_userns == "0":
        parts.append(
            "User namespace count limit set to 0 on host:\n"
            "  /proc/sys/user/max_user_namespaces = 0\n"
            "  Fix on HOST: sudo sysctl -w user.max_user_namespaces=15000"
        )

    if not parts:
        parts.append(
            "Container likely missing security relaxation. docker compose:\n"
            "  security_opt:\n"
            "    - seccomp:unconfined\n"
            "    - apparmor:unconfined\n"
            "  cap_add:\n"
            "    - NET_ADMIN"
        )

    return "\n".join(parts)


def check_sandbox_available(
    *,
    platform_system: Callable[[], str] | None = None,
    executable_which: Callable[[str], str | None] | None = None,
    subprocess_run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    """Check sandbox-tool availability at startup.

    ``True`` means sandboxing is available and required; ``False`` means the SDK does not support
    the current platform (Windows only; official platform constraints at
    https://code.claude.com/docs/en/sandboxing#platform-and-tool-compatibility），server
    can still start with sandboxing disabled; Bash falls back to the
    ``AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST`` code whitelist.
    Missing macOS / Linux tools still fail hard (no downgrade on supported platforms).
    """
    system = (platform_system or platform.system)()
    which = executable_which or shutil.which
    run = subprocess_run or subprocess.run
    if system == "Darwin":
        if which("sandbox-exec") is None:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on macOS\n"
                "  sandbox-exec: not found in PATH (should be system-installed)\n"
                "Required for ArcReel agent runtime."
            )
        return True
    if system == "Linux":
        # Linux dependencies: https://code.claude.com/docs/en/sandboxing#set-up-linux-and-wsl2; install both
        # (bwrap for process/file isolation, socat for network proxy forwarding).
        missing = [name for name in ("bwrap", "socat") if which(name) is None]
        if missing:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on linux\n"
                f"  missing in PATH: {', '.join(missing)}\n"
                "Required for ArcReel agent runtime. Install:\n"
                "  Ubuntu/Debian: sudo apt install bubblewrap socat\n"
                "  Fedora:        sudo dnf install bubblewrap socat\n"
                "  Arch:          sudo pacman -S bubblewrap socat"
            )
        # Installed bwrap may still fail to run. Two common failures:
        # 1) User namespace creation denied by seccomp / apparmor / sysctl
        #    → "No permissions to create new namespace"
        # 2) Loopback setup denied in a new net namespace: container lacks CAP_NET_ADMIN
        #    → "loopback: Failed RTM_NEWADDR: Operation not permitted"
        # Probe with unshare arguments close to SDK usage and reject at startup,
        # avoiding mysterious failure on the Agent's first Bash call.
        probe_cmd = [
            "bwrap",
            "--unshare-user",
            "--unshare-net",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "/bin/true",
        ]
        try:
            probe = run(probe_cmd, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "SANDBOX_BWRAP_BROKEN on Linux\n"
                f"  bwrap probe failed to execute: {exc}\n"
                "Required for ArcReel agent runtime."
            ) from exc
        if probe.returncode != 0:
            stderr = probe.stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
            raise RuntimeError(
                "SANDBOX_BWRAP_BROKEN on Linux\n"
                f"  bwrap installed but cannot run: {stderr}\n"
                f"{_diagnose_bwrap_failure()}"
            )
        return True
    logger.warning(
        "SANDBOX_UNSUPPORTED on %s — server 启动 sandbox=disabled，Bash 工具回退到代码白名单"
        "（python .claude/skills/.../scripts/*.py / ffmpeg / ffprobe）。"
        "生产部署推荐 macOS / Linux / Docker；Windows 用户建议使用 WSL2。",
        system,
    )
    return False


_DOCKERENV_PATH = Path("/.dockerenv")
_CGROUP_PATH = Path("/proc/1/cgroup")


def detect_docker_environment(
    *,
    dockerenv_path: Path | None = None,
    cgroup_path: Path | None = None,
) -> bool:
    """Detect Docker / Podman container execution once at startup.

    Used to decide whether to enable ``SandboxSettings.enableWeakerNestedSandbox``.
    """
    docker_marker = dockerenv_path or _DOCKERENV_PATH
    cgroup = cgroup_path or _CGROUP_PATH
    if docker_marker.exists():
        return True
    try:
        content = cgroup.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in content or "podman" in content


# Initialize logging with only a stream handler during module import.
# Defer the file handler to lifespan, after migrate_legacy_log_dir()
# moves old source-directory logs into the data root; otherwise the handler creates same-named destination files
# that block migration. This also avoids real-filesystem side effects when pytest collection imports server.app
# before startup.
setup_logging(file=False)
logger = logging.getLogger(__name__)


def _log_profile_sync_outcome(stats: dict, *, log: logging.Logger = logger) -> None:
    """Choose info versus warning from ``sync_all_agent_profiles`` stats.

    ``stats["aborted"]`` is bool, a subclass of int; a simple
    ``isinstance(v, int) and v > 0`` would treat ``aborted=True`` as a positive materialization-completed
    signal, opposite to reality. Handle abort separately first, then count only genuine integers using ``type(v) is int`` (strict
    type equality).
    """
    if stats.get("aborted"):
        log.warning("agent_runtime profile 物化已中止: %s", stats)
        return
    if any(type(v) is int and v > 0 for v in stats.values()):
        log.info("agent_runtime profile 物化完成: %s", stats)


async def _migrate_source_encoding_on_startup(
    projects_dir: Path,
    *,
    migrate_source_encoding: Callable[[Path], Any] | None = None,
) -> dict[str, dict]:
    """Run idempotent encoding migration per project; catch and log failures without blocking startup."""
    summary: dict[str, dict] = {}
    migrate = migrate_source_encoding or migrate_project_source_encoding
    if not projects_dir.exists():  # noqa: ASYNC240 -- One-time startup existence check on local metadata
        return summary

    for project_dir in list_project_dirs(projects_dir):
        summary[project_dir.name] = await asyncio.to_thread(
            migrate_project_source_encoding_once, project_dir, migrate=migrate
        )
    return summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # Startup
    # Security checks: sanitize parent env first, then check sandbox tools, then detect Docker
    assert_no_provider_secrets_in_environ()
    sandbox_enabled = check_sandbox_available()
    # detect_docker_environment matters only on sandbox-supported platforms (Linux path probes);
    # skip Windows fallback to avoid pointless filesystem calls.
    is_docker = detect_docker_environment() if sandbox_enabled else False
    logger.info("Sandbox runtime: enabled=%s docker=%s", sandbox_enabled, is_docker)

    app.state.in_docker = is_docker
    app.state.sandbox_enabled = sandbox_enabled

    # Probe bundled ffmpeg once and cache the result in-process; unavailability does not block startup.
    await asyncio.to_thread(log_ffmpeg_status)

    # Persistent logs: move old source-directory logs into the data root before attaching the file handler.
    # Order matters: the handler creates arcreel.log at the new location; attaching early leaves conflicting old logs unmigrated.
    await asyncio.to_thread(migrate_legacy_log_dir)
    attach_file_handler()
    warn_if_log_dir_env_set()
    warn_if_env_diverges_from_file()

    ensure_auth_password()
    warn_if_auth_disabled()

    # Run Alembic migrations (auto-creates tables on first start)
    await init_db()

    # Data-root layout migration follows file logging (to log migration) and Alembic (steps modify the DB),
    # before all project traversal: those steps expect the current layout, not a partially migrated root. Failure aborts startup.
    layout = DataRootLayout.current()
    await migrate_data_root_layout(
        layout.root,
        session_factory=async_session_factory,
        sdk_config_dir=default_sdk_config_dir(),
    )
    # Signing keys may live in data-root runtime/; resolve after layout migration because an old-layout project named runtime
    # moves into projects/, taking any prematurely written signing key into the project with it.
    await asyncio.to_thread(ensure_token_secret)

    # Source encoding migration (idempotent; failures do not block startup) precedes schema migration: normalize sources to UTF-8 first
    # so later UTF-8 source readers (episode planning and derived-file reconciliation) share consistent input.
    source_migration_summary = await _migrate_source_encoding_on_startup(layout.projects_dir)
    migrated_total = sum(len(s.get("migrated") or []) for s in source_migration_summary.values())
    failed_total = sum(len(s.get("failed") or []) for s in source_migration_summary.values())
    if migrated_total or failed_total:
        logger.info(
            "源文件编码迁移完成：migrated=%d failed=%d projects=%d",
            migrated_total,
            failed_total,
            len(source_migration_summary),
        )

    # Run any pending project.json schema migrations (file-based).
    # Both calls are synchronous filesystem walks — offload to a worker thread
    # so they don't block the event loop during uvicorn startup.
    migration_summary = await asyncio.to_thread(
        run_project_migrations,
        layout.projects_dir,
        recorded_episode_ids=recorded_episode_ids_on(asyncio.get_running_loop()),
    )
    if migration_summary.migrated or migration_summary.failed:
        logger.info(
            "Project migrations: migrated=%s skipped=%d failed=%s",
            migration_summary.migrated,
            len(migration_summary.skipped),
            migration_summary.failed,
        )
    await asyncio.to_thread(cleanup_stale_backups, layout.projects_dir, 7)

    # Reconcile session storage keys (idempotent; nonblocking failures): after data-root absolute-path changes, move Agent context to keys derived from current project directories.
    # Follow data-root layout migration, which moves old keys to projects/ directories; recompute here using the current layout.
    # Precede local transcript import, which lists sessions and checks prior imports by current keys; unreconciled keys cause duplicate import under new keys,
    # leaving the same session under two keys, after which reconciliation no longer changes it.
    try:
        await align_session_keys(
            async_session_factory,
            projects_dir=layout.projects_dir,
            sdk_config_dir=default_sdk_config_dir(),
            store_enabled=session_store_enabled(),
        )
    except Exception:
        logger.exception("会话存储键对齐失败（不阻塞启动）")

    # Migrate any pre-existing local SDK jsonl transcripts into the DbSessionStore.
    # Runs once (marker-gated); failures are non-fatal and logged.
    if session_store_enabled():
        try:
            store = DbSessionStore(async_session_factory)
            await migrate_local_transcripts_to_store(store, data_root=layout.root)
        except Exception:
            logger.exception("session-store transcript migration failed (non-fatal)")

    # Migrate legacy .system_config.json → DB (no-op if file doesn't exist or already migrated)
    try:
        from lib.config.migration import migrate_json_to_db

        async with async_session_factory() as session:
            await migrate_json_to_db(session, layout.root)
    except Exception as exc:
        logger.warning("JSON→DB config migration failed (non-fatal): %s", exc)

    # Old task-level text backend keys to tier keys (docs/adr/0051). Follow JSON-to-DB migration
    # so same-named old JSON keys stored through catch-all are included.
    try:
        from lib.config.migration import migrate_text_tier_settings

        async with async_session_factory() as session:
            await migrate_text_tier_settings(session)
    except Exception as exc:
        logger.warning("text tier settings migration failed (non-fatal): %s", exc)

    # Seed official market sources: insert if absent, synchronize changed address constants; do not fetch
    try:
        from lib.market.sources import seed_official_source

        async with async_session_factory() as session:
            await seed_official_source(session)
    except Exception as exc:
        logger.warning("official market source seed failed (non-fatal): %s", exc)

    # Load officially synchronized catalog data into queries; on read failure, use the factory snapshot until next sync
    try:
        from lib.catalog.official_sync import load_synced_data

        async with async_session_factory() as session:
            await load_synced_data(session)
    except Exception:
        logger.exception("loading synced catalog data failed (non-fatal)")

    # Merge user-modified entry copies into queries; on read failure, those entries temporarily use official data; others are unaffected
    try:
        from lib.catalog.user_entries import reload_user_entries

        async with async_session_factory() as session:
            await reload_user_entries(session)
    except Exception:
        logger.exception("loading user-modified catalog entries failed (non-fatal)")

    # Merge user-added built-in-provider models into queries; on read failure, those models are temporarily unavailable; others are unaffected
    try:
        from server.routers.provider_models import reload_user_added_models

        async with async_session_factory() as session:
            await reload_user_added_models(session)
    except Exception:
        logger.exception("loading user-added built-in provider models failed (non-fatal)")

    # Materialize agent_runtime_profile in existing projects (file I/O in worker threads)
    from lib.project.project_manager import get_project_manager

    _pm = get_project_manager()
    _profile_sync_stats = await asyncio.to_thread(_pm.sync_all_agent_profiles)
    _log_profile_sync_outcome(_profile_sync_stats)

    # Start the shared httpx client for version checks and other external APIs
    await startup_http_client()

    # Initialize async services
    await assistant.assistant_service.startup(in_docker=is_docker, sandbox_enabled=sandbox_enabled)
    assistant.assistant_service.session_manager.start_patrol()

    logger.info("启动 GenerationWorker...")
    worker = create_generation_worker()
    app.state.generation_worker = worker
    await worker.start()
    logger.info("GenerationWorker 已启动")

    logger.info("启动 ProjectEventService...")
    project_event_service = ProjectEventService(PROJECT_ROOT, data_root=layout.root)
    app.state.project_event_service = project_event_service
    await project_event_service.start()
    logger.info("ProjectEventService 已启动")
    assistant.assistant_service.session_manager.set_autonomous_turn_listener(
        project_event_service.publish_assistant_session_resumed
    )

    # Start the stall probe after startup steps: synchronous startup migrations are not runtime stalls; avoid false positives.
    stall_probe = start_loop_stall_probe()
    # Official model-catalog sync: check at startup, then daily; cancel on application shutdown
    catalog_sync = start_official_sync(async_session_factory)
    # Expired project-trash and deleted-project cleanup: once at startup, then periodically; cancel on shutdown
    from server.services.project.trash_expiry import start_trash_expiry, stop_trash_expiry

    trash_expiry = start_trash_expiry(layout)
    # Provider-asset ingestion: recover interrupted submissions at startup, then advance staging uploads, status queries, and staging-copy cleanup; cancel on shutdown
    asset_ingest = start_asset_ingest(async_session_factory)
    # Remote assets: settle interrupted formal submissions at startup, then retry pending formal-object deletions, recompute references, and clean idle local workfiles (projects/global asset library); cancel on shutdown
    material_maintenance = start_material_maintenance(
        async_session_factory, layout.projects_dir, layout.deleted_projects_dir, data_root=layout.root
    )
    try:
        async with remote_mcp_host.run():
            yield
    finally:
        await material_maintenance.stop()
        await stop_asset_ingest(asset_ingest)
        await stop_trash_expiry(trash_expiry)
        await stop_official_sync(catalog_sync)
        # Also stop the probe on runtime exceptions or canceled shutdown to avoid classifying shutdown work as stalls.
        await stop_loop_stall_probe(stall_probe)

    # Shutdown
    assistant.assistant_service.session_manager.set_autonomous_turn_listener(None)
    project_event_service = getattr(app.state, "project_event_service", None)
    if project_event_service:
        logger.info("正在停止 ProjectEventService...")
        await project_event_service.shutdown()
        logger.info("ProjectEventService 已停止")
    worker = getattr(app.state, "generation_worker", None)
    if worker:
        logger.info("正在停止 GenerationWorker...")
        # Shutdown does not cancel running tasks: worker.stop() waits for them before leaving the main loop.
        await worker.stop()
        logger.info("GenerationWorker 已停止")
    # Connection-test runs cannot resume; event-loop loss would leave ledger rows pending forever. Settle via cancellation before shutdown.
    from lib.custom_provider.endpoint_test import shutdown_trial_runs

    await shutdown_trial_runs()
    await shutdown_http_client()
    await close_db()


# Create the FastAPI application
app = FastAPI(
    title="视频项目管理 WebUI",
    description="Web management interface for the AI video-generation workspace",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_valid_project_name)],
)

# Environment-driven CORS (parsing in server/cors_config.py); remote MCP mounts share the whitelist.
# Resolve before register_error_handlers(app): unexpected-exception 500 responses use
# ServerErrorMiddleware and bypass CORSMiddleware (see
# server/error_handlers.py::_cors_headers_for), so handlers need this configuration to attach CORS headers manually.
_allow_origins, _allow_credentials = resolve_cors_policy()

# Application exception handlers: one exception-to-status-to-detail mapping point (server/error_handlers.py)
register_error_handlers(app, cors_allow_origins=_allow_origins, cors_allow_credentials=_allow_credentials)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_listen_addr() -> tuple[str, int]:
    """Resolve ``LISTEN_HOST`` / ``LISTEN_PORT`` for the ``__main__`` block and tests.

    **Scope**: applies only when executed directly with ``python server/app.py`` (through ``__main__`` below).
    With a standard ASGI CLI such as ``uvicorn server.app:app``, the bind address
    comes from the uvicorn process's own ``--host`` / ``--port`` arguments; this function does not participate because
    ASGI app module imports cannot change uvicorn's binding retroactively. Docker, systemd, and similar
    deployments must explicitly pass host/port as uvicorn CLI arguments.

    Truthy defaults (``or``) handle accidentally empty ``.env`` values such as ``LISTEN_PORT=``.
    """
    host = os.environ.get("LISTEN_HOST") or "0.0.0.0"
    port = int(os.environ.get("LISTEN_PORT") or "1241")
    return host, port


# The frontend polls these task-status APIs every 3s; steady-state successes would drown out real errors/slow requests,
# so demote fast 2xx responses to DEBUG while retaining INFO for exceptional/slow responses.
_QUIET_POLL_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks/stats"),
    }
)
_QUIET_SLOW_THRESHOLD_MS = 500.0


@app.middleware("http")
async def request_logging_middleware(
    request: Request, call_next, *, quiet_slow_threshold_ms: float = _QUIET_SLOW_THRESHOLD_MS
):
    start = time.perf_counter()
    path = request.url.path
    _skip_log = path.startswith("/assets") or path == "/health"
    try:
        response: Response = await call_next(request)
    except Exception:
        if not _skip_log:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "%s %s 500 %.0fms (unhandled)",
                request.method,
                path,
                elapsed_ms,
            )
        raise
    if not _skip_log:
        elapsed_ms = (time.perf_counter() - start) * 1000
        is_quiet = (
            (request.method, path) in _QUIET_POLL_ENDPOINTS
            and response.status_code < 400
            and elapsed_ms < quiet_slow_threshold_ms
        )
        log = logger.debug if is_quiet else logger.info
        log(
            "%s %s %d %.0fms",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
    return response


# Register API routes
#
# This block is the sole authentication source of truth: routers with dependencies require Bearer tokens;
# CurrentUser in signatures only means handlers use the user object; it does not authorize requests.
# The two groups without dependencies are explained below.
app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["项目管理"])
app.include_router(characters.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["角色管理"])
app.include_router(scenes.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["场景管理"])
app.include_router(props.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["道具管理"])
app.include_router(products.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["商品管理"])
app.include_router(presentations.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["成片演示"])
app.include_router(
    edit_timelines.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["剪辑时间线"]
)
app.include_router(files.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["文件管理"])
app.include_router(
    file_view.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["文件视图"],
)
app.include_router(
    project_trash.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["项目回收站"],
)
app.include_router(episodes_view.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["分集视图"])
app.include_router(
    episode_management.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["集管理"]
)
app.include_router(
    generate.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["生成"],
)
app.include_router(
    storyboard_batches.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["生成"],
)
app.include_router(
    asset_sheets.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["资产图"],
)
app.include_router(
    script_review.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["内容确认"],
)
app.include_router(
    episode_drafts.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["内容确认"],
)
app.include_router(
    prompt_authoring.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["提示词编写"],
)
app.include_router(
    episode_planning.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["分集规划"],
)
app.include_router(
    script_plan.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["脚本规划"],
)
app.include_router(
    ad_script.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["广告/短片脚本"],
)
app.include_router(
    shot_uploads.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["分镜上传"],
)
app.include_router(
    derivative_uploads.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["衍生资产图上传"],
)
app.include_router(
    end_frames.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["分镜尾帧"],
)
app.include_router(
    project_migration.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["项目数据升级"]
)
app.include_router(versions.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["版本管理"])
app.include_router(usage.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["费用统计"])
app.include_router(auth_router.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["认证"])
app.include_router(
    assistant.router,
    prefix="/api/v1/projects/{project_name}/assistant",
    dependencies=[Depends(get_current_user)],
    tags=["Agent 会话"],
)
app.include_router(
    project_events.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["项目变更流"]
)
app.include_router(tasks.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["任务队列"])
app.include_router(providers.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["供应商管理"])
app.include_router(
    provider_models.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["供应商管理"]
)
app.include_router(catalog.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["模型目录"])
app.include_router(object_storage.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["对象存储"])
app.include_router(
    asset_library.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["供应商素材库"]
)
app.include_router(
    provider_asset_links.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["供应商素材库"]
)
app.include_router(system_config.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["系统配置"])
app.include_router(system.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["系统"])
app.include_router(api_keys.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["API Key 管理"])
app.include_router(agent_config.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["Agent 配置"])
app.include_router(
    custom_providers.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["自定义供应商"]
)
app.include_router(
    custom_endpoints.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["自定义调用端点"]
)
app.include_router(market.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["市场"])
app.include_router(market_submissions.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["市场"])
app.include_router(
    official_service.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["官方服务"]
)
app.include_router(
    cost_estimation.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["费用估算"]
)
app.include_router(
    grids.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["多宫格分镜图"],
)
app.include_router(
    reference_videos.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["参考生视频"],
)
app.include_router(assets.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["全局资产库"])
app.include_router(
    agent_memory.user_router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["Agent 记忆"]
)
app.include_router(
    agent_memory.project_router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user), Depends(require_project_migration_ok)],
    tags=["Agent 记忆"],
)
app.include_router(onboarding.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["首次使用引导"])
app.include_router(
    prompt_templates.router, prefix="/api/v1", dependencies=[Depends(get_current_user)], tags=["提示词模版"]
)

# Public endpoints: anonymous access. Login is required to obtain tokens; static media load through <img src> / <video src>.
app.include_router(auth_router.public_router, prefix="/api/v1", tags=["认证"])
app.include_router(files.public_router, prefix="/api/v1", tags=["文件管理"])

# Self-authenticating endpoints: native browser download navigation cannot carry Authorization headers,
# so verify_download_token inside endpoints checks short-lived download tokens (docs/adr/0071).
app.include_router(projects.self_auth_router, prefix="/api/v1", tags=["项目管理"])
app.include_router(edit_timelines.self_auth_router, prefix="/api/v1", tags=["剪辑时间线"])


mount_remote_mcp(app, remote_mcp_host)


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker(executor=execute_generation_task, resume_executor=execute_resume_video_task)


@app.get("/health")
async def health_check():
    """Health check; commit is the SHA injected at image build time, or null when running from source."""
    return {
        "status": "ok",
        "message": "视频项目管理 WebUI 运行正常",
        "commit": os.environ.get("ARCREEL_BUILD_COMMIT") or None,
    }


@app.get("/agent-installation-guide.md", include_in_schema=False)
async def serve_agent_installation_guide(request: Request) -> Response:
    """Render Agent installation instructions dynamically, replacing {{BASE_URL}} with the actual service URL (no authentication required)."""
    from fastapi.responses import PlainTextResponse

    template_path = PROJECT_ROOT / "public" / "agent-installation-guide.md"

    def _read() -> tuple[bool, str]:
        if not template_path.exists():
            return False, ""
        return True, template_path.read_text(encoding="utf-8")

    exists, template = await asyncio.to_thread(_read)
    if not exists:
        return PlainTextResponse("Agent 安装指引不存在", status_code=404)

    # Infer base URL from requests; trust only x-forwarded-proto (standard reverse-proxy header),
    # using the actual connection target for host, never user-spoofable x-forwarded-host.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.url.netloc
    base_url = f"{scheme}://{host}"

    content = template.replace("{{BASE_URL}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


#: Content-hashed resource directory in frontend builds (Vite default assetsDir). Names change with content and allow permanent caching;
#: public/ files at dist root (favicon, style-thumbnails, etc.) lack hashes and are excluded.
_HASHED_FRONTEND_ASSETS_PREFIX = "/assets/"
_SPA_SHELL_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"
_HASHED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class FrontendCacheHeadersMiddleware:
    """Cache headers for frontend build artifacts.

    - Forbid browser caching of SPA entry HTML shells, covering the shared text/html response characteristics of spa_deep_link and app.frontend native fallback
      paths. Otherwise, after redeployment browsers may reuse old shells that load deleted
      old hashed assets, causing blank screens. Judge content-type rather than routes to cover entries such as "/" and "/login"
      that use native fallback.
    - Mark hashed ``/assets/`` resources immutable (including 304 revalidation responses); do not cache failures such as 404.

    Use pure ASGI rather than BaseHTTPMiddleware: this global middleware affects all requests, and
    BaseHTTPMiddleware's anyio TaskGroup + contextvars copying adds overhead to each request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_hashed_asset = scope["path"].startswith(_HASHED_FRONTEND_ASSETS_PREFIX)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if headers.get("content-type", "").lower().startswith("text/html"):
                    headers["Cache-Control"] = _SPA_SHELL_CACHE_CONTROL
                elif is_hashed_asset and message["status"] in (200, 206, 304):
                    headers["Cache-Control"] = _HASHED_ASSET_CACHE_CONTROL
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(FrontendCacheHeadersMiddleware)


class ResponseCompressionMiddleware:
    """Gzip compression for textual responses such as JSON / JS / CSS / HTML.

    Retain Starlette GZip's default exclusions (text/event-stream and already compressed
    image/audio/video types); pass 206 partial responses unchanged. Stream with per-chunk Z_SYNC_FLUSH without batching. Starlette (>= 1.8) immediately releases headers for excluded types,
    allowing event streams to receive headers before the first event. Use zlib default compresslevel 6:
    JSON/JS size savings nearly match level 9 with much lower CPU cost.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        responder: ASGIApp
        if "gzip" in Headers(scope=scope).get("accept-encoding", ""):
            responder = GZipResponder(self.app, minimum_size=500, compresslevel=6)
        else:
            responder = IdentityResponder(self.app, minimum_size=500)
        await responder(scope, receive, send)


# Outermost: frontend caching rewrites headers and compression preserves content-type; their order has no effect
app.add_middleware(ResponseCompressionMiddleware)


# Frontend builds: SPA static serving. Fallback applies only to GET/HEAD; writes to page paths no longer return pages.
# Mount only when index.html exists, not merely the directory: app.frontend validates fallback files at startup,
# so incomplete builds raise RuntimeError and break the entire application, including every API
frontend_dist_dir = PROJECT_ROOT / "frontend" / "dist"

if (frontend_dist_dir / "index.html").is_file():

    def _spa_deep_link_response(rest: str) -> FileResponse:
        # SPA deep links may end in extensions (such as /app/projects/x/source/chapter1.txt);
        # app.frontend fallback treats them as static requests and returns 404; explicitly fall back to the SPA shell here.
        # Registered before app.frontend, this route receives all /app/ requests first; if the build contains
        # real static files under dist/app/... (URL mapping matches app.frontend,
        # the same relative path under dist root), serve them first to avoid unconditional masking.
        # rest is a user-controlled URL segment: boundary violations fall back to the SPA shell, never exposing files outside dist
        candidate = try_safe_join(frontend_dist_dir / "app", rest, require_file=True)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(frontend_dist_dir / "index.html")

    @app.get("/app/{_rest:path}", include_in_schema=False)
    async def spa_deep_link(_rest: str) -> FileResponse:
        # Static probing and FileResponse construction (initial guess_type loads system MIME tables) access disk; offload both to a thread.
        return await asyncio.to_thread(_spa_deep_link_response, _rest)

    app.frontend("/", directory=frontend_dist_dir, fallback="index.html")
else:
    logger.warning("frontend/dist/index.html 不存在，跳过前端页面挂载（API 不受影响）")


def _main_uvicorn_args(platform_name: str = sys.platform) -> tuple[str | FastAPI, dict[str, Any]]:
    """Application and arguments passed by ``__main__`` to ``uvicorn.run``.

    Reload accepts only import strings. On Windows, enabling uvicorn reload selects
    SelectorEventLoop without subprocess support, preventing embedded Agents from starting Claude Code CLI; therefore disable reload on Windows.
    """
    host, port = _resolve_listen_addr()
    if platform_name == "win32":
        return app, {"host": host, "port": port, "reload": False}
    return "server.app:app", {"host": host, "port": port, "reload": True, "reload_dirs": ["server", "lib"]}


if __name__ == "__main__":
    import uvicorn

    _target, _options = _main_uvicorn_args()
    uvicorn.run(_target, **_options)
