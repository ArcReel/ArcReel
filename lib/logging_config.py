"""Unified logging configuration."""

import logging
import os

_HANDLER_ATTR = "_arcreel_logging"


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger.

    Args:
        level: Log level string (DEBUG/INFO/WARNING/ERROR).
               If not provided, read from the LOG_LEVEL environment variable, defaulting to INFO.
    """
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Idempotent: avoid adding handlers more than once
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

    # Unify uvicorn's log format to avoid two formats coexisting
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # Disable uvicorn.access: request logging is handled centrally by app.py middleware
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True

    # Suppress aiosqlite DEBUG noise (two log lines per SQL operation)
    logging.getLogger("aiosqlite").setLevel(max(numeric_level, logging.INFO))
