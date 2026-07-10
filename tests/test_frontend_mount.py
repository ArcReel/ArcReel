"""前端构建产物挂载行为测试（server/app.py 的 frontend_dist_dir 分支）。"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import lib
from server import app as app_module


@pytest.fixture
def reload_app_cleanup(monkeypatch: pytest.MonkeyPatch):
    """还原 lib.PROJECT_ROOT 并重新 reload server.app，恢复成真实构建产物路径。

    setup（写 index.html、monkeypatch、reload）仍留在各测试体内——顺序必须是
    先落盘构建产物再 reload，模块顶层的 `if index.html.is_file()` 才能读到预期状态。
    """
    yield
    monkeypatch.undo()
    importlib.reload(app_module)


async def test_deep_link_with_extension_falls_back_to_index_html(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """构建产物存在时，带扩展名的 SPA 深链应回退到 index.html 而非被当作静态资源返回 404。"""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/app/projects/demo/source/chapter1.txt")
        assert res.status_code == 200
        assert "shell" in res.text


async def test_write_request_to_spa_path_returns_405_not_shell(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """写请求误入 SPA 页面路径（含带扩展名深链与根路径）应返回 405，而非 SPA 外壳。"""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        deep_link_res = await client.put("/app/projects/demo/source/chapter1.txt")
        assert deep_link_res.status_code == 405
        assert "shell" not in deep_link_res.text

        root_res = await client.post("/")
        assert root_res.status_code == 405
        assert "shell" not in root_res.text


async def test_missing_index_html_skips_mount_without_crashing(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """构建产物目录缺 index.html 时跳过前端挂载，应用仍能正常启动且 API 不受影响。"""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/app/anything")
        assert res.status_code == 404


async def test_spa_shell_responses_are_never_cached(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SPA 外壳（无论走 spa_deep_link 还是 app.frontend 原生 fallback）都不能被浏览器缓存，
    否则重新部署后旧壳会引用已被删除的旧哈希资源导致白屏。
    """
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 带扩展名的深链：命中我们自己注册的 spa_deep_link 路由
        deep_link_res = await client.get("/app/projects/demo/source/chapter1.txt")
        assert deep_link_res.status_code == 200
        assert deep_link_res.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"

        # 根路径：命中 app.frontend 原生 fallback（非我们自己的路由）
        root_res = await client.get("/", headers={"accept": "text/html"})
        assert root_res.status_code == 200
        assert root_res.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
