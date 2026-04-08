"""Unified runtime configuration resolver.

Centralises config reads and default value definitions scattered across multiple files.
Reads from DB on each call, no caching (local SQLite overhead is negligible).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import async_sessionmaker

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import (
    _DEFAULT_IMAGE_BACKEND,
    _DEFAULT_TEXT_BACKEND,
    _DEFAULT_VIDEO_BACKEND,
    ConfigService,
)
from lib.db.repositories.credential_repository import CredentialRepository
from lib.env_init import PROJECT_ROOT
from lib.project_manager import ProjectManager
from lib.text_backends.base import TextTaskType

_project_manager: ProjectManager | None = None


def get_project_manager() -> ProjectManager:
    """Return the shared ProjectManager singleton (using the standard project root directory)."""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager(PROJECT_ROOT / "projects")
    return _project_manager


logger = logging.getLogger(__name__)

# Truthy values for boolean string parsing
_TRUTHY = frozenset({"true", "1", "yes"})


def _parse_bool(raw: str) -> bool:
    """Parse a configuration string as a boolean value."""
    return raw.strip().lower() in _TRUTHY


_TEXT_TASK_SETTING_KEYS: dict[TextTaskType, str] = {
    TextTaskType.SCRIPT: "text_backend_script",
    TextTaskType.OVERVIEW: "text_backend_overview",
    TextTaskType.STYLE_ANALYSIS: "text_backend_style",
}


class ConfigResolver:
    """Runtime configuration resolver.

    A thin wrapper on top of ConfigService that provides:
    - A single point of default value definitions
    - Typed outputs (bool / tuple / dict)
    - Built-in priority resolution (global config → project-level override)
    """

    # ── Single point of default value definitions ──
    _DEFAULT_VIDEO_GENERATE_AUDIO = False

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        _bound_session: AsyncSession | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bound_session = _bound_session

    # ── Session management ──

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ConfigResolver]:
        """Open a shared session and return a ConfigResolver bound to that session."""
        if self._bound_session is not None:
            yield self
        else:
            async with self._session_factory() as sess:
                yield ConfigResolver(self._session_factory, _bound_session=sess)

    @asynccontextmanager
    async def _open_session(self) -> AsyncIterator[tuple[AsyncSession, ConfigService]]:
        """Obtain (session, ConfigService), reusing bound session if available."""
        if self._bound_session is not None:
            yield self._bound_session, ConfigService(self._bound_session)
        else:
            async with self._session_factory() as session:
                yield session, ConfigService(session)

    # ── Public API ──

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        """Resolve video_generate_audio.

        Priority: project-level override > global config > default (False).
        """
        async with self._open_session() as (session, svc):
            return await self._resolve_video_generate_audio(svc, project_name)

    async def default_video_backend(self) -> tuple[str, str]:
        """Return (provider_id, model_id)."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_video_backend(svc, session)

    async def default_image_backend(self) -> tuple[str, str]:
        """Return (provider_id, model_id)."""
        async with self._open_session() as (session, svc):
            return await self._resolve_default_image_backend(svc, session)

    async def provider_config(self, provider_id: str) -> dict[str, str]:
        """Fetch configuration for a single provider."""
        async with self._open_session() as (session, svc):
            return await self._resolve_provider_config(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Bulk-fetch configuration for all providers."""
        async with self._open_session() as (session, svc):
            return await self._resolve_all_provider_configs(svc, session)

    # ── Internal resolver methods (independently testable, receive a pre-created svc) ──

    async def _resolve_video_generate_audio(
        self,
        svc: ConfigService,
        project_name: str | None,
    ) -> bool:
        raw = await svc.get_setting("video_generate_audio", "")
        value = _parse_bool(raw) if raw else self._DEFAULT_VIDEO_GENERATE_AUDIO

        if project_name:
            project = get_project_manager().load_project(project_name)
            override = project.get("video_generate_audio")
            if override is not None:
                if isinstance(override, str):
                    value = _parse_bool(override)
                else:
                    value = bool(override)

        return value

    async def _resolve_default_video_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        raw = await svc.get_setting("default_video_backend", "")
        if raw and "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)
        return await self._auto_resolve_backend(svc, session, "video")

    async def _resolve_default_image_backend(self, svc: ConfigService, session: AsyncSession) -> tuple[str, str]:
        raw = await svc.get_setting("default_image_backend", "")
        if raw and "/" in raw:
            return ConfigService._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)
        return await self._auto_resolve_backend(svc, session, "image")

    async def _resolve_provider_config(
        self,
        svc: ConfigService,
        session: AsyncSession,
        provider_id: str,
    ) -> dict[str, str]:
        config = await svc.get_provider_config(provider_id)
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            active.overlay_config(config)
        return config

    async def _resolve_all_provider_configs(
        self,
        svc: ConfigService,
        session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        for provider_id, cred in active_creds.items():
            cfg = configs.setdefault(provider_id, {})
            cred.overlay_config(cfg)
        return configs

    async def default_text_backend(self) -> tuple[str, str]:
        """Return (provider_id, model_id)."""
        async with self._open_session() as (session, svc):
            return await svc.get_default_text_backend()

    async def text_backend_for_task(
        self,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> tuple[str, str]:
        """Resolve text backend. Priority: project task config → global task config → global default → auto-resolve."""
        async with self._open_session() as (session, svc):
            return await self._resolve_text_backend(svc, session, task_type, project_name)

    async def _resolve_text_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        task_type: TextTaskType,
        project_name: str | None,
    ) -> tuple[str, str]:
        setting_key = _TEXT_TASK_SETTING_KEYS[task_type]

        # 1. Project-level task override
        if project_name:
            project = get_project_manager().load_project(project_name)
            project_val = project.get(setting_key)
            if project_val and "/" in str(project_val):
                return ConfigService._parse_backend(str(project_val), _DEFAULT_TEXT_BACKEND)

        # 2. Global task-type setting
        task_val = await svc.get_setting(setting_key, "")
        if task_val and "/" in task_val:
            return ConfigService._parse_backend(task_val, _DEFAULT_TEXT_BACKEND)

        # 3. Global default text backend
        default_val = await svc.get_setting("default_text_backend", "")
        if default_val and "/" in default_val:
            return ConfigService._parse_backend(default_val, _DEFAULT_TEXT_BACKEND)

        # 4. Auto-resolve
        return await self._auto_resolve_backend(svc, session, "text")

    async def _auto_resolve_backend(
        self,
        svc: ConfigService,
        session: AsyncSession,
        media_type: str,
    ) -> tuple[str, str]:
        """Iterate PROVIDER_REGISTRY (in registration order) and find the first ready provider supporting the given media_type."""
        statuses = await svc.get_all_providers_status()
        ready = {s.name for s in statuses if s.status == "ready"}

        for provider_id, meta in PROVIDER_REGISTRY.items():
            if provider_id not in ready:
                continue
            for model_id, model_info in meta.models.items():
                if model_info.media_type == media_type and model_info.default:
                    return provider_id, model_id

        from lib.custom_provider import make_provider_id
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository

        repo = CustomProviderRepository(session)
        custom_models = await repo.list_enabled_models_by_media_type(media_type)
        for model in custom_models:
            if model.is_default:
                return make_provider_id(model.provider_id), model.model_id

        raise ValueError(f"No available {media_type} provider found. Please configure at least one provider in Global Settings → Providers.")
