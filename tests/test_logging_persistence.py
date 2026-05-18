"""TimedRotatingFileHandler 注册与降级行为测试。"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from lib import logging_config


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """每个用例前后清空 root logger handlers，避免污染。"""
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("ARCREEL_LOG_FILE_DISABLED", raising=False)
    return tmp_path / "logs"


def test_file_handler_registered_by_default(isolated_log_dir: Path) -> None:
    logging_config.setup_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename).parent == isolated_log_dir.resolve()


def test_file_handler_disabled_by_env(isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_LOG_FILE_DISABLED", "1")
    logging_config.setup_logging()
    root = logging.getLogger()
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)


def test_logs_written_to_file(isolated_log_dir: Path) -> None:
    logging_config.setup_logging()
    logging.getLogger("test.persistence").info("hello-arcreel")
    for h in logging.getLogger().handlers:
        h.flush()
    log_file = isolated_log_dir / "arcreel.log"
    assert log_file.exists()
    assert "hello-arcreel" in log_file.read_text(encoding="utf-8")


def test_mkdir_failure_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "blocked" / "logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(target))
    monkeypatch.delenv("ARCREEL_LOG_FILE_DISABLED", raising=False)

    real_mkdir = Path.mkdir

    def fake_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise PermissionError("simulated read-only fs")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    logging_config.setup_logging()  # 不抛
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)


def test_idempotent(isolated_log_dir: Path) -> None:
    logging_config.setup_logging()
    logging_config.setup_logging()
    logging_config.setup_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes"])
def test_disabled_env_accepts_aliases(isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ARCREEL_LOG_FILE_DISABLED", value)
    logging_config.setup_logging()
    root = logging.getLogger()
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)
