"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd ArcReel
    uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

注意：必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+。

Windows 原生环境不要加 --reload：uvicorn 启用 reload 时在 Windows 上改用
SelectorEventLoop，它不支持子进程，内置 Agent 无法拉起 Claude Code。改用：
    uv run uvicorn server.app:app --port 1241

也可以 ``uv run python -m server.app``：监听地址取 LISTEN_HOST / LISTEN_PORT，
Windows 上自动不开 reload，其他平台开启 reload 并只监视 server 与 lib。
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.datastructures import Headers
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
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
from lib.infra.httpx_shared import get_http_client, shutdown_http_client, startup_http_client
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
from server.services.tasks.generation_context import invalidate_backend_cache
from server.services.tasks.generation_tasks import execute_generation_task
from server.services.tasks.resume_executor import execute_resume_video_task
from server.shutdown import ShutdownController


def assert_no_provider_secrets_in_environ() -> None:
    """父进程禁止持有任何 provider 密钥；违反即 fail-fast。

    Bash 沙箱子进程通过 fork 继承父 env，父进程必须把 provider secrets
    全部下线到 DB，由 SDK options.env 显式注入子进程。
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
    """根据 host sysctl 状态给出 bwrap 失败的精确修复路径。

    procfs 是宿主机共享的，容器内同样能读到 host sysctl 值，所以这套
    诊断在 docker 内外都能跑。优先级：Ubuntu 24.04 AppArmor 限制 >
    传统 unprivileged_userns_clone > max_user_namespaces > 兜底容器配置。
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
    """启动期检测 sandbox 工具可用性。

    返回 ``True`` 表示沙箱可用且必须启用；返回 ``False`` 表示 SDK 不支持
    当前平台（仅 Windows，官方平台约束见
    https://code.claude.com/docs/en/sandboxing#platform-and-tool-compatibility），server
    仍可启动但 sandbox 关闭，Bash 工具回退到
    ``AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST`` 代码白名单。
    macOS / Linux 工具缺失仍硬失败（受支持平台禁止降级）。
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
        # bwrap 装了不代表跑得起来。两类常见失败：
        # 1) 创建 user namespace 被拒：seccomp / apparmor / sysctl 屏蔽
        #    → "No permissions to create new namespace"
        # 2) 新 net namespace 内 loopback 配置被拒：容器缺 CAP_NET_ADMIN
        #    → "loopback: Failed RTM_NEWADDR: Operation not permitted"
        # 用与 SDK 实际调用接近的 unshare 参数试跑，启动期就拦下来，
        # 避免 Agent 第一次调 Bash 才神秘失败。
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
        "SANDBOX_UNSUPPORTED on %s — server 启动 sandbox=disabled，Bash 工具按 Agent 访问策略校验。"
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
    """启动期一次性检测当前是否在 Docker / Podman 容器内。

    用于决定是否启用 ``SandboxSettings.enableWeakerNestedSandbox``。
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


setup_logging(file=False)
logger = logging.getLogger(__name__)


