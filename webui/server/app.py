"""
视频项目管理 WebUI - FastAPI 主应用

启动方式:
    cd cc-novel2video-v2
    uv run uvicorn webui.server.app:app --reload --port 8080
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from webui.server.routers import (
    assistant,
    projects,
    characters,
    clues,
    files,
    generate,
    versions,
    usage,
)

# 创建 FastAPI 应用
app = FastAPI(
    title="视频项目管理 WebUI",
    description="AI 视频生成工作空间的 Web 管理界面",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(projects.router, prefix="/api/v1", tags=["项目管理"])
app.include_router(characters.router, prefix="/api/v1", tags=["人物管理"])
app.include_router(clues.router, prefix="/api/v1", tags=["线索管理"])
app.include_router(files.router, prefix="/api/v1", tags=["文件管理"])
app.include_router(generate.router, prefix="/api/v1", tags=["生成"])
app.include_router(versions.router, prefix="/api/v1", tags=["版本管理"])
app.include_router(usage.router, prefix="/api/v1", tags=["费用统计"])
app.include_router(assistant.router, prefix="/api/v1", tags=["助手会话"])

# 前端构建产物目录（Vite）
frontend_dir = project_root / "frontend"
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


@app.get("/", include_in_schema=False)
async def serve_root():
    """服务 React 前端入口"""
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
