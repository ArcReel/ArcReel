from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.env_keys import ANTHROPIC_ENV_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.db.repositories.credential_repository import CredentialRepository

_DEFAULT_VIDEO_BACKEND = "gemini-aistudio/veo-3.1-lite-generate-preview"
_DEFAULT_IMAGE_BACKEND = "gemini-aistudio/gemini-3.1-flash-image-preview"
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"

# DB setting key → environment variable name
_ANTHROPIC_ENV_MAP: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_base_url": "ANTHROPIC_BASE_URL",
    "anthropic_model": "ANTHROPIC_MODEL",
    "anthropic_default_haiku_model": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "anthropic_default_opus_model": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "anthropic_default_sonnet_model": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "claude_code_subagent_model": "CLAUDE_CODE_SUBAGENT_MODEL",
}
# 一致性守护：env 名单与 ANTHROPIC_ENV_KEYS 必须对齐。
assert set(_ANTHROPIC_ENV_MAP.values()) == set(ANTHROPIC_ENV_KEYS), (
    "_ANTHROPIC_ENV_MAP 与 lib.config.env_keys.ANTHROPIC_ENV_KEYS 漂移"
)


async def build_anthropic_env_dict(session: AsyncSession) -> dict[str, str]:
    """从 DB 读 active credential，返回 {ENV_KEY: value} dict，**不写 os.environ**。

    返回值由 SessionManager._build_provider_env_overrides() 注入到
    ClaudeAgentOptions.env。

    双轨期 fallback：active credential 字段为空时从 system_settings 兜底。
    """
    # 局部 import 避免循环依赖（agent_credential_repo → agent_credential model → base）
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    repo = AgentCredentialRepository(session)
    cred = await repo.get_active()

    if cred is not None:
        settings = await SystemSettingRepository(session).get_all()
        return {
            "ANTHROPIC_API_KEY": cred.api_key or "",
            "ANTHROPIC_BASE_URL": cred.base_url or "",
            "ANTHROPIC_MODEL": cred.model or settings.get("anthropic_model", "").strip(),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": cred.haiku_model
            or settings.get("anthropic_default_haiku_model", "").strip(),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": cred.sonnet_model
            or settings.get("anthropic_default_sonnet_model", "").strip(),
            "ANTHROPIC_DEFAULT_OPUS_MODEL": cred.opus_model or settings.get("anthropic_default_opus_model", "").strip(),
            "CLAUDE_CODE_SUBAGENT_MODEL": cred.subagent_model or settings.get("claude_code_subagent_model", "").strip(),
        }

    # 无 active credential — 回退 system_settings（双轨期兼容）
    settings = await SystemSettingRepository(session).get_all()
    return {env_key: settings.get(db_key, "").strip() for db_key, env_key in _ANTHROPIC_ENV_MAP.items()}


@dataclass
class ProviderStatus:
    name: str
    display_name: str
    description: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    configured_keys: list[str]
    missing_keys: list[str]
    models: dict[str, dict] | None = None  # model_id -> ModelInfo dict representation


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._provider_repo = ProviderConfigRepository(session)
        self._setting_repo = SystemSettingRepository(session)

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all(provider)

    async def set_provider_config(
        self,
        provider: str,
        key: str,
        value: str,
        *,
        flush: bool = True,
    ) -> None:
        self._validate_provider(provider)
        meta = PROVIDER_REGISTRY[provider]
        is_secret = key in meta.secret_keys
        await self._provider_repo.set(provider, key, value, is_secret=is_secret, flush=flush)

    async def delete_provider_config(
        self,
        provider: str,
        key: str,
        *,
        flush: bool = True,
    ) -> None:
        self._validate_provider(provider)
        await self._provider_repo.delete(provider, key, flush=flush)

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        all_configured = await self._provider_repo.get_all_configured_keys_bulk()
        cred_repo = CredentialRepository(self._provider_repo.session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            has_active = name in active_creds
            configured = all_configured.get(name, [])
            if has_active:
                status: Literal["ready", "unconfigured", "error"] = "ready"
                missing: list[str] = []
            else:
                status = "unconfigured"
                missing = list(meta.required_keys)
            models_dict = {mid: asdict(mi) for mid, mi in meta.models.items()}
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    description=meta.description,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=configured,
                    missing_keys=missing,
                    models=models_dict,
                )
            )
        return statuses

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        """Get raw config for ALL providers in a single query."""
        return await self._provider_repo.get_all_configs_bulk()

    async def get_provider_config_masked(self, provider: str) -> dict[str, dict]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all_masked(provider)

    async def get_setting(self, key: str, default: str = "") -> str:
        return await self._setting_repo.get(key, default)

    async def get_all_settings(self) -> dict[str, str]:
        """Get all system settings in a single query."""
        return await self._setting_repo.get_all()

    async def set_setting(self, key: str, value: str) -> None:
        await self._setting_repo.set(key, value)

    async def get_default_video_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_video_backend", _DEFAULT_VIDEO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)

    async def get_default_image_backend(self) -> tuple[str, str]:
        """图像默认 backend 的真实解析路径在 ConfigResolver.default_image_backend_t2i / _i2i；
        此方法保留为公共 API，仅作为 T2I 兜底（外部调用方极少；Resolver 不调用此方法）。

        与 resolver._resolve_default_image_backend 语义一致：新 key 存在但为空 = 显式清空，
        不再回退 legacy；新 key 不存在才尝试 legacy。
        """
        settings = await self._setting_repo.get_all()
        if "default_image_backend_t2i" in settings:
            raw = settings["default_image_backend_t2i"]
        else:
            raw = settings.get("default_image_backend", _DEFAULT_IMAGE_BACKEND)
        return self._parse_backend(raw or _DEFAULT_IMAGE_BACKEND, _DEFAULT_IMAGE_BACKEND)

    async def get_default_text_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_text_backend", _DEFAULT_TEXT_BACKEND)
        return self._parse_backend(raw, _DEFAULT_TEXT_BACKEND)

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
