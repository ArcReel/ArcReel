"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd ArcReel
    uv run uvicorn server.app:app --reload --port 1241
"""

import asyncio
import logging
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
from lib.db import async_session_factory, close_db, init_db
from lib.generation_worker import GenerationWorker
from lib.logging_config import setup_logging
from lib.project_migrations import cleanup_stale_backups, run_project_migrations
from lib.source_loader.migration import migrate_project_source_encoding
from server.auth import ensure_auth_password
from server.routers import (
    agent_chat,
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
    ensure_auth_password()

    # Run Alembic migrations (auto-creates tables on first start)
    await init_db()

    # Run any pending project.json schema migrations (file-based).
    # Both calls are synchronous filesystem walks — offload to a worker thread
    # so they don't block the event loop during uvicorn startup.
    projects_root = PROJECT_ROOT / "projects"
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

    # Migrate legacy .system_config.json → DB (no-op if file doesn't exist or already migrated)
    try:
        from lib.config.migration import migrate_json_to_db

        json_path = PROJECT_ROOT / "projects" / ".system_config.json"
        async with async_session_factory() as session:
            await migrate_json_to_db(session, json_path)
    except Exception as exc:
        logger.warning("JSON→DB config migration failed (non-fatal): %s", exc)

    # Sync Anthropic DB settings to env vars (Claude Agent SDK reads from os.environ)
    try:
        from lib.config.service import ConfigService, sync_anthropic_env

        async with async_session_factory() as session:
            svc = ConfigService(session)
            all_settings = await svc.get_all_settings()
            sync_anthropic_env(all_settings)
    except Exception as exc:
        logger.warning("DB→env Anthropic config sync failed (non-fatal): %s", exc)

    # 修复存量项目的 agent_runtime 软连接（同步文件遍历 → 放到 worker 线程）
    from lib.project_manager import ProjectManager

    _pm = ProjectManager(PROJECT_ROOT / "projects")
    _symlink_stats = await asyncio.to_thread(_pm.repair_all_symlinks)
    if any(v > 0 for v in _symlink_stats.values()):
        logger.info("agent_runtime 软连接修复完成: %s", _symlink_stats)

    # Initialize async services
    await assistant.assistant_service.startup()
    assistant.assistant_service.session_manager.start_patrol()

    logger.info("启动 GenerationWorker...")
    worker = create_generation_worker()
    app.state.generation_worker = worker
    await worker.start()
    logger.info("GenerationWorker 已启动")

    logger.info("启动 ProjectEventService...")
    project_event_service = ProjectEventService(PROJECT_ROOT)
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
    await close_db()


# 创建 FastAPI 应用
app = FastAPI(
    title="视频项目管理 WebUI",
    description="AI 视频生成工作空间的 Web 管理界面",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        logger.info(
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

    uvicorn.run(app, host="0.0.0.0", port=1241, reload=True)
