from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.env_keys import ANTHROPIC_ENV_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.db.repositories.credential_repository import CredentialRepository

_DEFAULT_VIDEO_BACKEND = "gemini-aistudio/veo-3.1-lite-generate-preview"
_DEFAULT_IMAGE_BACKEND = "gemini-aistudio/gemini-3.1-flash-image-preview"
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"
_DEFAULT_AUDIO_BACKEND = "dashscope/qwen3-tts-flash"
# 旁白默认音色（DashScope 预设）；可被 project.json 顶层 narration_voice 或全局 setting 覆盖
# （与 video_backend 等同走顶层 key，非 settings 子字典）。
_DEFAULT_NARRATION_VOICE = "Cherry"

# 参考上传副本的保守通用请求体上限（ArcReel 侧安全策略常量，非任一供应商的真实字节限；
# 被动 413 兜底负责自我纠正）。可经 per-provider 配置 key 覆盖。
# 与 lib/reference_compression.DEFAULT_* 数值一致（单测断言对齐）。
_DEFAULT_REFERENCE_TOTAL_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_REFERENCE_SINGLE_MAX_BYTES = 4 * 1024 * 1024

# 写入层校验为非负整数的容量键（与 CapacityTable 的三条 lane 一一对应）。
# 其余 number 字段（image_rpm / video_rpm / request_gap）语义不同（允许小数），不在此列。
_MAX_WORKERS_KEYS = frozenset({"image_max_workers", "video_max_workers", "audio_max_workers"})
_MAX_WORKERS_CODE = "max_workers_must_be_nonnegative_integer"


class ProviderConfigValueError(ValueError):
    """provider 配置值校验失败。

    携带 i18n message code + 渲染参数（key 而非译文），router 层 ``_t(exc.code, **exc.params)``
    泛化翻译为 user-facing 文案，无需感知具体校验规则。
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
        self._validate_value(provider, key, value)
        if key in _MAX_WORKERS_KEYS:
            # 入库统一规范化为 ASCII 数字串（int() 接受 " 5 " / "+5" / "1_0" / 全角数字等
            # 非规范形态），保证任何读取方拿到的都是可直接解析、可在 number 输入框回显的值
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
            # 先按 __dict__ 排除 pricing（其费率含 tuple 键，非 JSON 可序列化且响应不消费；
            # 用 __dict__ 而非 asdict 以免递归转换 pricing 后又被丢弃），再 deepcopy 其余可变容器
            # 字段（list/dict），避免返回值与全局 PROVIDER_REGISTRY 共享引用被调用方意外改写。
            models_dict = {
                mid: deepcopy({k: v for k, v in mi.__dict__.items() if k != "pricing"})
                for mid, mi in meta.models.items()
            }
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

    async def get_default_audio_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_audio_backend", _DEFAULT_AUDIO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_AUDIO_BACKEND)

    async def get_narration_voice(self) -> str:
        # 空白 setting 视为未配置，与项目级覆盖的 strip 语义一致，避免空音色进 TTS 请求
        raw = await self._setting_repo.get("narration_voice", "")
        voice = raw.strip()
        return voice or _DEFAULT_NARRATION_VOICE

    async def get_narration_speed(self) -> float | None:
        """旁白语速（全局 setting）。未设置/损坏值返回 None，由各 audio backend 按自身能力处理。"""
        raw = await self._setting_repo.get("narration_speed", "")
        return self.parse_narration_speed(raw)

    @staticmethod
    def parse_narration_speed(raw: str) -> float | None:
        """把存储态语速字符串解析为有效正有限数；空白/非数值/非正/inf/nan 一律视为未设置。"""
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
        """容量键写入校验：非负整数（0 合法，语义=该 lane 容量为 0 即 fail-fast）。

        坏值一旦入库，容量 reload 只能逐 key 回退默认值，配置变更静默失效，
        因此在写入口拦下。
        """
        if key not in _MAX_WORKERS_KEYS:
            return
        try:
            parsed = int(value)
        except ValueError:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE) from None
        if parsed < 0:
            raise ProviderConfigValueError(provider, key, value, code=_MAX_WORKERS_CODE)

    @staticmethod
    def _parse_backend(raw: str, fallback: str) -> tuple[str, str]:
        if "/" in raw:
            provider_id, model_id = raw.split("/", 1)
            return provider_id, model_id
        parts = fallback.split("/", 1)
        return parts[0], parts[1]
