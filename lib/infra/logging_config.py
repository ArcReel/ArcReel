"""Shared logging configuration."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import httpx

from lib.infra.data_root_layout import PROJECT_FILENAME, DataRootLayout, is_project_dir
from lib.infra.env_init import PROJECT_ROOT

_HANDLER_ATTR = "_arcreel_logging"
_FILE_HANDLER_ATTR = "_arcreel_file_logging"
_DISABLED_TRUTHY = frozenset({"1", "true", "yes"})


class _StripUrlQueryFilter(logging.Filter):
    """httpx logs complete request URLs at INFO. Presigned query strings act as download credentials while valid; remove queries before logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and any(isinstance(arg, httpx.URL) and arg.query for arg in args):
            record.args = tuple(
                arg.copy_with(query=None) if isinstance(arg, httpx.URL) and arg.query else arg for arg in args
            )
        return True


def _install_httpx_url_redaction() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _StripUrlQueryFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_StripUrlQueryFilter())


def _file_logging_disabled() -> bool:
    return os.environ.get("ARCREEL_LOG_FILE_DISABLED", "").strip().lower() in _DISABLED_TRUTHY


def resolve_log_dir() -> Path:
    """File-log directory: ``<数据根>/logs`` (``DataRootLayout.log_dir``)."""
    return DataRootLayout.current().log_dir


LEGACY_LOG_DIR = PROJECT_ROOT / "logs"
"""Legacy log location under the code directory; the old Docker volume ``./logs:/app/logs`` mounts here too."""


def warn_if_log_dir_env_set() -> None:
    """``ARCREEL_LOG_DIR`` does not control log placement; warn once with the actual destination if it is still set."""
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
    """Move legacy logs individually into the data-root log directory before attaching the file handler.

    - Same/containing paths or absent old location: do nothing.
    - Unwritable old location (read-only source): warn and preserve it; never half-migrate by copying without removing sources.
    - Use ``shutil.move`` per entry: rename on one device, copy/delete across devices such as old Docker volumes.
    - Same-name target entries: preserve the old entry and warn, without overwriting; reruns skip it again.
    - An entry fails: leave it at the old location, log ERROR and continue with others.
    - Once empty, try removing the old directory; retain it if removal fails, such as EBUSY on a mount point.
    - A project occupies the destination: do nothing; :func:`attach_file_handler` logs the warning.
    - Log OSError as ERROR without aborting startup: old-location logs do not affect the data-root layout.
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
            old_dir.rmdir()  # hard-delete: infra -- empty legacy log directory after migration
    except OSError as exc:
        logger.error(
            "legacy log dir migration FAILED (logs remain at %s; please move them to %s manually): %s",
            old_dir,
            new_dir,
            exc,
        )


def setup_logging(level: str | None = None, *, file: bool = True) -> None:
    """Configure the root logger.

    Args:
        level: DEBUG/INFO/WARNING/ERROR string. If absent, read LOG_LEVEL, default INFO.
        file: Whether to attach TimedRotatingFileHandler. Use False during imports
            and defer until after lifespan, avoiding import-time logs/ creation in
            the data root and allowing migrate_legacy_log_dir() to move same-name
            old logs before the handler creates them.
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

    # Idempotent: do not add duplicate stream handlers.
    if not any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_ATTR, True)
        root.addHandler(handler)

    if file:
        attach_file_handler(formatter)

    # Use the same format for uvicorn logs rather than mixing two formats.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # Disable uvicorn.access; app.py middleware owns request logging.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True

    # Suppress aiosqlite DEBUG noise (two lines per SQL operation).
    logging.getLogger("aiosqlite").setLevel(max(numeric_level, logging.INFO))

    # TOS SDK INFO retries include request bodies (uploaded object content); DEBUG logs signing;
    # keep media and signatures out of ordinary diagnostics by allowing only WARNING and above.
    logging.getLogger("tos").setLevel(max(numeric_level, logging.WARNING))

    # Provider asset/artifact download URLs include signatures; request logs retain only the query-free URL.
    _install_httpx_url_redaction()


def attach_file_handler(formatter: logging.Formatter | None = None) -> None:
    """Attach TimedRotatingFileHandler to the root logger (enabled by default, daily rotation, 7 backups).

    Idempotent: return if already attached. Called by setup_logging or separately in
    lifespan, where migrate_legacy_log_dir() runs first; otherwise an early handler
    could create arcreel.log and leave the same-name old file behind.
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
        # Write nothing when a project occupies the log-directory location.
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
