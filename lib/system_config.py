"""
Global system configuration manager.

.. deprecated::
    SystemConfigManager and related functions are deprecated. Configuration is
    now stored in the database via lib.config (ConfigService + repositories).
    JSON migration is handled automatically at startup by lib.config.migration.
    This module is retained for utility functions (parse_bool_env,
    resolve_vertex_credentials_path) and for test backward-compatibility only.

This module intentionally avoids importing lib/__init__.py to prevent circular
imports during early environment initialization.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_MANAGERS: dict[str, SystemConfigManager] = {}
_MANAGERS_LOCK = threading.Lock()


def _project_root_key(project_root: Path) -> str:
    try:
        return str(project_root.resolve())
    except OSError:
        return str(project_root)


def get_system_config_manager(project_root: Path) -> SystemConfigManager:
    """Return a cached SystemConfigManager for *project_root*.

    .. deprecated::
        Use lib.config.service.ConfigService with a DB session instead.
    """
    import warnings

    warnings.warn(
        "get_system_config_manager() is deprecated. Use lib.config.service.ConfigService instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    key = _project_root_key(project_root)
    with _MANAGERS_LOCK:
        existing = _MANAGERS.get(key)
        if existing is not None:
            return existing
        manager = SystemConfigManager(project_root=project_root)
        _MANAGERS[key] = manager
        return manager


def init_and_apply_system_config(project_root: Path) -> SystemConfigManager:
    """Initialize (cached) manager and apply overrides to the process env.

    .. deprecated::
        Use lib.config.migration.migrate_json_to_db() at startup instead.
        The app lifespan in server/app.py handles this automatically.
    """
    import warnings

    warnings.warn(
        "init_and_apply_system_config() is deprecated. JSON→DB migration is now "
        "handled automatically at app startup via lib.config.migration.",
        DeprecationWarning,
        stacklevel=2,
    )
    key = _project_root_key(project_root)
    with _MANAGERS_LOCK:
        existing = _MANAGERS.get(key)
        if existing is None:
            existing = SystemConfigManager(project_root=project_root)
            _MANAGERS[key] = existing
    existing.apply()
    return existing


def _iso_now_millis() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="milliseconds")


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def parse_bool_env(value: Any, default: bool) -> bool:
    """Parse a bool-like env/config value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    return default


def _read_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _read_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class SystemConfigPaths:
    config_path: Path
    vertex_credentials_path: Path


def resolve_vertex_credentials_path(project_root: Path) -> Path | None:
    """
    Resolve the Vertex credentials JSON file to use.

    Prefers `vertex_keys/vertex_credentials.json` and falls back to the first
    `*.json` file in `vertex_keys/` for backward compatibility.
    """
    project_root = Path(project_root)
    credentials_dir = project_root / "vertex_keys"
    preferred = credentials_dir / "vertex_credentials.json"
    if preferred.exists():
        return preferred
    if not credentials_dir.exists():
        return None
    candidates = sorted(credentials_dir.glob("*.json"))
    return candidates[0] if candidates else None


