"""GET /api/v1/system/logs/download 行为测试。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")


# 三处 importlib.reload(app_module)：FastAPI app 在 import 时立刻 mount router 与读取 env，
# monkeypatch 设的 env 要在测试中生效必须让 server.app 重新走一次顶层代码。这一点与
# tests/test_logging_persistence.py 不同——那里 setup_logging() 在每次调用都重新读 env，
# 不需要 reload。
@pytest.fixture
async def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, auth_disabled: None):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, log_dir


async def test_download_returns_zip(_client) -> None:
    client, log_dir = _client
    (log_dir / "arcreel.log").write_text("test log line\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "attachment" in res.headers.get("content-disposition", "")


async def test_zip_contains_diagnostics(_client) -> None:
    client, _log_dir = _client
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert "diagnostics.txt" in z.namelist()
    diag = z.read("diagnostics.txt").decode("utf-8")
    assert "App version" in diag


async def test_zip_includes_log_files(_client) -> None:
    client, log_dir = _client
    (log_dir / "arcreel.log").write_text("active log\n", encoding="utf-8")
    (log_dir / "arcreel.log.2026-05-15").write_text("archived log\n", encoding="utf-8")
    res = await client.get("/api/v1/system/logs/download")
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = z.namelist()
    assert any(n.endswith("arcreel.log") for n in names)
    assert any(n.endswith("arcreel.log.2026-05-15") for n in names)


async def test_empty_logs_dir(_client) -> None:
    client, _ = _client
    res = await client.get("/api/v1/system/logs/download")
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert z.namelist() == ["diagnostics.txt"]


async def test_oversized_file_skipped(_client) -> None:
    client, log_dir = _client
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
    # log_dir 故意不创建
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

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
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    import importlib

    from server import app as app_module

    importlib.reload(app_module)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/system/logs/download")
        assert res.status_code == 401
