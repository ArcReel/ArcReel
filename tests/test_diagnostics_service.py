"""diagnostics.collect_diagnostics 行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_collect_returns_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
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
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
    assert "supersecretpassword" not in text
    assert "••" in text
    assert "db.example.com" in text
    assert "arcreel" in text


def test_collect_swallows_field_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    import server.services.diagnostics as diag_mod

    def boom() -> str:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(diag_mod, "_app_version", boom)

    text = diag_mod.collect_diagnostics()
    assert "<unavailable" in text
    assert "Python" in text


def test_collect_returns_log_dir_matching_logging_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_dir = tmp_path / "custom-logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(log_dir))
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()

    from server.services.diagnostics import collect_diagnostics

    text = collect_diagnostics()
    assert str(log_dir) in text
