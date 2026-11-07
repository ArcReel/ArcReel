"""GET /api/v1/system/logs/download behavior tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")


# Three importlib.reload(app_module) calls: FastAPI mounts routes/reads env immediately on import;
# monkeypatched environment requires rerunning server.app top-level code to affect tests. Unlike
# tests/test_logging_persistence.py, whose setup_logging() rereads env on each call,
# reload is required here.
@pytest.fixture
async def logs_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth_disabled: None):
    log_dir = tmp_path / "data" / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.infra.app_data_dir import reset_for_tests

    reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, log_dir


async def test_download_returns_zip(logs_client) -> None:
    client, log_dir = logs_client
    (log_dir / "arcreel.log").write_text("test log line\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers.get("content-disposition", "")


async def test_zip_contains_diagnostics(logs_client) -> None:
    client, _log_dir = logs_client
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert "diagnostics.txt" in z.namelist()
    diag = z.read("diagnostics.txt").decode("utf-8")
    assert "App version" in diag


async def test_zip_includes_log_files(logs_client) -> None:
    client, log_dir = logs_client
    (log_dir / "arcreel.log").write_text("active log\n", encoding="utf-8")
    (log_dir / "arcreel.log.2026-05-15").write_text("archived log\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = z.namelist()
    assert any(n.endswith("arcreel.log") for n in names)
    assert any(n.endswith("arcreel.log.2026-05-15") for n in names)


async def test_empty_logs_dir(logs_client) -> None:
    client, _ = logs_client
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert z.namelist() == ["diagnostics.txt"]


async def test_oversized_file_skipped(logs_client) -> None:
    client, log_dir = logs_client
    big = log_dir / "arcreel.log.2026-05-10"
    with big.open("wb") as f:
        f.seek(101 * 1024 * 1024 - 1)
        f.write(b"\0")

    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    diag = z.read("diagnostics.txt").decode("utf-8")
    assert "skipped: too large" in diag
    assert big.name in diag
    assert big.name not in z.namelist()


async def test_missing_logs_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth_disabled: None) -> None:
    # Deliberately omit data-root log directories.
    assert not (tmp_path / "data" / "logs").exists()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.infra.app_data_dir import reset_for_tests

    reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/system/logs/download")
        assert res.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(res.content))
        assert z.namelist() == ["diagnostics.txt"]


async def test_download_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-32-chars-long-xxxxx")
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.infra.app_data_dir import reset_for_tests

    reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/system/logs/download")
        assert res.status_code == 401
