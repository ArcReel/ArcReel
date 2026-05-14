"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd ArcReel
    uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

注意：必须用 --reload-dir 限定监视目录，否则 watchfiles 会扫描
node_modules / .venv / .git / .worktrees 等十几万个文件，单核 CPU 50%+。
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from lib import PROJECT_ROOT
from lib.agent_session_store import session_store_enabled
from lib.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent_session_store.store import DbSessionStore
from lib.app_data_dir import app_data_dir
from lib.config.env_keys import PROVIDER_SECRET_KEYS
from lib.db import async_session_factory, close_db, init_db
from lib.generation_worker import GenerationWorker
from lib.httpx_shared import shutdown_http_client, startup_http_client
from lib.logging_config import setup_logging
from lib.project_migrations import cleanup_stale_backups, run_project_migrations
from lib.source_loader.migration import migrate_project_source_encoding
from server.auth import ensure_auth_password
from server.routers import (
    agent_chat,
    agent_config,
    api_keys,
    assets,
    assistant,
    characters,
    cost_estimation,
    custom_providers,
    files,
    generate,
    grids,
    project_events,
    projects,
    props,
    providers,
    reference_videos,
    scenes,
    system_config,
    tasks,
    usage,
    versions,
)
from server.routers import auth as auth_router
from server.services.project_events import ProjectEventService


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


