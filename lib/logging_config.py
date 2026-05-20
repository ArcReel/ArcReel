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
    """日志目录解析：ARCREEL_LOG_DIR > app_data_dir()/logs。

    相对路径基于 PROJECT_ROOT，与 app_data_dir 的策略保持一致。
    """
    raw = os.environ.get("ARCREEL_LOG_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return app_data_dir() / "logs"


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
