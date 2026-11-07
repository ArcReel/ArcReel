"""Frontend build mounting, covering server/app.py frontend_dist_dir branches."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import lib
from server import app as app_module


@pytest.fixture
def reload_app_cleanup(monkeypatch: pytest.MonkeyPatch):
    """Restore lib.PROJECT_ROOT and reload server.app to restore actual build paths.

    Setup (index.html writes, monkeypatch, reload) remains inside tests: write build artifacts
    before reload so module-level `if index.html.is_file()` observes intended states.
    """
    yield
    monkeypatch.undo()
    importlib.reload(app_module)


async def test_deep_link_with_extension_falls_back_to_index_html(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Existing builds serve extension-bearing SPA deep links through index.html, not static-resource 404s."""
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
    """Write requests to SPA routes, including extension-bearing deep links/root, return 405 rather than SPA shells."""
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


async def test_real_static_file_under_app_path_is_not_shadowed_by_shell(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Existing dist/app/ static files outrank unconditional index.html fallback in spa_deep_link."""
    dist_dir = tmp_path / "frontend" / "dist"
    (dist_dir / "app").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (dist_dir / "app" / "logo.png").write_bytes(b"fake-png-bytes")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/app/logo.png")
        assert res.status_code == 200
        assert res.content == b"fake-png-bytes"


async def test_deep_link_path_traversal_falls_back_to_shell(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """_rest containing ../ must never escape build directories; fall back normally to index.html."""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Use %2e%2e, not literal ../, which httpx collapses client-side;
        # preserve encoded .. in requests to test server containment rather than client normalization.
        # Joining dist/app/<_rest> needs three .. levels to escape app/, dist/, frontend/.
        # Three levels reach tmp_path/secret.txt.
        res = await client.get("/app/%2e%2e/%2e%2e/%2e%2e/secret.txt")
        assert res.status_code == 200
        assert "shell" in res.text
        assert "top-secret" not in res.text


async def test_missing_index_html_skips_mount_without_crashing(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Missing build index.html skips frontend mounting; app startup/APIs remain unaffected."""
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
    """Never browser-cache SPA shells from spa_deep_link or app.frontend fallback: redeployments
    remove old hashed assets, so cached old shells would produce blank pages.
    """
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Extension-bearing deep links reach our registered spa_deep_link route.
        deep_link_res = await client.get("/app/projects/demo/source/chapter1.txt")
        assert deep_link_res.status_code == 200
        assert deep_link_res.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"

        # Root paths reach native app.frontend fallback, not our route.
        root_res = await client.get("/", headers={"accept": "text/html"})
        assert root_res.status_code == 200
        assert root_res.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"


async def test_hashed_assets_are_immutable_but_unhashed_public_files_are_not(
    reload_app_cleanup: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Hashed dist/assets/ resources cache permanently, including 304 revalidation; missing assets/unhashed root files never get immutable."""
    dist_dir = tmp_path / "frontend" / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    bundle = "console.log('arcreel');\n" * 200
    (dist_dir / "assets" / "index-abc123.js").write_text(bundle, encoding="utf-8")
    (dist_dir / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(lib, "PROJECT_ROOT", tmp_path)
    importlib.reload(app_module)

    immutable = "public, max-age=31536000, immutable"
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/assets/index-abc123.js", headers={"accept-encoding": "gzip"})
        assert res.status_code == 200
        assert res.headers["cache-control"] == immutable
        # Compress textual build responses.
        assert res.headers["content-encoding"] == "gzip"
        assert res.text == bundle

        revalidated = await client.get("/assets/index-abc123.js", headers={"if-none-match": res.headers["etag"]})
        assert revalidated.status_code == 304
        assert revalidated.headers["cache-control"] == immutable

        missing = await client.get("/assets/index-gone.js")
        assert missing.status_code == 404
        assert "immutable" not in missing.headers.get("cache-control", "")

        favicon = await client.get("/favicon.svg")
        assert favicon.status_code == 200
        assert "immutable" not in favicon.headers.get("cache-control", "")
