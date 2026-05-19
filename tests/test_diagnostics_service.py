"""diagnostics.collect_diagnostics 行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.services.diagnostics as diag_mod
from lib.app_data_dir import _reset_for_tests


def test_collect_returns_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    _reset_for_tests()

    text = diag_mod.collect_diagnostics()
    assert isinstance(text, str)
    assert "ArcReel diagnostics" in text
    assert "App version" in text
    assert "Python" in text
    assert "OS" in text
    assert "Data directory" in text
    assert "Log directory" in text


def test_collect_masks_db_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://arcuser:supersecretpassword@db.example.com:5432/arcreel",
    )
    _reset_for_tests()

    text = diag_mod.collect_diagnostics()
    db_line = next(line for line in text.splitlines() if line.startswith("Database URL:"))
    assert "supersecretpassword" not in db_line
    # 精确匹配脱敏后的 URL：避免 substring 检查导致 CodeQL 误报 URL sanitization 不完整。
    assert db_line == "Database URL: postgresql+asyncpg://••••:••@db.example.com:5432/arcreel"


def test_collect_masks_db_query_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://host.example.com/arcreel?sslmode=require&password=topsecret&token=abc123",
    )
    _reset_for_tests()

    text = diag_mod.collect_diagnostics()
    db_line = next(line for line in text.splitlines() if line.startswith("Database URL:"))
    assert "topsecret" not in db_line
    assert "abc123" not in db_line
    # 精确匹配脱敏后完整 URL（key 顺序由 parse_qsl→urlencode 保留输入顺序）。
    assert (
        db_line
        == "Database URL: postgresql://host.example.com/arcreel?sslmode=require&password=%E2%80%A2%E2%80%A2&token=%E2%80%A2%E2%80%A2"
    )


def test_collect_swallows_field_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    _reset_for_tests()

    def boom() -> str:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(diag_mod, "_app_version", boom)

    text = diag_mod.collect_diagnostics()
    assert "<unavailable" in text
    assert "Python" in text


def test_collect_returns_log_dir_matching_logging_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_dir = tmp_path / "custom-logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    _reset_for_tests()

    text = diag_mod.collect_diagnostics()
    assert str(log_dir) in text
