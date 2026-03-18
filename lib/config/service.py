from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository

_DEFAULT_VIDEO_BACKEND = "gemini-aistudio/veo-3.1-generate-001"
_DEFAULT_IMAGE_BACKEND = "gemini-aistudio/gemini-3.1-flash-image-preview"


@dataclass
class ProviderStatus:
    name: str
    display_name: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    configured_keys: list[str]
    missing_keys: list[str]


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._provider_repo = ProviderConfigRepository(session)
        self._setting_repo = SystemSettingRepository(session)

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all(provider)

    async def set_provider_config(self, provider: str, key: str, value: str) -> None:
        self._validate_provider(provider)
        meta = PROVIDER_REGISTRY[provider]
        is_secret = key in meta.secret_keys
        await self._provider_repo.set(provider, key, value, is_secret=is_secret)

    async def delete_provider_config(self, provider: str, key: str) -> None:
        self._validate_provider(provider)
        await self._provider_repo.delete(provider, key)

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            configured = await self._provider_repo.get_configured_keys(name)
            missing = [k for k in meta.required_keys if k not in configured]
            status: Literal["ready", "unconfigured", "error"] = (
                "ready" if not missing else "unconfigured"
            )
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=configured,
                    missing_keys=missing,
                )
            )
        return statuses

    async def get_provider_config_masked(self, provider: str) -> dict[str, dict]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all_masked(provider)

    async def get_setting(self, key: str, default: str = "") -> str:
        return await self._setting_repo.get(key, default)

    async def set_setting(self, key: str, value: str) -> None:
        await self._setting_repo.set(key, value)

    async def get_default_video_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_video_backend", _DEFAULT_VIDEO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)

    async def get_default_image_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_image_backend", _DEFAULT_IMAGE_BACKEND)
        return self._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)

    @staticmethod
    def _validate_provider(provider: str) -> None:
        if provider not in PROVIDER_REGISTRY:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _parse_backend(raw: str, fallback: str) -> tuple[str, str]:
        if "/" in raw:
            provider_id, model_id = raw.split("/", 1)
            return provider_id, model_id
        parts = fallback.split("/", 1)
        return parts[0], parts[1]
