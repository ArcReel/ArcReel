"""统一日志配置。"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from lib.infra.data_root_layout import PROJECT_FILENAME, DataRootLayout, is_project_dir
from lib.infra.env_init import PROJECT_ROOT

_HANDLER_ATTR = "_arcreel_logging"
_FILE_HANDLER_ATTR = "_arcreel_file_logging"
_DISABLED_TRUTHY = frozenset({"1", "true", "yes"})


def _file_logging_disabled() -> bool:
    return os.environ.get("ARCREEL_LOG_FILE_DISABLED", "").strip().lower() in _DISABLED_TRUTHY


def resolve_log_dir() -> Path:
    """文件日志目录：``<数据根>/logs``（``DataRootLayout.log_dir``）。"""
    return DataRootLayout.current().log_dir


LEGACY_LOG_DIR = PROJECT_ROOT / "logs"
"""代码目录下的旧日志位置；Docker 旧卷 ``./logs:/app/logs`` 的挂载点同在此处。"""


def warn_if_log_dir_env_set() -> None:
    """日志位置不读取 ``ARCREEL_LOG_DIR``；它仍被设置时提示一次日志的实际去向。"""
    raw = os.environ.get("ARCREEL_LOG_DIR", "").strip()
    if not raw:
        return
    destination = (
        "file logging is disabled by ARCREEL_LOG_FILE_DISABLED"
        if _file_logging_disabled()
        else f"file logs are written to {resolve_log_dir()}"
    )
    logging.getLogger(__name__).warning(
        "ARCREEL_LOG_DIR=%s no longer takes effect; %s. Existing files under %s are not moved.",
        raw,
        destination,
        raw,
    )


def migrate_legacy_log_dir(*, legacy_dir: Path = LEGACY_LOG_DIR) -> None:
    """把旧位置的日志逐项迁入数据根下的日志目录；须在挂文件日志 handler 之前调用。

    - 新旧位置相同、互相包含或旧位置不存在 → 不动
    - 旧位置不可写（只读代码目录）→ 告警，原样保留，不做只复制不删源的半截迁移
    - 逐项 ``shutil.move``：同设备 rename，跨设备（Docker 旧卷）复制后删源
    - 新位置已有同名条目 → 该条目留在旧位置并告警，不覆盖；重跑只会再次跳过它
    - 单个条目迁移失败 → 该条目留在旧位置并记 ERROR，其余条目照常迁入
    - 旧位置清空后尝试删除；删不掉（挂载点 EBUSY）就留下空目录
    - 新位置被项目占着 → 不动（告警由 :func:`attach_file_handler` 记）
    - OSError 记 ERROR，不中止启动：日志留在旧位置不影响数据根布局
    """
    logger = logging.getLogger(__name__)
    old_dir = legacy_dir
    new_dir = resolve_log_dir()
    try:
        if not old_dir.is_dir() or is_project_dir(new_dir):
            return
        old_resolved, new_resolved = old_dir.resolve(), new_dir.resolve()
        if old_resolved == new_resolved:
            return
        if new_resolved.is_relative_to(old_resolved) or old_resolved.is_relative_to(new_resolved):
            logger.warning(
                "legacy log dir %s and log dir %s contain each other; leaving both in place", old_dir, new_dir
            )
            return
        if not os.access(old_dir, os.W_OK | os.X_OK):
            logger.warning(
                "legacy log dir %s is not writable; leaving it in place, copy its files to %s manually if needed",
                old_dir,
                new_dir,
            )
            return
        new_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for entry in sorted(old_dir.iterdir()):
            target = new_dir / entry.name
            if target.exists() or target.is_symlink():
                logger.warning(
                    "legacy log entry %s not migrated: %s already exists; please merge manually", entry, target
                )
                continue
            try:
                shutil.move(entry, target)
            except OSError as exc:
                logger.error("legacy log entry %s not migrated to %s: %s; please move it manually", entry, target, exc)
                continue
            moved += 1
        if moved:
            logger.info("migrated %d legacy log entries %s -> %s", moved, old_dir, new_dir)
        with contextlib.suppress(OSError):
            old_dir.rmdir()
    except OSError as exc:
        logger.error(
            "legacy log dir migration FAILED (logs remain at %s; please move them to %s manually): %s",
            old_dir,
            new_dir,
            exc,
        )


def setup_logging(level: str | None = None, *, file: bool = True) -> None:
    """配置根 logger。

    Args:
        level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）。
               如未提供，从环境变量 LOG_LEVEL 读取，默认 INFO。
        file: 是否挂 TimedRotatingFileHandler。模块导入期传 False 推迟到
              lifespan 之后挂——避免 import 期在数据根下创建 logs/，
              也让 migrate_legacy_log_dir() 先于 handler 把旧的同名日志文件迁入。
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 幂等：避免重复添加 stream handler
    if not any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_ATTR, True)
        root.addHandler(handler)

    if file:
        attach_file_handler(formatter)

    # 统一 uvicorn 的日志格式，避免两种格式并存
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # 禁用 uvicorn.access：请求日志由 app.py 的 middleware 统一处理
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True

    # 抑制 aiosqlite 的 DEBUG 噪音（每次 SQL 操作都会输出两行日志）
    logging.getLogger("aiosqlite").setLevel(max(numeric_level, logging.INFO))


def attach_file_handler(formatter: logging.Formatter | None = None) -> None:
    """为 root logger 挂 TimedRotatingFileHandler（默认开启，按天切，保留 7 份）。

    幂等：已挂则直接返回。被 setup_logging 调用，也可在 lifespan 内
    单独触发——后者用于先跑 migrate_legacy_log_dir() 迁入旧日志，再挂
    file handler，避免 handler 先创建 arcreel.log 使旧的同名文件留在原处。
    """
    if _file_logging_disabled():
        return

    root = logging.getLogger()
    if any(getattr(h, _FILE_HANDLER_ATTR, False) for h in root.handlers):
        return

    if formatter is None:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    try:
        log_dir = resolve_log_dir()
        # 日志目录位置上是一个项目时不往里写任何东西。
        if is_project_dir(log_dir):
            logging.getLogger(__name__).warning(
                "file logging disabled for this run: %s contains %s; "
                "file logging resumes once that directory no longer holds a project",
                log_dir,
                PROJECT_FILENAME,
            )
            return
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(log_dir / "arcreel.log"),
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            utc=False,
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _FILE_HANDLER_ATTR, True)
        root.addHandler(file_handler)
    except Exception as exc:
        logging.getLogger(__name__).warning("file logging disabled: %s", exc)