class SystemConfigManager:
    """Manages global system configuration overrides and env application.

    .. deprecated::
        Use lib.config.service.ConfigService with a DB session instead.
        This class is retained for backward-compatibility with existing tests only.
    """

    _ENV_KEYS = (
        "GEMINI_IMAGE_BACKEND",
        "GEMINI_VIDEO_BACKEND",
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "GEMINI_IMAGE_MODEL",
        "GEMINI_VIDEO_MODEL",
        "GEMINI_VIDEO_GENERATE_AUDIO",
        "GEMINI_IMAGE_RPM",
        "GEMINI_VIDEO_RPM",
        "GEMINI_REQUEST_GAP",
        "IMAGE_MAX_WORKERS",
        "VIDEO_MAX_WORKERS",
        "VERTEX_GCS_BUCKET",
        "DEFAULT_VIDEO_PROVIDER",
        "ARK_API_KEY",
        "FILE_SERVICE_BASE_URL",
        "XAI_API_KEY",
    )

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.paths = SystemConfigPaths(
            config_path=(self.project_root / "projects" / ".system_config.json"),
            vertex_credentials_path=(self.project_root / "vertex_keys" / "vertex_credentials.json"),
        )
        self._lock = threading.Lock()
        self._baseline_env = {key: os.environ.get(key) for key in self._ENV_KEYS}

    # ------------------------------------------------------------------
    # IO helpers
    # ------------------------------------------------------------------

    def _load_file(self) -> tuple[dict[str, Any], bool]:
        """Return (data, migrated)."""
        if not self.paths.config_path.exists():
            return {"version": 1, "updated_at": None, "overrides": {}}, False

        try:
            raw = self.paths.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            # TODO(multi-user): JSONDecodeError messages may contain config file fragments (including API keys);
            # log content must be sanitised in multi-user scenarios.
            logger.warning("Failed to read system config, using empty overrides: %s", exc)
            return {"version": 1, "updated_at": None, "overrides": {}}, False

        if not isinstance(data, dict):
            return {"version": 1, "updated_at": None, "overrides": {}}, False

        overrides = data.get("overrides")
        if not isinstance(overrides, dict):
            overrides = {}

        migrated = False
        # Migration: gemini_backend -> image_backend/video_backend
        legacy_backend = overrides.get("gemini_backend")
        if isinstance(legacy_backend, str) and legacy_backend.strip():
            if "image_backend" not in overrides:
                overrides["image_backend"] = legacy_backend.strip()
            if "video_backend" not in overrides:
                overrides["video_backend"] = legacy_backend.strip()
            overrides.pop("gemini_backend", None)
            migrated = True

        # Migration: storyboard_max_workers -> image_max_workers
        legacy_workers = overrides.get("storyboard_max_workers")
        if legacy_workers is not None:
            if "image_max_workers" not in overrides:
                overrides["image_max_workers"] = legacy_workers
            overrides.pop("storyboard_max_workers", None)
            migrated = True

        # Migration: AI Studio old 001 suffix -> preview (001 is Vertex-only)
        _model_migration = {
            "veo-3.1-generate-001": "veo-3.1-generate-preview",
            "veo-3.1-fast-generate-001": "veo-3.1-fast-generate-preview",
        }
        for key in ("image_model", "video_model"):
            old_val = overrides.get(key)
            if isinstance(old_val, str) and old_val in _model_migration:
                overrides[key] = _model_migration[old_val]
                migrated = True

        data["version"] = int(data.get("version") or 1)
        data["overrides"] = overrides
        return data, migrated

    def _save_file(self, data: dict[str, Any]) -> None:
        self.paths.config_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": int(data.get("version") or 1),
            "updated_at": _iso_now_millis(),
            "overrides": data.get("overrides") or {},
        }

        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        dir_path = self.paths.config_path.parent
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(dir_path),
            prefix=".system_config.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(serialized)
            tmp_path = Path(tmp.name)

        os.replace(tmp_path, self.paths.config_path)
        try:
            os.chmod(self.paths.config_path, 0o600)
        except OSError as exc:
            logger.debug(
                "Unable to chmod %s to 0600: %s",
                self.paths.config_path,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_overrides(self) -> dict[str, Any]:
        with self._lock:
            data, migrated = self._load_file()
            if migrated:
                self._save_file(data)
            overrides = data.get("overrides") or {}
            return dict(overrides) if isinstance(overrides, dict) else {}

    def update_overrides(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Apply patch to overrides file. Returns updated overrides."""
        with self._lock:
            data, migrated = self._load_file()
            overrides = data.get("overrides") or {}
            if not isinstance(overrides, dict):
                overrides = {}

            def _set_or_clear(key: str, value: Any) -> None:
                if _is_blank(value):
                    overrides.pop(key, None)
                    return
                overrides[key] = value

            for key, value in patch.items():
                _set_or_clear(key, value)

            data["overrides"] = overrides
            self._save_file(data)
            # Always apply after update.
            self._apply_to_env(overrides)
            return dict(overrides)

    def apply(self) -> dict[str, Any]:
        """Load overrides (and migrate), then apply to env. Returns overrides."""
        with self._lock:
            data, migrated = self._load_file()
            if migrated:
                self._save_file(data)
            overrides = data.get("overrides") or {}
            if not isinstance(overrides, dict):
                overrides = {}
            self._apply_to_env(overrides)
            return dict(overrides)

    # ------------------------------------------------------------------
    # Env application
    # ------------------------------------------------------------------

    def _restore_or_unset(self, env_key: str) -> None:
        baseline_value = self._baseline_env.get(env_key)
        if baseline_value is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = baseline_value

    def _set_env(self, env_key: str, value: Any) -> None:
        if _is_blank(value):
            self._restore_or_unset(env_key)
            return
        os.environ[env_key] = str(value)

    def _apply_to_env(self, overrides: dict[str, Any]) -> None:
        # Backends
        image_backend = _safe_str(overrides.get("image_backend"))
        video_backend = _safe_str(overrides.get("video_backend"))
        if image_backend is not None:
            self._set_env("GEMINI_IMAGE_BACKEND", image_backend.strip().lower())
        else:
            self._restore_or_unset("GEMINI_IMAGE_BACKEND")

        if video_backend is not None:
            self._set_env("GEMINI_VIDEO_BACKEND", video_backend.strip().lower())
        else:
            self._restore_or_unset("GEMINI_VIDEO_BACKEND")

        # Secrets & base URLs
        for override_key, env_key in (
            ("gemini_api_key", "GEMINI_API_KEY"),
            ("gemini_base_url", "GEMINI_BASE_URL"),
            ("anthropic_api_key", "ANTHROPIC_API_KEY"),
            ("anthropic_base_url", "ANTHROPIC_BASE_URL"),
        ):
            if override_key in overrides:
                self._set_env(env_key, overrides.get(override_key))
            else:
                self._restore_or_unset(env_key)

        # Anthropic model routing
        for override_key, env_key in (
            ("anthropic_model", "ANTHROPIC_MODEL"),
            ("anthropic_default_haiku_model", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
            ("anthropic_default_opus_model", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
            ("anthropic_default_sonnet_model", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
            ("claude_code_subagent_model", "CLAUDE_CODE_SUBAGENT_MODEL"),
        ):
            if override_key in overrides:
                self._set_env(env_key, overrides.get(override_key))
            else:
                self._restore_or_unset(env_key)

        # Models, provider keys & misc simple overrides
        for override_key, env_key in (
            ("image_model", "GEMINI_IMAGE_MODEL"),
            ("video_model", "GEMINI_VIDEO_MODEL"),
            ("vertex_gcs_bucket", "VERTEX_GCS_BUCKET"),
            ("video_provider", "DEFAULT_VIDEO_PROVIDER"),
            ("ark_api_key", "ARK_API_KEY"),
            ("file_service_base_url", "FILE_SERVICE_BASE_URL"),
            ("xai_api_key", "XAI_API_KEY"),
        ):
            if override_key in overrides:
                self._set_env(env_key, overrides.get(override_key))
            else:
                self._restore_or_unset(env_key)

        # Video audio toggle (special: needs bool parsing)
        if "video_generate_audio" in overrides:
            configured = parse_bool_env(overrides.get("video_generate_audio"), True)
            self._set_env("GEMINI_VIDEO_GENERATE_AUDIO", "true" if configured else "false")
        else:
            self._restore_or_unset("GEMINI_VIDEO_GENERATE_AUDIO")

        # Rate limiting / performance
        for override_key, env_key, cast in (
            ("gemini_image_rpm", "GEMINI_IMAGE_RPM", _read_int),
            ("gemini_video_rpm", "GEMINI_VIDEO_RPM", _read_int),
            ("gemini_request_gap", "GEMINI_REQUEST_GAP", _read_float),
            ("image_max_workers", "IMAGE_MAX_WORKERS", _read_int),
            ("video_max_workers", "VIDEO_MAX_WORKERS", _read_int),
        ):
            if override_key in overrides:
                raw_value = overrides.get(override_key)
                normalized = cast(raw_value)
                if normalized is None:
                    self._restore_or_unset(env_key)
                else:
                    self._set_env(env_key, normalized)
            else:
                self._restore_or_unset(env_key)
