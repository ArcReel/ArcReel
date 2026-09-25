"""TimedRotatingFileHandler 注册与降级行为测试。"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

from lib.infra import app_data_dir as app_data_dir_mod
from lib.infra import logging_config


@pytest.fixture(autouse=True)
def reset_root_logger():
    """每个用例前后清空 root logger handlers，避免污染。

    setup_logging() / attach_file_handler() 不止改 root.handlers——还动
    root.level 以及 uvicorn*/aiosqlite 等命名 logger 的 handlers/disabled/
    propagate。teardown 必须把这些都恢复，并 close() 临时挂的 file handler
    以释放 fd（Windows 上 file locking 尤其敏感）。
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    named_loggers = {
        name: logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "aiosqlite")
    }
    saved_named = {
        name: (list(logger.handlers), logger.level, logger.disabled, logger.propagate)
        for name, logger in named_loggers.items()
    }
    root.handlers.clear()
    yield
    # 关闭测试中临时挂的 handler 释放 fd
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers
    for name, logger in named_loggers.items():
        handlers, level, disabled, propagate = saved_named[name]
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.disabled = disabled
        logger.propagate = propagate


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """数据根钉到 tmp_path/data，文件日志应落在 ``<数据根>/logs``。"""
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ARCREEL_LOG_DIR", raising=False)
    monkeypatch.delenv("ARCREEL_LOG_FILE_DISABLED", raising=False)
    app_data_dir_mod.reset_for_tests()
    yield tmp_path / "data" / "logs"
    app_data_dir_mod.reset_for_tests()


def test_file_handler_registered_by_default(isolated_log_dir: Path) -> None:
    logging_config.setup_logging()
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename).parent == isolated_log_dir.resolve()


def test_logs_written_to_file(isolated_log_dir: Path) -> None:
    logging_config.setup_logging()
    logging.getLogger("test.persistence").info("hello-arcreel")
    for h in logging.getLogger().handlers:
        h.flush()
    log_file = isolated_log_dir / "arcreel.log"
    assert log_file.exists()
    assert "hello-arcreel" in log_file.read_text(encoding="utf-8")


def test_mkdir_failure_graceful(isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = isolated_log_dir.resolve()

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


# --- 日志位置 + 旧位置迁入 -----------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path, isolated_log_dir: Path) -> Path:
    """数据根为 tmp_path/data，旧日志位置为 tmp_path/root/logs（代码目录下的 ``logs``，
    Docker 旧卷挂载点同此），经 ``legacy_dir`` 传给迁移。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "root").mkdir()
    return tmp_path


def _migrate(tmp: Path) -> None:
    logging_config.migrate_legacy_log_dir(legacy_dir=tmp / "root" / "logs")


def _write_legacy_logs(old_dir: Path) -> None:
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "arcreel.log").write_text("old content\n", encoding="utf-8")
    (old_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")


def _emit_and_read_log(log_dir: Path) -> str:
    logging.getLogger("test.persistence").info("hello-arcreel")
    for h in logging.getLogger().handlers:
        h.flush()
    return (log_dir / "arcreel.log").read_text(encoding="utf-8")


def test_log_dir_env_is_ignored(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = isolated_data_dir / "custom-logs"
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(custom))

    logging_config.setup_logging()

    assert "hello-arcreel" in _emit_and_read_log(isolated_data_dir / "data" / "logs")
    assert not custom.exists()


def test_log_dir_env_set_warns_once(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ARCREEL_LOG_DIR", str(isolated_data_dir / "custom-logs"))

    with caplog.at_level(logging.WARNING, logger="lib.infra.logging_config"):
        logging_config.warn_if_log_dir_env_set()

    assert [rec.levelno for rec in caplog.records] == [logging.WARNING]


def test_log_dir_env_unset_no_warning(isolated_data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="lib.infra.logging_config"):
        logging_config.warn_if_log_dir_env_set()

    assert caplog.records == []


def test_migrate_moves_legacy_logs_into_data_root(isolated_data_dir: Path) -> None:
    old_dir = isolated_data_dir / "root" / "logs"
    _write_legacy_logs(old_dir)

    _migrate(isolated_data_dir)

    new_dir = isolated_data_dir / "data" / "logs"
    assert not old_dir.exists()
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "old content\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"


def test_migrate_then_attach_appends_to_migrated_log(isolated_data_dir: Path) -> None:
    """启动顺序：先迁入旧日志、再挂 handler，新日志续写在迁入的文件后面。"""
    _write_legacy_logs(isolated_data_dir / "root" / "logs")

    _migrate(isolated_data_dir)
    logging_config.setup_logging()

    content = _emit_and_read_log(isolated_data_dir / "data" / "logs")
    assert content.startswith("old content\n")
    assert "hello-arcreel" in content


def test_migrate_is_rerunnable(isolated_data_dir: Path) -> None:
    _write_legacy_logs(isolated_data_dir / "root" / "logs")

    _migrate(isolated_data_dir)
    _migrate(isolated_data_dir)

    new_dir = isolated_data_dir / "data" / "logs"
    assert sorted(p.name for p in new_dir.iterdir()) == ["arcreel.log", "arcreel.log.2026-05-20"]
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "old content\n"


def test_migrate_merges_into_existing_new_dir_without_overwrite(isolated_data_dir: Path) -> None:
    """新位置已有同名文件时不覆盖：同名条目留在旧位置，其余照常迁入。"""
    old_dir = isolated_data_dir / "root" / "logs"
    new_dir = isolated_data_dir / "data" / "logs"
    _write_legacy_logs(old_dir)
    new_dir.mkdir()
    (new_dir / "arcreel.log").write_text("new\n", encoding="utf-8")

    _migrate(isolated_data_dir)

    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "new\n"
    assert (old_dir / "arcreel.log").read_text(encoding="utf-8") == "old content\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"
    assert not (old_dir / "arcreel.log.2026-05-20").exists()


def test_migrate_empties_mountpoint_that_cannot_be_removed(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker 旧卷 ``./logs:/app/logs``：旧位置是挂载点，rmdir 抛 EBUSY，
    且与数据根跨设备。内容仍要复制进数据根，旧位置被清空。"""
    import errno

    old_dir = isolated_data_dir / "root" / "logs"
    _write_legacy_logs(old_dir)
    (old_dir / "archive").mkdir()
    (old_dir / "archive" / "a.log").write_text("archived\n", encoding="utf-8")

    real_rename = os.rename
    real_rmdir = Path.rmdir

    def fake_rename(src: str, dst: str, *args: object, **kwargs: object) -> None:
        if Path(src).resolve().is_relative_to(old_dir.resolve()):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst, *args, **kwargs)

    def fake_rmdir(self: Path) -> None:
        if self.resolve() == old_dir.resolve():
            raise OSError(errno.EBUSY, "Device or resource busy")
        real_rmdir(self)

    monkeypatch.setattr(os, "rename", fake_rename)
    monkeypatch.setattr(Path, "rmdir", fake_rmdir)

    _migrate(isolated_data_dir)

    new_dir = isolated_data_dir / "data" / "logs"
    assert old_dir.is_dir()
    assert list(old_dir.iterdir()) == []
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "old content\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"
    assert (new_dir / "archive" / "a.log").read_text(encoding="utf-8") == "archived\n"


