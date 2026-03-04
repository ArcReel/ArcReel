"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd ArcReel
    uv run uvicorn server.app:app --reload --port 8080
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from lib import PROJECT_ROOT
from lib.db import init_db, close_db
from lib.logging_config import setup_logging

from lib.generation_worker import GenerationWorker
from server.auth import verify_token, ensure_auth_password
from server.routers import (
    assistant,
    projects,
    characters,
    clues,
    files,
    generate,
    project_events,
    versions,
    usage,
    tasks,
    system_config,
)
from server.routers import auth as auth_router
from server.services.project_events import ProjectEventService

# 初始化日志
setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    ensure_auth_password()

    # Initialize database tables (dev convenience; production uses Alembic)
    await init_db()

    # Initialize async services
    await assistant.assistant_service.startup()

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


# 不需要认证的路径前缀
_AUTH_WHITELIST = (
    "/health",
    "/assets",
    "/api/v1/auth/login",
    "/api/v1/files/",
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """JWT 认证中间件。白名单路径、非 API 路径（前端页面）跳过认证。"""
    path = request.url.path

    # 白名单路径跳过
    if any(path.startswith(prefix) for prefix in _AUTH_WHITELIST):
        return await call_next(request)

    # 非 API 路径（前端静态页面）跳过
    if not path.startswith("/api/"):
        return await call_next(request)

    # CORS preflight 跳过（浏览器不会附加 Authorization header）
    if request.method == "OPTIONS":
        return await call_next(request)

    # 从 Authorization header 获取 token
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()

    # SSE 端点也接受 query param 中的 token
    if token is None:
        token = request.query_params.get("token")

    if not token:
        return JSONResponse(status_code=401, content={"detail": "缺少认证 token"})

    payload = verify_token(token)
    if payload is None:
        return JSONResponse(status_code=401, content={"detail": "token 无效或已过期"})

    return await call_next(request)


# 注册 API 路由
app.include_router(auth_router.router, prefix="/api/v1", tags=["认证"])
app.include_router(projects.router, prefix="/api/v1", tags=["项目管理"])
app.include_router(characters.router, prefix="/api/v1", tags=["人物管理"])
app.include_router(clues.router, prefix="/api/v1", tags=["线索管理"])
app.include_router(files.router, prefix="/api/v1", tags=["文件管理"])
app.include_router(generate.router, prefix="/api/v1", tags=["生成"])
app.include_router(versions.router, prefix="/api/v1", tags=["版本管理"])
app.include_router(usage.router, prefix="/api/v1", tags=["费用统计"])
app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant", tags=["助手会话"])
app.include_router(tasks.router, prefix="/api/v1", tags=["任务队列"])
app.include_router(project_events.router, prefix="/api/v1", tags=["项目变更流"])
app.include_router(system_config.router, prefix="/api/v1", tags=["系统配置"])

# 前端构建产物目录（Vite）
frontend_dir = PROJECT_ROOT / "frontend"
frontend_dist_dir = frontend_dir / "dist"
frontend_assets_dir = frontend_dist_dir / "assets"
frontend_index_file = frontend_dist_dir / "index.html"

if frontend_assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_assets_dir), name="frontend-assets")


def _serve_frontend_index():
    if frontend_index_file.exists():
        return FileResponse(frontend_index_file)
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Frontend build not found. Run: cd frontend && npm install && npm run build"
            )
        },
    )


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker()


@app.get("/", include_in_schema=False)
async def serve_root():
    """服务 React 前端入口"""
    return _serve_frontend_index()


@app.get("/login", include_in_schema=False)
async def serve_login():
    """服务登录页面"""
    return _serve_frontend_index()


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
@app.get("/app/{subpath:path}", include_in_schema=False)
async def serve_dashboard(subpath: str = ""):
    """服务 React 前端入口"""
    return _serve_frontend_index()


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "message": "视频项目管理 WebUI 运行正常"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=True)
