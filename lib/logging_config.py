"""统一日志配置。"""

import logging
import os

_HANDLER_ATTR = "_arcreel_logging"


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

    # 幂等：避免重复添加 handler
    if any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers):
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_ATTR, True)
    root.addHandler(handler)

    # 统一 uvicorn 的日志格式，避免两种格式并存
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
