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


def test_resolve_log_dir_relative_path_resolves_against_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径 ARCREEL_LOG_DIR 必须基于 PROJECT_ROOT 解析。"""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.setattr(logging_config, "PROJECT_ROOT", project_root)
    monkeypatch.setenv("ARCREEL_LOG_DIR", "var/log/arcreel")

    assert logging_config.resolve_log_dir() == project_root / "var" / "log" / "arcreel"


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


def test_migrate_skips_when_both_have_content(isolated_data_dir: Path) -> None:
    """新旧都有内容时不动，避免静默覆盖。"""
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


def test_migrate_proceeds_when_new_dir_empty(isolated_data_dir: Path) -> None:
    """docker bind-mount 预创建场景：new_dir 是空目录时仍要搬旧目录过来。

    docker-compose 的 ``./logs:/app/logs`` 会让 docker 启动时把宿主机 ``./logs``
    创建为空目录。若 ``new_dir.exists()`` 直接放弃迁移，旧 logs 会一直留在
    projects/logs 下被当作伪项目枚举——这是升级路径的核心回归用例。
    """
    old_dir = isolated_data_dir / "data" / "logs"
    new_dir = isolated_data_dir / "root" / "logs"
    old_dir.mkdir()
    new_dir.mkdir()  # 模拟 docker 预创建的空 mount point
    (old_dir / "arcreel.log").write_text("payload\n", encoding="utf-8")
    (old_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")

    logging_config.migrate_legacy_log_dir()

    assert not old_dir.exists(), "旧目录应已被搬走"
    # new_dir 本身（挂载点）不能被 rmdir，但内容已被填入
    assert new_dir.exists(), "new_dir 必须保留（bind-mount 挂载点不能 rmdir）"
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "payload\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"


def test_migrate_preserves_new_dir_when_it_is_mountpoint(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """new_dir 是 docker bind-mount 挂载点时，rmdir(new_dir) 会抛 EBUSY。
    迁移必须不依赖 rmdir(new_dir) 成功——通过 mock 让任何对 new_dir 自身
    的 rmdir 都抛 OSError(EBUSY)，确认搬运仍能完成。
    """
    import errno

    old_dir = isolated_data_dir / "data" / "logs"
    new_dir = isolated_data_dir / "root" / "logs"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "arcreel.log").write_text("payload\n", encoding="utf-8")
    (old_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")

    real_rmdir = Path.rmdir

    def fake_rmdir(self: Path) -> None:
        # 任何对 new_dir 路径的 rmdir 都模拟成挂载点失败
        if self.resolve() == new_dir.resolve():
            raise OSError(errno.EBUSY, "Device or resource busy")
        real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", fake_rmdir)

    logging_config.migrate_legacy_log_dir()

    # 即使 new_dir.rmdir 全程会失败，迁移仍要完成
    assert not old_dir.exists()
    assert new_dir.exists()
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "payload\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"


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


def test_migrate_falls_back_to_copy_on_exdev(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """跨 mount 场景（docker bind-mount）下 os.rename 抛 EXDEV，shutil.move 自动降级 copy+unlink。"""
    import errno
    import os

    old_dir = isolated_data_dir / "data" / "logs"
    old_dir.mkdir()
    (old_dir / "arcreel.log").write_text("payload\n", encoding="utf-8")
    (old_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")

    real_rename = os.rename

    def fake_rename(src: str, dst: str, *args: object, **kwargs: object) -> None:
        # 只对老 logs dir 的根 rename 制造 EXDEV，其他路径（如 shutil 内部的临时操作）
        # 不受影响
        if str(src) == str(old_dir):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", fake_rename)

    logging_config.migrate_legacy_log_dir()

    new_dir = isolated_data_dir / "root" / "logs"
    assert not old_dir.exists(), "shutil.move 应在跨设备时自动 copy+unlink"
    assert (new_dir / "arcreel.log").read_text(encoding="utf-8") == "payload\n"
    assert (new_dir / "arcreel.log.2026-05-20").read_text(encoding="utf-8") == "rotated\n"


def test_migrate_failure_logs_error(
    isolated_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """迁移失败必须 ERROR 级（不是 WARNING）以让 operator 看到 logs 卡在旧位置。"""
    import shutil

    old_dir = isolated_data_dir / "data" / "logs"
    old_dir.mkdir()
    (old_dir / "arcreel.log").write_text("stuck\n", encoding="utf-8")

    def fake_move(src: str, dst: str, *args: object, **kwargs: object) -> None:
        raise PermissionError("simulated permission denied")

    monkeypatch.setattr(shutil, "move", fake_move)

    with caplog.at_level(logging.ERROR, logger="lib.logging_config"):
        logging_config.migrate_legacy_log_dir()

    assert any("FAILED" in rec.message and rec.levelno == logging.ERROR for rec in caplog.records)
    # 旧 dir 仍在
    assert (old_dir / "arcreel.log").exists()


def test_setup_logging_file_false_skips_file_handler(isolated_log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模块导入期用 file=False 时不应挂 file handler、不应 mkdir 新目录。"""
    # 注意：isolated_log_dir 把 ARCREEL_LOG_DIR 设到 tmp_path/logs 但还没创建
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
