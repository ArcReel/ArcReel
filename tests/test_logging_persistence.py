"""TimedRotatingFileHandler 注册与降级行为测试。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from lib import app_data_dir as app_data_dir_mod
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


# --- resolve_log_dir 默认路径 + 一次性迁移 -------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把 app_data_dir() 与 PROJECT_ROOT 都钉到 tmp_path 下的独立子目录。

    使新旧默认路径分别落在 tmp_path/data/logs（旧）与 tmp_path/root/logs（新），
    便于断言迁移是否搬动了文件。
    """
    data_root = tmp_path / "data"
    project_root = tmp_path / "root"
    data_root.mkdir()
    project_root.mkdir()
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    monkeypatch.delenv("ARCREEL_LOG_DIR", raising=False)
    monkeypatch.setattr(logging_config, "PROJECT_ROOT", project_root)
    app_data_dir_mod._reset_for_tests()
    yield tmp_path
    app_data_dir_mod._reset_for_tests()


def test_resolve_log_dir_default_is_project_root(isolated_data_dir: Path) -> None:
    assert logging_config.resolve_log_dir() == isolated_data_dir / "root" / "logs"


def test_resolve_log_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom-logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(target))
    assert logging_config.resolve_log_dir() == target


def test_legacy_log_dir_points_to_app_data(isolated_data_dir: Path) -> None:
    assert logging_config.legacy_log_dir() == (isolated_data_dir / "data" / "logs").resolve()


def test_migrate_moves_legacy_dir_when_new_absent(isolated_data_dir: Path) -> None:
    old_dir = isolated_data_dir / "data" / "logs"
    old_dir.mkdir()
    (old_dir / "arcreel.log").write_text("old content\n", encoding="utf-8")
    (old_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")

    logging_config.migrate_legacy_log_dir()

    new_dir = isolated_data_dir / "root" / "logs"
    assert not old_dir.exists()
    assert new_dir.exists()
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "old content\n"
    assert (new_dir / "arcreel.log.2026-05-20").exists()


def test_migrate_skips_when_both_exist(isolated_data_dir: Path) -> None:
    old_dir = isolated_data_dir / "data" / "logs"
    new_dir = isolated_data_dir / "root" / "logs"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "arcreel.log").write_text("old\n", encoding="utf-8")
    (new_dir / "arcreel.log").write_text("new\n", encoding="utf-8")

    logging_config.migrate_legacy_log_dir()

    # 两边都原样保留，不静默覆盖
    assert (old_dir / "arcreel.log").read_text(encoding="utf-8") == "old\n"
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "new\n"


def test_migrate_noop_when_legacy_absent(isolated_data_dir: Path) -> None:
    logging_config.migrate_legacy_log_dir()  # 不抛
    assert not (isolated_data_dir / "root" / "logs").exists()


def test_migrate_skips_when_log_dir_env_set(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old_dir = isolated_data_dir / "data" / "logs"
    old_dir.mkdir()
    (old_dir / "arcreel.log").write_text("keep me\n", encoding="utf-8")
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(isolated_data_dir / "custom"))

    logging_config.migrate_legacy_log_dir()

    # 用户显式设了 LOG_DIR，旧目录原地保留
    assert (old_dir / "arcreel.log").read_text(encoding="utf-8") == "keep me\n"


def test_migrate_noop_when_paths_equal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCREEL_DATA_DIR == PROJECT_ROOT 时旧新路径解析到同一处，不要把目录自己 rename 到自己。"""
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ARCREEL_LOG_DIR", raising=False)
    monkeypatch.setattr(logging_config, "PROJECT_ROOT", tmp_path)
    app_data_dir_mod._reset_for_tests()
    try:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "arcreel.log").write_text("hi\n", encoding="utf-8")

        logging_config.migrate_legacy_log_dir()  # 不抛

        assert (logs / "arcreel.log").read_text(encoding="utf-8") == "hi\n"
    finally:
        app_data_dir_mod._reset_for_tests()
