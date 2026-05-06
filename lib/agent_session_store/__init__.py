"""Agent SessionStore — SDK transcript mirror to project DB."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary

_ENV_VAR = "ARCREEL_SDK_SESSION_STORE"
_VALID_MODES = frozenset({"db", "off", ""})

logger = logging.getLogger("arcreel.session_store.config")

FlushMode = Literal["eager", "batched"]

_FLUSH_ENV_VAR = "ARCREEL_SDK_SESSION_STORE_FLUSH"
_VALID_FLUSH_MODES = frozenset({"eager", "batched"})


def make_project_key(project_cwd: Path | str) -> str:
    """Derive the SessionStore project_key for a project cwd.

    Thin wrapper around SDK's public ``project_key_for_directory`` so adapter
    callers and SDK live-mirror writes agree on the key. SDK import is local
    so module-level callers (`session_store_enabled` in app.py lifespan) stay
    importable when claude_agent_sdk is missing.
    """
    from claude_agent_sdk import project_key_for_directory

    return project_key_for_directory(str(project_cwd))


def session_store_enabled() -> bool:
    """True when ARCREEL_SDK_SESSION_STORE is anything but 'off' (case-insensitive).

    Single source of truth for the kill-switch parsed at every read site
    (lifespan migration, SessionManager). Unknown values default to enabled
    with a warning in callers that care to log it.
    """
    mode = os.getenv(_ENV_VAR, "db").strip().lower()
    return mode != "off"


def session_store_mode() -> str:
    """Return the raw normalized mode string (`db`, `off`, or other)."""
    return os.getenv(_ENV_VAR, "db").strip().lower()


def is_known_session_store_mode(mode: str) -> bool:
    return mode in _VALID_MODES


def session_store_flush_mode() -> FlushMode:
    """Return SDK ClaudeAgentOptions.session_store_flush value.

    Defaults to "eager" so transcript writes are durable across crashes
    and visible mid-turn for reconnect snapshots. Set
    ARCREEL_SDK_SESSION_STORE_FLUSH=batched for the legacy end-of-turn
    flush behavior (rollback path).
    """
    raw = os.getenv(_FLUSH_ENV_VAR, "").strip().lower()
    if raw == "batched":
        return "batched"
    if raw and raw not in _VALID_FLUSH_MODES:
        logger.warning(
            "Unknown %s=%r; defaulting to eager",
            _FLUSH_ENV_VAR,
            raw,
        )
    return "eager"


__all__ = [
    "AgentSessionEntry",
    "AgentSessionSummary",
    "is_known_session_store_mode",
    "make_project_key",
    "session_store_enabled",
    "session_store_flush_mode",
    "session_store_mode",
]
