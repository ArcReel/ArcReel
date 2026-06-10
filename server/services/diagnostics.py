"""收集脱敏后的系统诊断信息，供 /system/logs/download 打包。"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from lib.app_data_dir import app_data_dir
from lib.logging_config import resolve_log_dir
from lib.logging_utils import _redact_value

_UNAVAILABLE = "<unavailable: {exc}>"


def _safe(fn: Callable[[], object], label: str) -> str:
    try:
        return str(fn())
    except Exception as exc:
        return _UNAVAILABLE.format(exc=f"{label}: {exc}")


def _app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("arcreel")
    except PackageNotFoundError:
        pass

    try:
        import tomllib

        from lib.env_init import PROJECT_ROOT

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as f:
            data = tomllib.load(f)
        return str(data.get("project", {}).get("version", "<unknown>"))
    except Exception:
        return "<unknown>"


def _python_version() -> str:
    return sys.version.replace("\n", " ")


def _os_info() -> str:
    return platform.platform()


def _data_dir() -> str:
    return str(app_data_dir())


def _log_dir() -> str:
    return str(resolve_log_dir())


_SENSITIVE_QUERY_KEYS = frozenset({"password", "passwd", "pwd", "token", "secret", "api_key", "apikey"})


def _db_url() -> str:
    raw = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./projects/.arcreel.db")
    try:
        parsed = urlparse(raw)
        netloc = parsed.netloc
        if parsed.username or parsed.password:
            user = _redact_value(parsed.username) if parsed.username else ""
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{user}:••@{host}{port}" if parsed.password else f"{user}@{host}{port}"

        query = parsed.query
        if query:
            masked = [
                (k, "••" if k.lower() in _SENSITIVE_QUERY_KEYS else v)
                for k, v in parse_qsl(query, keep_blank_values=True)
            ]
            query = urlencode(masked)

        return urlunparse(parsed._replace(netloc=netloc, query=query))
    except Exception:
        # 脱敏失败时回退到原始字符串，避免诊断包完全失败；调用方 _safe 会再兜一层。
        return raw


def _log_level() -> str:
    return os.environ.get("LOG_LEVEL", "INFO")


def _sandbox_status() -> str:
    from server.app import check_sandbox_available

    return "enabled" if check_sandbox_available() else "disabled"


def _providers() -> str:
    from lib.config.registry import PROVIDER_REGISTRY

    ids = sorted(PROVIDER_REGISTRY.keys())
    return ", ".join(ids) if ids else "<none>"


def collect_diagnostics() -> str:
    """返回脱敏的 plain-text 诊断报告。任一字段失败用 <unavailable> 占位，整体不抛。"""
    fields: list[tuple[str, Callable[[], object]]] = [
        ("App version", _app_version),
        ("Python", _python_version),
        ("OS", _os_info),
        ("Data directory", _data_dir),
        ("Log directory", _log_dir),
        ("Database URL", _db_url),
        ("Log level", _log_level),
        ("Sandbox", _sandbox_status),
        ("Registered providers", _providers),
        ("Report generated", lambda: datetime.now(UTC).isoformat()),
    ]

    lines = ["ArcReel diagnostics", "=" * 40]
    for label, fn in fields:
        lines.append(f"{label}: {_safe(fn, label)}")
    return "\n".join(lines) + "\n"