def check_sandbox_available() -> bool:
    """启动期检测 sandbox 工具可用性。

    返回 ``True`` 表示沙箱可用且必须启用；返回 ``False`` 表示 SDK 不支持
    当前平台（目前仅 Windows — sandboxing.md §"Platform support"），server
    仍可启动但 sandbox 关闭，Bash 工具回退到
    ``SessionManager._WINDOWS_BASH_PREFIX_WHITELIST`` 代码白名单。
    macOS / Linux 工具缺失仍硬失败（受支持平台禁止降级）。
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("sandbox-exec") is None:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on macOS\n"
                "  sandbox-exec: not found in PATH (should be system-installed)\n"
                "Required for ArcReel agent runtime."
            )
        return True
    if system == "Linux":
        # 官方 sandboxing.md 明确 Linux 需要 bubblewrap + socat 一起装
        # （bwrap 做进程/文件隔离，socat 做网络代理转发）。
        missing = [name for name in ("bwrap", "socat") if shutil.which(name) is None]
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
        # 避免 agent 第一次调 Bash 才神秘失败。
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
            probe = subprocess.run(probe_cmd, capture_output=True, timeout=5, check=False)
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


def detect_docker_environment() -> bool:
    """启动期一次性检测当前是否在 Docker / Podman 容器内。

    用于决定是否启用 ``SandboxSettings.enableWeakerNestedSandbox``。
    """
    if _DOCKERENV_PATH.exists():
        return True
    try:
        content = _CGROUP_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in content or "podman" in content


# 初始化日志
setup_logging()
logger = logging.getLogger(__name__)


async def _migrate_source_encoding_on_startup(projects_root: Path) -> dict[str, dict]:
    """对每个项目执行幂等编码迁移。失败被捕获并写日志，不阻塞启动。"""
    summary: dict[str, dict] = {}
    if not projects_root.exists():
        return summary

    def _run_one(project_dir: Path) -> dict:
        marker_dir = project_dir / ".arcreel"
        marker = marker_dir / "source_encoding_migrated"
        if marker.exists():
            return {"skipped": True}
        try:
            result = migrate_project_source_encoding(project_dir)
            marker_dir.mkdir(exist_ok=True)
            marker.touch()
            if result.failed:
                err_log = marker_dir / "migration_errors.log"
                err_log.write_text(
                    "\n".join(f"FAILED: {name}" for name in result.failed) + "\n",
                    encoding="utf-8",
                )
            return {
                "migrated": result.migrated,
                "skipped": result.skipped,
                "failed": result.failed,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "源文件编码迁移失败 project=%s，已跳过，server 继续启动",
                project_dir.name,
            )
            try:
                marker_dir.mkdir(exist_ok=True)
                (marker_dir / "migration_errors.log").write_text(f"FATAL: {exc}\n", encoding="utf-8")
                marker.touch()
            except Exception:  # noqa: BLE001
                pass
            return {"error": str(exc)}

    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        summary[project_dir.name] = await asyncio.to_thread(_run_one, project_dir)
    return summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    # 安全红线检测：先父进程 env 净化，再 sandbox 工具可用性，再 docker 检测
    assert_no_provider_secrets_in_environ()
    sandbox_enabled = check_sandbox_available()
    # detect_docker_environment 仅在 sandbox 可用平台有意义（Linux 路径探测）；
    # Windows 回退时跳过，避免无意义的文件系统调用。
    is_docker = detect_docker_environment() if sandbox_enabled else False
    logger.info("Sandbox runtime: enabled=%s docker=%s", sandbox_enabled, is_docker)

    app.state.in_docker = is_docker
    app.state.sandbox_enabled = sandbox_enabled

    ensure_auth_password()

    # Run Alembic migrations (auto-creates tables on first start)
    await init_db()

    # Run any pending project.json schema migrations (file-based).
    # Both calls are synchronous filesystem walks — offload to a worker thread
    # so they don't block the event loop during uvicorn startup.
    projects_root = app_data_dir()
    migration_summary = await asyncio.to_thread(run_project_migrations, projects_root)
    if migration_summary.migrated or migration_summary.failed:
        logger.info(
            "Project migrations: migrated=%s skipped=%d failed=%s",
            migration_summary.migrated,
            len(migration_summary.skipped),
            migration_summary.failed,
        )
    await asyncio.to_thread(cleanup_stale_backups, projects_root, 7)

    # 源文件编码迁移（幂等；失败不阻塞启动）
    source_migration_summary = await _migrate_source_encoding_on_startup(projects_root)
    migrated_total = sum(len(s.get("migrated") or []) for s in source_migration_summary.values())
    failed_total = sum(len(s.get("failed") or []) for s in source_migration_summary.values())
    if migrated_total or failed_total:
        logger.info(
            "源文件编码迁移完成：migrated=%d failed=%d projects=%d",
            migrated_total,
            failed_total,
            len(source_migration_summary),
        )

    # Migrate any pre-existing local SDK jsonl transcripts into the DbSessionStore.
    # Runs once (marker-gated); failures are non-fatal and logged.
    if session_store_enabled():
        try:
            store = DbSessionStore(async_session_factory)
            await migrate_local_transcripts_to_store(
                store,
                projects_root=projects_root,
                data_dir=projects_root,  # same place .arcreel.db lives, so docker volume catches it
            )
        except Exception:
            logger.exception("session-store transcript migration failed (non-fatal)")

    # Migrate legacy .system_config.json → DB (no-op if file doesn't exist or already migrated)
    try:
        from lib.config.migration import migrate_json_to_db

        json_path = app_data_dir() / ".system_config.json"
        async with async_session_factory() as session:
            await migrate_json_to_db(session, json_path)
    except Exception as exc:
        logger.warning("JSON→DB config migration failed (non-fatal): %s", exc)

    # 修复存量项目的 agent_runtime 软连接（同步文件遍历 → 放到 worker 线程）
    from lib.project_manager import ProjectManager

    _pm = ProjectManager(app_data_dir())
    _symlink_stats = await asyncio.to_thread(_pm.repair_all_symlinks)
    if any(v > 0 for v in _symlink_stats.values()):
        logger.info("agent_runtime 软连接修复完成: %s", _symlink_stats)

    # 启动共享 httpx 客户端（用于版本检查等外部 API 调用）
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
    project_event_service = ProjectEventService(PROJECT_ROOT, projects_root=app_data_dir())
    app.state.project_event_service = project_event_service
    await project_event_service.start()
    logger.info("ProjectEventService 已启动")

    yield

    # Shutdown
    project_event_service = getattr(app.state, "project_event_service", None)
    if project_event_service:
        logger.info("正在停止 ProjectEventService...")
        await project_event_service.shutdown()
        logger.info("ProjectEventService 已停止")
    worker = getattr(app.state, "generation_worker", None)
    if worker:
        logger.info("正在停止 GenerationWorker...")
        await worker.stop()
        logger.info("GenerationWorker 已停止")
    await shutdown_http_client()
    await close_db()


# 创建 FastAPI 应用
app = FastAPI(
    title="视频项目管理 WebUI",
    description="AI 视频生成工作空间的 Web 管理界面",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置（env 驱动）：
#   - CORS_ORIGINS 未设置 / 空 / 包含 "*" → 通配 origins，credentials 强制关闭
#     （CORS spec 不允许通配 + credentials 组合；Starlette 在初始化时会 RuntimeError）
#   - 否则按逗号分隔解析为白名单，credentials 打开供前端附带 cookie / Authorization 跨域
_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
_allow_origins: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]
if not _allow_origins or "*" in _allow_origins:
    _allow_origins = ["*"]
    _allow_credentials = False
else:
    _allow_credentials = True

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


# 前端每 3s 轮询下述接口获取任务状态；稳态下成功响应会把真正的错误/慢请求淹没，
# 所以对 2xx + 快速响应降级到 DEBUG，异常/慢响应仍走 INFO 保证可观测。
_QUIET_POLL_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks/stats"),
    }
)
_QUIET_SLOW_THRESHOLD_MS = 500.0


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
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
            and elapsed_ms < _QUIET_SLOW_THRESHOLD_MS
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


# 注册 API 路由
app.include_router(auth_router.router, prefix="/api/v1", tags=["认证"])
app.include_router(projects.router, prefix="/api/v1", tags=["项目管理"])
app.include_router(characters.router, prefix="/api/v1", tags=["角色管理"])
app.include_router(scenes.router, prefix="/api/v1", tags=["场景管理"])
app.include_router(props.router, prefix="/api/v1", tags=["道具管理"])
app.include_router(files.router, prefix="/api/v1", tags=["文件管理"])
app.include_router(generate.router, prefix="/api/v1", tags=["生成"])
app.include_router(versions.router, prefix="/api/v1", tags=["版本管理"])
app.include_router(usage.router, prefix="/api/v1", tags=["费用统计"])
app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant", tags=["助手会话"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务队列"])
app.include_router(project_events.router, prefix="/api/v1", tags=["项目变更流"])
app.include_router(providers.router, prefix="/api/v1", tags=["供应商管理"])
app.include_router(system_config.router, prefix="/api/v1", tags=["系统配置"])
app.include_router(api_keys.router, prefix="/api/v1", tags=["API Key 管理"])
app.include_router(agent_chat.router, prefix="/api/v1", tags=["Agent 对话"])
app.include_router(agent_config.router, prefix="/api/v1", tags=["Agent 配置"])
app.include_router(custom_providers.router, prefix="/api/v1", tags=["自定义供应商"])
app.include_router(cost_estimation.router, prefix="/api/v1", tags=["费用估算"])
app.include_router(grids.router, prefix="/api/v1", tags=["宫格图"])
app.include_router(reference_videos.router, prefix="/api/v1", tags=["参考生视频"])
app.include_router(assets.router, prefix="/api/v1", tags=["全局资产库"])


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker()


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "message": "视频项目管理 WebUI 运行正常"}


@app.get("/skill.md", include_in_schema=False)
async def serve_skill_md(request: Request) -> Response:
    """动态渲染 skill.md 模板，将 {{BASE_URL}} 替换为实际服务地址（无需认证）。"""
    from starlette.responses import PlainTextResponse

    template_path = PROJECT_ROOT / "public" / "skill.md.template"

    def _read() -> tuple[bool, str]:
        if not template_path.exists():
            return False, ""
        return True, template_path.read_text(encoding="utf-8")

    exists, template = await asyncio.to_thread(_read)
    if not exists:
        return PlainTextResponse("skill.md 模板不存在", status_code=404)

    # 从请求推断 base URL；仅信任 x-forwarded-proto（反向代理标准头），
    # host 使用连接实际目标地址，不接受可被用户伪造的 x-forwarded-host。
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.url.netloc
    base_url = f"{scheme}://{host}"

    content = template.replace("{{BASE_URL}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


# 前端构建产物：SPA 静态文件服务（必须在所有显式路由之后挂载）
frontend_dist_dir = PROJECT_ROOT / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """服务 Vite 构建产物，未匹配的路径回退到 index.html（SPA 路由）。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


if frontend_dist_dir.exists():
    app.mount("/", SPAStaticFiles(directory=frontend_dist_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    _host, _port = _resolve_listen_addr()
    uvicorn.run(app, host=_host, port=_port, reload=True)
