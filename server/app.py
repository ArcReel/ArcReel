"""
Video Project Management WebUI - FastAPI Main Application

Startup:
    cd ArcReel
    uv run uvicorn server.app:app --reload --port 1241
"""

import logging
import time
from contextlib import asynccontextmanager

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
from server.auth import ensure_auth_password
from server.routers import (
    agent_chat,
    api_keys,
    assistant,
    characters,
    clues,
    cost_estimation,
    custom_providers,
    files,
    generate,
    project_events,
    projects,
    providers,
    system_config,
    tasks,
    usage,
    versions,
)
from server.routers import auth as auth_router
from server.services.project_events import ProjectEventService

# Initialize logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    # Startup
    ensure_auth_password()

    # Run Alembic migrations (auto-creates tables on first start)
    await init_db()

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

    # Fix legacy project agent_runtime symlinks
    from lib.project_manager import ProjectManager

    _pm = ProjectManager(PROJECT_ROOT / "projects")
    _symlink_stats = _pm.repair_all_symlinks()
    if any(v > 0 for v in _symlink_stats.values()):
        logger.info("agent_runtime symlink repair complete: %s", _symlink_stats)

    # Initialize async services
    await assistant.assistant_service.startup()
    assistant.assistant_service.session_manager.start_patrol()

    logger.info("Starting GenerationWorker...")
    worker = create_generation_worker()
    app.state.generation_worker = worker
    await worker.start()
    logger.info("GenerationWorker started")

    logger.info("Starting ProjectEventService...")
    project_event_service = ProjectEventService(PROJECT_ROOT)
    app.state.project_event_service = project_event_service
    await project_event_service.start()
    logger.info("ProjectEventService started")

    yield

    # Shutdown
    project_event_service = getattr(app.state, "project_event_service", None)
    if project_event_service:
        logger.info("Stopping ProjectEventService...")
        await project_event_service.shutdown()
        logger.info("ProjectEventService stopped")
    worker = getattr(app.state, "generation_worker", None)
    if worker:
        logger.info("Stopping GenerationWorker...")
        await worker.stop()
        logger.info("GenerationWorker stopped")
    await close_db()


# Create FastAPI application
app = FastAPI(
    title="Video Project Management WebUI",
    description="Web management interface for AI video generation workspace",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
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


# Register API routes
app.include_router(auth_router.router, prefix="/api/v1", tags=["Authentication"])
app.include_router(projects.router, prefix="/api/v1", tags=["Project Management"])
app.include_router(characters.router, prefix="/api/v1", tags=["Character Management"])
app.include_router(clues.router, prefix="/api/v1", tags=["Clue Management"])
app.include_router(files.router, prefix="/api/v1", tags=["File Management"])
app.include_router(generate.router, prefix="/api/v1", tags=["Generation"])
app.include_router(versions.router, prefix="/api/v1", tags=["Version Management"])
app.include_router(usage.router, prefix="/api/v1", tags=["Usage Statistics"])
app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant", tags=["Assistant Sessions"])
app.include_router(tasks.router, prefix="/api/v1", tags=["Task Queue"])
app.include_router(project_events.router, prefix="/api/v1", tags=["Project Change Stream"])
app.include_router(providers.router, prefix="/api/v1", tags=["Provider Management"])
app.include_router(system_config.router, prefix="/api/v1", tags=["System Configuration"])
app.include_router(api_keys.router, prefix="/api/v1", tags=["API Key Management"])
app.include_router(agent_chat.router, prefix="/api/v1", tags=["Agent Chat"])
app.include_router(custom_providers.router, prefix="/api/v1", tags=["Custom Providers"])
app.include_router(cost_estimation.router, prefix="/api/v1", tags=["Cost Estimation"])


def create_generation_worker() -> GenerationWorker:
    return GenerationWorker()


@app.get("/health")
async def health_check():
    """Health check."""
    return {"status": "ok", "message": "Video Project Management WebUI is running"}


@app.get("/skill.md", include_in_schema=False)
async def serve_skill_md(request: Request) -> Response:
    """Dynamically render skill.md template, replacing {{BASE_URL}} with actual service address (no auth required)."""
    from starlette.responses import PlainTextResponse

    template_path = PROJECT_ROOT / "public" / "skill.md.template"
    if not template_path.exists():
        return PlainTextResponse("skill.md template not found", status_code=404)

    template = template_path.read_text(encoding="utf-8")

    # Infer base URL from request; only trust x-forwarded-proto (standard reverse proxy header),
    # host uses the actual connection target address, not the user-forgeable x-forwarded-host.
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.url.netloc
    base_url = f"{scheme}://{host}"

    content = template.replace("{{BASE_URL}}", base_url)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")


# Frontend build artifacts: SPA static file service (must be mounted after all explicit routes)
frontend_dist_dir = PROJECT_ROOT / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """Serves Vite build artifacts, falling back to index.html for unmatched paths (SPA routing)."""

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
