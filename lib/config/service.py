from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.video_backend_contract import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
from lib.agent.credential_tiers import TIERS, AgentPricing, Tier, agent_pricing, tier_models
from lib.catalog import query as catalog
from lib.catalog.builtin_models import disabled_builtin_models
from lib.config.env_keys import ANTHROPIC_ENV_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.schema_guards import is_int

DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"
DEFAULT_AUDIO_BACKEND = "dashscope/qwen3-tts-flash"
# Default DashScope narration voices without global settings. Global voices/speeds prefill new TTS projects only;
# after creation, project.json top-level snapshots govern (docs/adr/0089).
_DEFAULT_NARRATION_VOICE = "Cherry"
MIN_VIDEO_POLL_TIMEOUT_SECONDS = 60

# Conservative upload-copy request-size caps: ArcReel policy, not actual vendor limits;
# reactive 413 fallback self-corrects. Provider configuration keys can override.
# Match lib/references/reference_compression.DEFAULT_* values, guarded by tests.
DEFAULT_REFERENCE_TOTAL_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_REFERENCE_SINGLE_MAX_BYTES = 4 * 1024 * 1024

# Capacity keys requiring positive integers, matching three CapacityTable lanes.
# Other number fields image_rpm/video_rpm/request_gap permit fractions and are excluded.
_MAX_WORKERS_KEYS = frozenset({"image_max_workers", "video_max_workers", "audio_max_workers"})
_MAX_WORKERS_CODE = "max_workers_must_be_positive_integer"


class ProviderConfigValueError(ValueError):
    """Invalid provider configuration values.

    Carry i18n codes/parameters, never translations; routers render ``_t(exc.code, **exc.params)``
    generically without knowing validation rules.
    """

    def __init__(self, provider: str, key: str, value: str, *, code: str) -> None:
        super().__init__(f"invalid value for {provider}.{key}: {value!r} ({code})")
        self.provider = provider
        self.key = key
        self.value = value
        self.code = code
        self.params: dict[str, str] = {"field": key, "value": value}


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
# Environment-key consistency with ANTHROPIC_ENV_KEYS.
assert set(_ANTHROPIC_ENV_MAP.values()) == set(ANTHROPIC_ENV_KEYS), (
    "_ANTHROPIC_ENV_MAP 与 lib.config.env_keys.ANTHROPIC_ENV_KEYS 漂移"
)


# Tier → environment names.
_TIER_ENV_KEYS: dict[Tier, str] = {
    "model": "ANTHROPIC_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "subagent": "CLAUDE_CODE_SUBAGENT_MODEL",
}


@dataclass(frozen=True)
class AnthropicEnv:
    """Agent-session ANTHROPIC_* variables and same-row pricing facts, defaulting without active credentials."""

    env: dict[str, str]
    pricing: AgentPricing


async def build_anthropic_env(session: AsyncSession) -> AnthropicEnv:
    """Read active DB credentials, returning {ENV_KEY:value}/pricing snapshots, **never writing os.environ**.

    OptionsAssembler load_provider_env_overrides injects ClaudeAgentOptions.env. Read credentials once so
    env/pricing id/multiplier/tier bindings share rows; sessions price connection-time credentials (docs/adr/0102).

    Dual-track fallback: absent active fields use system_settings. Tier naming:
    :func:`lib.agent.credential_tiers.tier_models`.
    """
    # Local imports avoid agent_credential_repo → agent_credential model → base cycles.
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    cred = await AgentCredentialRepository(session).get_active()
    settings = await SystemSettingRepository(session).get_all()

    if cred is not None:
        models = tier_models(cred, settings)
        env = {
            "ANTHROPIC_API_KEY": cred.api_key or "",
            "ANTHROPIC_BASE_URL": cred.base_url or "",
            **{_TIER_ENV_KEYS[tier]: models[tier] for tier in TIERS},
        }
        return AnthropicEnv(env=env, pricing=agent_pricing(cred, settings))

    # No active credentials: dual-track system_settings fallback.
    return AnthropicEnv(
        env={env_key: settings.get(db_key, "").strip() for db_key, env_key in _ANTHROPIC_ENV_MAP.items()},
        pricing=AgentPricing(),
    )