def _log_profile_sync_outcome(stats: dict, *, log: logging.Logger = logger) -> None:
    """根据 ``sync_all_agent_profiles`` 返回的 stats 决定打 info 还是 warning。

    ``stats["aborted"]`` 是 bool；而 bool 是 int 的子类——简单的
    ``isinstance(v, int) and v > 0`` 会把 ``aborted=True`` 当成"物化完成"的正向
    信号，与实际状态相反。先单独处理 abort 信号，再用 ``type(v) is int``（严格
    类型相等）仅统计真正的整数计数。
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
    """对每个项目执行幂等编码迁移。失败被捕获并写日志，不阻塞启动。"""
    summary: dict[str, dict] = {}
    migrate = migrate_source_encoding or migrate_project_source_encoding
    if not projects_dir.exists():  # noqa: ASYNC240 -- 启动期一次性存在性检查，本地元数据
        return summary

    for project_dir in list_project_dirs(projects_dir):
        summary[project_dir.name] = await asyncio.to_thread(
            migrate_project_source_encoding_once, project_dir, migrate=migrate
        )
    return summary


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """应用生命周期管理"""
    shutdown = ShutdownController()
    app.state.shutdown_controller = shutdown
    with shutdown.capture_signals():
        async with _running_app(app, shutdown):
            yield


@asynccontextmanager
async def _running_app(app: FastAPI, shutdown: ShutdownController) -> AsyncGenerator[None]:
    assert_no_provider_secrets_in_environ()
    sandbox_enabled = check_sandbox_available()
    is_docker = detect_docker_environment() if sandbox_enabled else False
    logger.info("Sandbox runtime: enabled=%s docker=%s", sandbox_enabled, is_docker)

    app.state.in_docker = is_docker
    app.state.sandbox_enabled = sandbox_enabled

    await asyncio.to_thread(log_ffmpeg_status)

    await asyncio.to_thread(migrate_legacy_log_dir)
    attach_file_handler()
    warn_if_log_dir_env_set()
    warn_if_env_diverges_from_file()

    ensure_auth_password()
    warn_if_auth_disabled()

    await init_db()

    layout = DataRootLayout.current()
    await migrate_data_root_layout(
        layout.root,
        session_factory=async_session_factory,
        sdk_config_dir=default_sdk_config_dir(),
    )
    await asyncio.to_thread(ensure_token_secret)

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

    try:
        from lib.catalog.official_sync import load_synced_data

        async with async_session_factory() as session:
            await load_synced_data(session)
    except Exception:
        logger.exception("loading synced catalog data failed (non-fatal)")

    try:
        from lib.catalog.user_entries import reload_user_entries

        async with async_session_factory() as session:
            await reload_user_entries(session)
    except Exception:
        logger.exception("loading user-modified catalog entries failed (non-fatal)")

    try:
        from server.routers.provider_models import reload_user_added_models

        async with async_session_factory() as session:
            await reload_user_added_models(session)
    except Exception:
        logger.exception("loading user-added built-in provider models failed (non-fatal)")

    from lib.config import pre_upgrade_recommendations
    from lib.project.project_migrations.v19_to_v20_bare_provider_models import install_bare_provider_models

    async with async_session_factory() as session:
        pre_upgrade = await pre_upgrade_recommendations.capture(session)
    install_bare_provider_models(pre_upgrade.model)

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

    try:
        await align_session_keys(
            async_session_factory,
            projects_dir=layout.projects_dir,
            sdk_config_dir=default_sdk_config_dir(),
            store_enabled=session_store_enabled(),
        )
    except Exception:
        logger.exception("会话存储键对齐失败（不阻塞启动）")

    if session_store_enabled():
        try:
            store = DbSessionStore(async_session_factory)
            await migrate_local_transcripts_to_store(store, data_root=layout.root)
        except Exception:
            logger.exception("session-store transcript migration failed (non-fatal)")

    try:
        from lib.config.migration import migrate_json_to_db

        async with async_session_factory() as session:
            await migrate_json_to_db(session, layout.root)
    except Exception as exc:
        logger.warning("JSON→DB config migration failed (non-fatal): %s", exc)

    try:
        from lib.config.migration import migrate_text_tier_settings

        async with async_session_factory() as session:
            await migrate_text_tier_settings(session)
    except Exception as exc:
        logger.warning("text tier settings migration failed (non-fatal): %s", exc)

    try:
        from lib.config.recommended_models import freeze_effective_defaults

        async with async_session_factory() as session:
            await freeze_effective_defaults(session)
    except Exception:
        logger.exception("freezing effective default models failed (non-fatal)")

    try:
        from lib.market.sources import seed_official_source

        async with async_session_factory() as session:
            await seed_official_source(session)
    except Exception as exc:
        logger.warning("official market source seed failed (non-fatal): %s", exc)

    from lib.project.project_manager import get_project_manager

    _pm = get_project_manager()
    _profile_sync_stats = await asyncio.to_thread(_pm.sync_all_agent_profiles)
    _log_profile_sync_outcome(_profile_sync_stats)

    await startup_http_client()

    await assistant.assistant_service.startup(in_docker=is_docker, sandbox_enabled=sandbox_enabled)
    session_manager = assistant.assistant_service.session_manager
    session_manager.reset_streams()
    session_manager.start_patrol()

    logger.info("启动 GenerationWorker...")
    worker = create_generation_worker()
    app.state.generation_worker = worker
    shutdown.on_shutdown(worker.stop_admission)
    shutdown.on_shutdown(session_manager.stop_streams)
    await worker.start()
    logger.info("GenerationWorker 已启动")

    logger.info("启动 ProjectEventService...")
    project_event_service = ProjectEventService(PROJECT_ROOT, data_root=layout.root)
    app.state.project_event_service = project_event_service
    shutdown.on_shutdown(project_event_service.stop_streams)
    await project_event_service.start()
    logger.info("ProjectEventService 已启动")
    assistant.assistant_service.session_manager.set_autonomous_turn_listener(
        project_event_service.publish_assistant_session_resumed
    )

    stall_probe = start_loop_stall_probe()
    catalog_sync = start_catalog_sync(async_session_factory)
    from server.services.project.trash_expiry import start_trash_expiry, stop_trash_expiry

    trash_expiry = start_trash_expiry(layout)
    asset_ingest = start_asset_ingest(async_session_factory)
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
        await stop_loop_stall_probe(stall_probe)

        assistant.assistant_service.session_manager.set_autonomous_turn_listener(None)
        logger.info("正在停止 ProjectEventService...")
        await project_event_service.shutdown()
        logger.info("ProjectEventService 已停止")

        logger.info("正在停止 Agent 会话与 GenerationWorker...")
        results = await asyncio.gather(
            assistant.assistant_service.shutdown(),
            worker.stop(),
            return_exceptions=True,
        )

        from lib.custom_provider.endpoint_test import shutdown_trial_runs

        await shutdown_trial_runs()
        await shutdown_http_client()
        await close_db()

        errors = [result for result in results if isinstance(result, BaseException)]
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Agent session and generation worker shutdown failed", errors)


app = FastAPI(
    title="视频项目管理 WebUI",
    description="AI 视频生成工作空间的 Web 管理界面",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_valid_project_name)],
)

_allow_origins, _allow_credentials = resolve_cors_policy()

register_error_handlers(app, cors_allow_origins=_allow_origins, cors_allow_credentials=_allow_credentials)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_listen_addr() -> tuple[str, int]:
    """解析 ``LISTEN_HOST`` / ``LISTEN_PORT``，供 ``__main__`` 块与测试共用。

    **作用范围**：仅当通过 ``python server/app.py`` 直接执行（走下方 ``__main__``）
    时生效。通过 ``uvicorn server.app:app`` 这种标准 ASGI CLI 启动时，listen 地址
    由 uvicorn 进程自身的 ``--host`` / ``--port`` 参数决定，本函数不参与 —— 因为
    ASGI app 模块在 import 时无法回头改 uvicorn 进程的绑定。Docker、systemd 等
    部署需要把 host/port 作为 uvicorn CLI 参数显式传入。

    truthy 默认（``or``）兜底，覆盖 ``.env`` 误写空值（如 ``LISTEN_PORT=``）的场景。
    """
    host = os.environ.get("LISTEN_HOST") or "0.0.0.0"
    port = int(os.environ.get("LISTEN_PORT") or "1241")
    return host, port


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

app.include_router(auth_router.public_router, prefix="/api/v1", tags=["认证"])
app.include_router(files.public_router, prefix="/api/v1", tags=["文件管理"])

app.include_router(projects.self_auth_router, prefix="/api/v1", tags=["项目管理"])
app.include_router(edit_timelines.self_auth_router, prefix="/api/v1", tags=["剪辑时间线"])


mount_remote_mcp(app, remote_mcp_host)


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker(executor=execute_generation_task, resume_executor=execute_resume_video_task)


def start_catalog_sync(
    session_factory: async_sessionmaker[AsyncSession],
    http_client: Callable[[], httpx.AsyncClient] = get_http_client,
) -> asyncio.Task[None] | None:
    """启动模型目录的官方同步；装入新数据后清 backend 实例缓存并重载 worker 容量。

    自定义供应商的 backend 在构造时由目录注入画质可选值、参考图上限与能力，内置视频 backend 绑定构造时的条目，
    都随实例缓存；设置接口与费用预估每次现读目录。不清缓存的话，同步后执行与预估会用两份不同的数据。
    """

    async def on_applied() -> None:
        invalidate_backend_cache()
        worker = getattr(app.state, "generation_worker", None)
        if worker is not None:
            await worker.reload_limits()

    return start_official_sync(session_factory, http_client, on_applied=on_applied)


@app.get("/health")
async def health_check():
    """健康检查；commit 为镜像构建时注入的 commit sha，源码运行时为 null。"""
    return {
        "status": "ok",
        "message": "视频项目管理 WebUI 运行正常",
        "commit": os.environ.get("ARCREEL_BUILD_COMMIT") or None,
    }


@app.get("/agent-installation-guide.md", include_in_schema=False)
async def serve_agent_installation_guide(request: Request) -> Response:
    """动态渲染 Agent 安装指引，将 {{BASE_URL}} 替换为实际服务地址（无需认证）。"""
    from fastapi.responses import PlainTextResponse

    template_path = PROJECT_ROOT / "public" / "agent-installation-guide.md"

    def _read() -> tuple[bool, str]:
        if not template_path.exists():
            return False, ""
        return True, template_path.read_text(encoding="utf-8")

    exists, template = await asyncio.to_thread(_read)
    if not exists:
        return PlainTextResponse("Agent 安装指引不存在", status_code=404)

    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.url.netloc
    base_url = f"{scheme}://{host}"

    content = template.replace("{{BASE_URL}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


_HASHED_FRONTEND_ASSETS_PREFIX = "/assets/"
_SPA_SHELL_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"
_HASHED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class FrontendCacheHeadersMiddleware:
    """前端构建产物的缓存头。

    - SPA 入口 HTML 外壳禁止浏览器缓存：覆盖 spa_deep_link 与 app.frontend 原生 fallback
      两条路径共用的响应特征（text/html），否则重新部署后浏览器可能沿用旧壳加载已被删除的
      旧哈希资源，导致白屏——按 content-type 而非按路由判定，才能同时管住 "/"、"/login" 等
      落在原生 fallback 上的入口。
    - ``/assets/`` 下的哈希资源（含 304 再验证响应）设 immutable；404 等失败响应不缓存。

    纯 ASGI 实现而非 BaseHTTPMiddleware：这是个作用于全部请求的全局中间件，
    BaseHTTPMiddleware 的 anyio TaskGroup + contextvars 复制机制会给每个请求引入额外开销。
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
    """JSON / JS / CSS / HTML 等文本响应的 gzip 压缩。

    沿用 Starlette GZip 的默认排除清单（text/event-stream 与 image/audio/video 等已压缩的
    媒体类型），206 分段响应原样透传；流式响应按块 Z_SYNC_FLUSH，不攒批。排除类型的响应头
    由 Starlette（>= 1.8）即时放行，事件流在首个事件前就能收到响应头。compresslevel 取 zlib 默认档 6：
    JSON/JS 的体积收益与 9 相差无几，CPU 开销低得多。
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


app.add_middleware(ResponseCompressionMiddleware)


# 挂载条件必须检查 index.html 而非目录：app.frontend 在启动期校验 fallback 文件，
# 构建产物不完整时会抛 RuntimeError 拖垮整个应用（含全部 API）
frontend_dist_dir = PROJECT_ROOT / "frontend" / "dist"

if (frontend_dist_dir / "index.html").is_file():

    def _spa_deep_link_response(rest: str) -> FileResponse:
        # SPA 深链末段可能带扩展名（如 /app/projects/x/source/chapter1.txt），
        # app.frontend 的 fallback 会将其判为静态资源请求返回 404，此处显式兜底回 SPA 外壳。
        # 本路由注册在 app.frontend 之前，/app/ 下任何请求都会先到这里——若构建产物中
        # 恰好存在 dist/app/... 下的真实静态文件（URL 路径与 app.frontend 的映射规则一致，
        # 即相对 dist 根目录同路径），须优先返回该文件，避免被无条件遮蔽。
        candidate = try_safe_join(frontend_dist_dir / "app", rest, require_file=True)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(frontend_dist_dir / "index.html")

    @app.get("/app/{_rest:path}", include_in_schema=False)
    async def spa_deep_link(_rest: str) -> FileResponse:
        return await asyncio.to_thread(_spa_deep_link_response, _rest)

    app.frontend("/", directory=frontend_dist_dir, fallback="index.html")
else:
    logger.warning("frontend/dist/index.html 不存在，跳过前端页面挂载（API 不受影响）")


def _main_uvicorn_args(platform_name: str = sys.platform) -> tuple[str | FastAPI, dict[str, Any]]:
    """``__main__`` 交给 ``uvicorn.run`` 的应用与参数。

    reload 只接受导入字符串。Windows 上 uvicorn 一旦启用 reload 就改用不支持子进程的
    SelectorEventLoop，内置 Agent 拉不起 Claude Code CLI，因此 Windows 不开 reload。
    """
    host, port = _resolve_listen_addr()
    options = {"host": host, "port": port, "timeout_graceful_shutdown": 10}
    if platform_name == "win32":
        return app, {**options, "reload": False}
    return "server.app:app", {**options, "reload": True, "reload_dirs": ["server", "lib"]}


if __name__ == "__main__":
    import uvicorn

    _target, _options = _main_uvicorn_args()
    uvicorn.run(_target, **_options)
