"""统一日志配置。"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from lib.app_data_dir import app_data_dir
from lib.env_init import PROJECT_ROOT

_HANDLER_ATTR = "_arcreel_logging"
_FILE_HANDLER_ATTR = "_arcreel_file_logging"
_DISABLED_TRUTHY = frozenset({"1", "true", "yes"})


def _file_logging_disabled() -> bool:
    return os.environ.get("ARCREEL_LOG_FILE_DISABLED", "").strip().lower() in _DISABLED_TRUTHY


def resolve_log_dir() -> Path:
    """日志目录解析：ARCREEL_LOG_DIR > PROJECT_ROOT/logs。

    相对路径基于 PROJECT_ROOT。

    日志目录刻意不放在 app_data_dir() 里：app_data_dir() 同时承担 projects_root
    的身份，project 枚举走的是 `.`/`_` 前缀负向过滤，任何无前缀的兄弟目录都会被
    当作项目暴露给前端。logs 走独立的 PROJECT_ROOT/logs，从源头消除这条歧义。
    """
    raw = os.environ.get("ARCREEL_LOG_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return PROJECT_ROOT / "logs"


def legacy_log_dir() -> Path:
    """旧默认路径（app_data_dir()/logs），用于一次性启动迁移。"""
    return app_data_dir() / "logs"


def migrate_legacy_log_dir() -> None:
    """将旧默认位置的日志迁到新位置；只在 ARCREEL_LOG_DIR 未显式覆盖时进行。

    策略：
    - 用户显式设了 ARCREEL_LOG_DIR → 不动（用户已自主决定路径）
    - 新旧路径解析到同一处 → no-op（例如 ARCREEL_DATA_DIR == PROJECT_ROOT）
    - 旧目录不存在 → no-op
    - 新目录不存在 → 整体 rename 旧→新
    - 新旧都存在 → 警告，不动（避免静默覆盖；让用户自己处置）
    - 任意异常 → warning，不抛（迁移辅助逻辑不阻塞启动）
    """
    if os.environ.get("ARCREEL_LOG_DIR", "").strip():
        return

    logger = logging.getLogger(__name__)
    try:
        old_dir = legacy_log_dir()
        new_dir = resolve_log_dir()

        if old_dir.resolve() == new_dir.resolve():
            return
        if not old_dir.exists():
            return
        if new_dir.exists():
            logger.warning(
                "legacy log dir %s and new log dir %s both exist; leaving both in place — please move/delete manually",
                old_dir,
                new_dir,
            )
            return

        new_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old_dir, new_dir)
        logger.info("migrated legacy log dir %s → %s", old_dir, new_dir)
    except Exception as exc:
        logger.warning("legacy log dir migration skipped: %s", exc)


def setup_logging(level: str | None = None) -> None:
    """配置根 logger。

    Args:
        level: 日志级别字符串（DEBUG/INFO/WARNING/ERROR）。
               如未提供，从环境变量 LOG_LEVEL 读取，默认 INFO。
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

    # 文件 handler：默认开启，按天切，保留 7 份。失败不阻塞 stdout。
    file_handler_exists = any(getattr(h, _FILE_HANDLER_ATTR, False) for h in root.handlers)
    if not _file_logging_disabled() and not file_handler_exists:
        try:
            log_dir = resolve_log_dir()
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