@dataclass
class ProviderStatus:
    name: str
    display_name: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    # Credential counts, called keys in UI, for settings configured-key totals.
    credential_count: int
    models: dict[str, dict] | None = None  # model_id → CatalogModel dict shapes.
    # Disabled/discontinued keys never appear default candidates or automatic selection (docs/adr/0099).
    disabled_models: frozenset[str] = frozenset()


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
        self._validate_value(provider, key, value)
        if key in _MAX_WORKERS_KEYS:
            # Persist canonical ASCII digits: int() accepts whitespace/signs/underscores/fullwidth forms,
            # ensuring all readers receive parseable values echoable in number inputs.
            value = str(int(value))
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
        cred_repo = CredentialRepository(self._provider_repo.session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        credential_counts = await cred_repo.count_by_provider_bulk()
        disabled = await disabled_builtin_models(self._provider_repo.session)
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            status: Literal["ready", "unconfigured", "error"] = "ready" if name in active_creds else "unconfigured"
            # asdict deep-copies list/dict fields; caller mutations cannot affect catalog references.
            models_dict = {mid: asdict(mi) for mid, mi in catalog.list_models(name).items()}
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    status=status,
                    media_types=catalog.provider_media_types(name),
                    capabilities=catalog.provider_capabilities(name),
                    required_keys=list(meta.required_keys),
                    credential_count=credential_counts.get(name, 0),
                    models=models_dict,
                    disabled_models=disabled.get(name, frozenset()),
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

    async def get_video_poll_timeout_seconds(self) -> int:
        raw = await self._setting_repo.get(
            "video_poll_timeout_seconds",
            str(DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS),
        )
        return self.parse_video_poll_timeout_seconds(raw)

    @staticmethod
    def parse_video_poll_timeout_seconds(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
        return value if value >= MIN_VIDEO_POLL_TIMEOUT_SECONDS else DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS

    async def set_video_poll_timeout_seconds(self, value: int) -> None:
        if not is_int(value, minimum=MIN_VIDEO_POLL_TIMEOUT_SECONDS):
            raise ValueError(
                f"video poll timeout must be an integer of at least {MIN_VIDEO_POLL_TIMEOUT_SECONDS} seconds"
            )
        await self._setting_repo.set("video_poll_timeout_seconds", str(value))

    async def get_default_text_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_text_backend", DEFAULT_TEXT_BACKEND)
        return self._parse_backend(raw, DEFAULT_TEXT_BACKEND)

    async def get_default_audio_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_audio_backend", DEFAULT_AUDIO_BACKEND)
        return self._parse_backend(raw, DEFAULT_AUDIO_BACKEND)

    async def get_narration_voice(self) -> str:
        # Blank settings mean absent, matching project override strip semantics, keeping empty voices out of TTS.
        raw = await self._setting_repo.get("narration_voice", "")
        voice = raw.strip()
        return voice or _DEFAULT_NARRATION_VOICE

    async def get_narration_speed(self) -> float | None:
        """Global narration speeds; None for absent/corrupt settings, handled by audio backends per capability."""
        raw = await self._setting_repo.get("narration_speed", "")
        return self.parse_narration_speed(raw)

    @staticmethod
    def parse_narration_speed(raw: str) -> float | None:
        """Parse stored speed strings as positive finite numbers; blank/nonnumeric/nonpositive/inf/nan means unset."""
        text = raw.strip()
        if not text:
            return None
        try:
            speed = float(text)
        except ValueError:
            return None
        if not math.isfinite(speed) or speed <= 0:
            return None
        return speed

    @staticmethod
    def _validate_provider(provider: str) -> None:
        if provider not in PROVIDER_REGISTRY:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _validate_value(provider: str, key: str, value: str) -> None:
        """Capacity writes require positive integers ≥1. Empty keys are omitted for defaults, never reaching here.

        Zero is internal CapacityTable unsupported-lane sentinels only, produced in memory by projections,
        never persisted. Dirty values force per-key reload fallback and silently ineffective configuration,
        so reject at writes.
        """
        if key not in _MAX_WORKERS_KEYS:
            return
        try:
            parsed = int(value)
        except ValueError:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE) from None
        if parsed < 1:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE)

    @staticmethod
    def _parse_backend(raw: str, fallback: str) -> tuple[str, str]:
        if "/" in raw:
            provider_id, model_id = raw.split("/", 1)
            return provider_id, model_id
        parts = fallback.split("/", 1)
        return parts[0], parts[1]


async def read_video_poll_timeout_seconds() -> int:
    """Open sessions to read global video poll timeouts once.

    Workers/connection tests share this before real generation, preventing independently drifting timeout semantics.
    Read once at dispatch, never independently on each poll.
    """
    from lib.db import safe_session_factory

    async with safe_session_factory() as session:
        return await ConfigService(session).get_video_poll_timeout_seconds()