def test_log_dir_occupied_by_project_is_left_untouched(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """旧布局里 ``<数据根>/logs`` 可能是名为 logs 的项目：本次启动不迁旧日志、不挂文件日志，
    项目目录原样不动，并告警一次。"""
    project = isolated_data_dir / "data" / "logs"
    project.mkdir()
    (project / "project.json").write_text("{}", encoding="utf-8")
    old_dir = isolated_data_dir / "root" / "logs"
    _write_legacy_logs(old_dir)

    with caplog.at_level(logging.WARNING, logger="lib.infra.logging_config"):
        _migrate(isolated_data_dir)
        logging_config.setup_logging()
        logging.getLogger("test.persistence").info("hello-arcreel")

    assert sorted(p.name for p in project.iterdir()) == ["project.json"]
    assert sorted(p.name for p in old_dir.iterdir()) == ["arcreel.log", "arcreel.log.2026-05-20"]
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in logging.getLogger().handlers)
    assert [rec.levelno for rec in caplog.records if rec.name == "lib.infra.logging_config"] == [logging.WARNING]


def test_migrate_noop_when_legacy_absent(isolated_data_dir: Path) -> None:
    _migrate(isolated_data_dir)  # 不抛
    assert not (isolated_data_dir / "data" / "logs").exists()


def test_migrate_noop_when_paths_equal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """数据根就是代码目录时新旧位置相同，原样保留。"""
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    app_data_dir_mod.reset_for_tests()
    try:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "arcreel.log").write_text("hi\n", encoding="utf-8")

        logging_config.migrate_legacy_log_dir(legacy_dir=logs)  # 不抛

        assert (logs / "arcreel.log").read_text(encoding="utf-8") == "hi\n"
    finally:
        app_data_dir_mod.reset_for_tests()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="directory write bits only bind non-root POSIX")
def test_migrate_leaves_read_only_legacy_dir_intact(isolated_data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """旧位置不可写（只读代码目录）：不迁、不在新位置留副本，告警后照常启动；重跑结果相同。"""
    old_dir = isolated_data_dir / "root" / "logs"
    _write_legacy_logs(old_dir)
    old_dir.chmod(0o555)
    try:
        with caplog.at_level(logging.WARNING, logger="lib.infra.logging_config"):
            _migrate(isolated_data_dir)
            _migrate(isolated_data_dir)
    finally:
        old_dir.chmod(0o755)

    assert sorted(p.name for p in old_dir.iterdir()) == ["arcreel.log", "arcreel.log.2026-05-20"]
    assert not (isolated_data_dir / "data" / "logs").exists()
    assert [rec.levelno for rec in caplog.records] == [logging.WARNING, logging.WARNING]


def test_migrate_failure_logs_error(
    isolated_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """迁移失败记 ERROR（日志仍留在旧位置），不中止启动。"""
    import shutil

    old_dir = isolated_data_dir / "root" / "logs"
    _write_legacy_logs(old_dir)

    def fake_move(src: str, dst: str, *args: object, **kwargs: object) -> None:
        raise PermissionError("simulated permission denied")

    monkeypatch.setattr(shutil, "move", fake_move)

    with caplog.at_level(logging.ERROR, logger="lib.infra.logging_config"):
        _migrate(isolated_data_dir)

    assert any(rec.levelno == logging.ERROR for rec in caplog.records)
    assert (old_dir / "arcreel.log").exists()


def test_setup_logging_file_false_skips_file_handler(isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模块导入期用 file=False 时不应挂 file handler、不应 mkdir 新目录。"""
    # 注意：isolated_log_dir 指向的数据根日志目录尚未创建
    log_dir = isolated_log_dir
    assert not log_dir.exists()

    logging_config.setup_logging(file=False)

    root = logging.getLogger()
    assert not any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers)
    assert not log_dir.exists(), "file=False 不应触发 mkdir"


def test_attach_file_handler_is_idempotent(isolated_log_dir: Path) -> None:
    """attach_file_handler() 多次调用只挂一个 file handler。"""
    logging_config.setup_logging(file=False)
    logging_config.attach_file_handler()
    logging_config.attach_file_handler()
    logging_config.attach_file_handler()

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
    assert len(file_handlers) == 1
