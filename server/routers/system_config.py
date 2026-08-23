"""
System configuration APIs.

Handles non-provider global settings: default backends, audio, anthropic config.
Provider-specific configuration (API keys, rate limits, credentials, connection test)
is managed by the providers router.
"""

from __future__ import annotations

import logging
import math
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, TypedDict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.capability_buckets import (
    BUCKETS_BY_MEDIA_TYPE,
    CapabilityBucket,
    builtin_model_buckets,
    custom_model_buckets,
)
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import mask_secret
from lib.config.resolver import ConfigResolver
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.httpx_shared import get_http_client
from lib.i18n import Translator
from server.dependencies import get_config_service
from server.routers._validators import validate_backend_value

logger = logging.getLogger(__name__)

router = APIRouter()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT_PATH = _PROJECT_ROOT / "pyproject.toml"
_GITHUB_RELEASE_LATEST_URL = "https://api.github.com/repos/ArcReel/ArcReel/releases/latest"
_GITHUB_USER_AGENT = "ArcReel"
_VERSION_CACHE_TTL_SECONDS = 300
_latest_release_cache: dict[str, datetime | dict[str, str] | None] = {
    "expires_at": None,
    "payload": None,
    "fetched_at": None,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _OptionsDict(TypedDict):
    video_backends: list[str]
    image_backends: list[str]
    text_backends: list[str]
    audio_backends: list[str]
    provider_names: dict[str, str]


_MEDIA_TO_OPTION_LIST = {
    "video": "video_backends",
    "image": "image_backends",
    "text": "text_backends",
    "audio": "audio_backends",
}


def _load_app_version(pyproject_path: Path) -> str:
    """从给定 pyproject 读版本号；不缓存，缓存由 :func:`_read_app_version` 负责。"""
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    version = str(data["project"]["version"]).strip()
    if not version:
        raise RuntimeError("project.version is empty")
    return version


@lru_cache(maxsize=1)
def _read_app_version() -> str:
    return _load_app_version(_PYPROJECT_PATH)


def get_app_version_reader() -> Callable[[], str]:
    """版本读取器的路由依赖；读失败的处置归路由，故注入可调用对象而非版本值。"""
    return _read_app_version


def _parse_version(raw: str) -> Version | None:
    text = raw.strip().removeprefix("v")
    if not text:
        return None
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _build_latest_release_payload(data: dict[str, Any]) -> dict[str, str]:
    raw_version = str(data.get("name") or data.get("tag_name") or "").strip()
    return {
        "version": raw_version.removeprefix("v"),
        "tag_name": str(data.get("tag_name") or ""),
        "name": str(data.get("name") or ""),
        "body": str(data.get("body") or ""),
        "html_url": str(data.get("html_url") or ""),
        "published_at": str(data.get("published_at") or ""),
    }


async def _get_latest_release(
    *,
    http_client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, str], datetime]:
    """Fetch latest GitHub release with a 5-minute cache.

    Returns (payload, fetched_at) where fetched_at is the timestamp of the
    actual successful HTTP fetch (not the current request time). This makes
    the value safe to surface as `checked_at` to clients without misleading
    them about cache freshness.
    """
    now = now or datetime.now(UTC)
    expires_at = _latest_release_cache.get("expires_at")
    payload = _latest_release_cache.get("payload")
    fetched_at = _latest_release_cache.get("fetched_at")
    if (
        isinstance(expires_at, datetime)
        and expires_at > now
        and isinstance(payload, dict)
        and isinstance(fetched_at, datetime)
    ):
        return payload, fetched_at

    client = http_client or get_http_client()
    response = await client.get(
        _GITHUB_RELEASE_LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _GITHUB_USER_AGENT},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = _build_latest_release_payload(response.json())

    _latest_release_cache["payload"] = payload
    _latest_release_cache["fetched_at"] = now
    _latest_release_cache["expires_at"] = now + timedelta(seconds=_VERSION_CACHE_TTL_SECONDS)
    return payload, now


@dataclass(frozen=True)
class _ModelCandidate:
    """一个可选模型及其任务类型桶归属，供不过滤的默认层与按桶过滤的细分层共用。"""

    option: str  # "provider_id/model_id"
    media_type: str
    buckets: frozenset[CapabilityBucket]


async def _enumerate_candidates(
    svc: ConfigService, session: AsyncSession
) -> tuple[list[_ModelCandidate], dict[str, str]]:
    """列出所有可选模型（内置 ready 供应商 + 已启用的自定义模型），并附任务类型桶归属。

    hidden 模型在这里统一剔除：registry 声明 hidden 的语义就是「从 UI 下拉剔除、条目仍保留
    供算价」，默认层与任务类型桶层同受此约束（能力过滤只加在桶层）。
    """
    statuses = await svc.get_all_providers_status()
    ready_providers = {s.name for s in statuses if s.status == "ready"}

    candidates: list[_ModelCandidate] = []
    provider_names: dict[str, str] = {}

    for provider_id, meta in PROVIDER_REGISTRY.items():
        if provider_id not in ready_providers:
            continue
        for model_id, model_info in meta.models.items():
            if model_info.hidden:
                continue
            candidates.append(
                _ModelCandidate(
                    option=f"{provider_id}/{model_id}",
                    media_type=model_info.media_type,
                    buckets=builtin_model_buckets(provider_id, model_id, model_info),
                )
            )

    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import endpoint_to_media_type
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    try:
        repo = CustomProviderRepository(session)
        providers = await repo.list_providers()
        provider_name_map = {p.id: p.display_name for p in providers}
        enabled_models = await repo.list_all_enabled_models()
        for model in enabled_models:
            pid = make_provider_id(model.provider_id)
            candidates.append(
                _ModelCandidate(
                    option=f"{pid}/{model.model_id}",
                    media_type=endpoint_to_media_type(model.endpoint),
                    buckets=custom_model_buckets(
                        endpoint=model.endpoint,
                        model_id=model.model_id,
                        capability_overrides=model.capability_overrides,
                    ),
                )
            )
            if pid not in provider_names and model.provider_id in provider_name_map:
                provider_names[pid] = provider_name_map[model.provider_id]
    except Exception:
        pass  # Non-fatal: custom providers unavailable shouldn't break the options endpoint

    return candidates, provider_names


async def _build_options(svc: ConfigService, session: AsyncSession) -> _OptionsDict:
    """Compute available backends from ready providers."""
    candidates, provider_names = await _enumerate_candidates(svc, session)

    by_media: dict[str, list[str]] = {
        "video_backends": [],
        "image_backends": [],
        "text_backends": [],
        "audio_backends": [],
    }
    for candidate in candidates:
        key = _MEDIA_TO_OPTION_LIST.get(candidate.media_type)
        if key:
            by_media[key].append(candidate.option)

    return {**by_media, "provider_names": provider_names}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MediaCandidates(BaseModel):
    """单一 media_type 的候选：默认层全量 + 各任务类型桶过滤后的子集。"""

    default: list[str]
    buckets: dict[CapabilityBucket, list[str]]


class ModelCandidatesResponse(BaseModel):
    image: MediaCandidates
    video: MediaCandidates
    # 仅含自定义供应商的显示名（内置供应商名由前端按 provider_id 本地化），与 options 同口径。
    provider_names: dict[str, str]


class SystemConfigPatchRequest(BaseModel):
    default_video_backend: str | None = None
    # 视频任务类型桶（docs/adr/0054）：i2v = 图生视频 / 宫格，r2v = 参考生视频；空值 = 回退默认层
    default_video_backend_i2v: str | None = None
    default_video_backend_r2v: str | None = None
    default_image_backend: str | None = None
    default_image_backend_t2i: str | None = None
    default_image_backend_i2i: str | None = None
    default_text_backend: str | None = None
    default_audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    video_generate_audio: bool | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_model: str | None = None
    anthropic_default_haiku_model: str | None = None
    anthropic_default_opus_model: str | None = None
    anthropic_default_sonnet_model: str | None = None
    claude_code_subagent_model: str | None = None
    agent_session_cleanup_delay_seconds: int | None = None
    agent_max_concurrent_sessions: int | None = None
    # 文本任务档位（docs/adr/0051）：调用点在代码里固定归档，这里只配置每档的 backend；
    # 各档未设置回退 default_text_backend。
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None


# Setting keys that map directly to string DB settings
#
# DEPRECATED: anthropic_api_key / anthropic_base_url 已迁移至 agent_anthropic_credentials 表
# (spec 2026-05-11-agent-url-config-optimization)。这里保留 anthropic_base_url 读写仅作旧客户端
# 兼容；新 UI 走 /api/v1/agent/credentials/* 接口。计划在 0.14.0 删除 anthropic_api_key /
# anthropic_base_url 字段，anthropic_*_model 系列保留（仍由 Section 2 Model Routing 管理）。
_STRING_SETTINGS = (
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_opus_model",
    "anthropic_default_sonnet_model",
    "claude_code_subagent_model",
)


# ---------------------------------------------------------------------------
# GET /system/config
# ---------------------------------------------------------------------------


@router.get("/system/config")
async def get_system_config(
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    # Read all settings in a single query
    all_s = await svc.get_all_settings()
    video_generate_audio_raw = all_s.get("video_generate_audio", "")
    video_generate_audio = (
        video_generate_audio_raw.lower() in ("true", "1", "yes")
        if video_generate_audio_raw
        else ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO
    )
    anthropic_key = all_s.get("anthropic_api_key", "")
    # 兼容新凭证目录：旧 system_settings 没填但 agent_anthropic_credentials 有 active 时
    # 也算 is_set，避免 dashboard "未配置" 红点误报
    if not anthropic_key:
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        active_cred = await AgentCredentialRepository(session).get_active()
        if active_cred is not None:
            anthropic_key = active_cred.api_key

    # 语速 setting 为字符串存储，损坏值（手工改库等）按未设置处理
    narration_speed = ConfigService.parse_narration_speed(all_s.get("narration_speed", ""))

    settings: dict[str, Any] = {
        "default_video_backend": all_s.get("default_video_backend", ""),
        "default_video_backend_i2v": all_s.get("default_video_backend_i2v", ""),
        "default_video_backend_r2v": all_s.get("default_video_backend_r2v", ""),
        "default_image_backend": all_s.get("default_image_backend", ""),
        "default_image_backend_t2i": all_s.get("default_image_backend_t2i", ""),
        "default_image_backend_i2i": all_s.get("default_image_backend_i2i", ""),
        "default_text_backend": all_s.get("default_text_backend", ""),
        "default_audio_backend": all_s.get("default_audio_backend", ""),
        "narration_voice": all_s.get("narration_voice", ""),
        "narration_speed": narration_speed,
        "video_generate_audio": video_generate_audio,
        "anthropic_api_key": {
            "is_set": bool(anthropic_key),
            "masked": mask_secret(anthropic_key) if anthropic_key else None,
        },
        "anthropic_base_url": all_s.get("anthropic_base_url") or None,
        "anthropic_model": all_s.get("anthropic_model") or None,
        "anthropic_default_haiku_model": all_s.get("anthropic_default_haiku_model") or None,
        "anthropic_default_opus_model": all_s.get("anthropic_default_opus_model") or None,
        "anthropic_default_sonnet_model": all_s.get("anthropic_default_sonnet_model") or None,
        "claude_code_subagent_model": all_s.get("claude_code_subagent_model") or None,
        "agent_session_cleanup_delay_seconds": int(all_s.get("agent_session_cleanup_delay_seconds") or "300"),
        "agent_max_concurrent_sessions": int(all_s.get("agent_max_concurrent_sessions") or "5"),
        "text_backend_simple": all_s.get("text_backend_simple") or "",
        "text_backend_complex": all_s.get("text_backend_complex") or "",
    }

    options = await _build_options(svc, session)

    return {"settings": settings, "options": options}


@router.get("/system/config/model-candidates", response_model=ModelCandidatesResponse)
async def get_model_candidates(
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> ModelCandidatesResponse:
    """任务类型桶下拉的候选数据源：默认层全量 + 每个任务类型桶按能力过滤后的模型列表。

    默认层不过滤 —— 默认层不承诺能力，能力不满足由解析闸报错兜底；只有桶层承诺「配进去的
    组合执行得了」，故按桶过滤。
    """
    candidates, provider_names = await _enumerate_candidates(svc, session)

    media: dict[str, MediaCandidates] = {}
    for media_type, buckets in BUCKETS_BY_MEDIA_TYPE.items():
        same_media = [c for c in candidates if c.media_type == media_type]
        media[media_type] = MediaCandidates(
            default=[c.option for c in same_media],
            buckets={bucket: [c.option for c in same_media if bucket in c.buckets] for bucket in buckets},
        )

    return ModelCandidatesResponse(
        image=media["image"],
        video=media["video"],
        provider_names=provider_names,
    )


@router.get("/system/version")
async def get_system_version(
    _t: Translator,
    read_app_version: Annotated[Callable[[], str], Depends(get_app_version_reader)],
) -> dict[str, Any]:
    try:
        current_version = read_app_version()
    except Exception as exc:
        logger.exception("Failed to read app version")
        raise HTTPException(status_code=500, detail=_t("about_version_read_failed")) from exc

    latest: dict[str, str] | None = None
    has_update = False
    update_check_error: str | None = None
    checked_at: datetime = datetime.now(UTC)
    try:
        latest, checked_at = await _get_latest_release()
        latest_v = _parse_version(latest["version"])
        current_v = _parse_version(current_version)
        if latest_v is not None and current_v is not None:
            has_update = latest_v > current_v
    except Exception as exc:
        logger.warning("Failed to fetch latest release: %s", exc)
        update_check_error = _t("about_update_check_failed")

    return {
        "current": {"version": current_version},
        "latest": latest,
        "has_update": has_update,
        "checked_at": checked_at.isoformat(),
        "update_check_error": update_check_error,
    }


# ---------------------------------------------------------------------------
# PATCH /system/config
# ---------------------------------------------------------------------------


@router.patch("/system/config")
async def patch_system_config(
    req: SystemConfigPatchRequest,
    svc: Annotated[ConfigService, Depends(get_config_service)],
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        patch[field_name] = getattr(req, field_name)

    # Validate backend references (empty string = auto-resolve)
    for backend_key in (
        "default_video_backend",
        "default_video_backend_i2v",
        "default_video_backend_r2v",
        "default_image_backend",
        "default_image_backend_t2i",
        "default_image_backend_i2i",
        "default_text_backend",
        "default_audio_backend",
        "text_backend_simple",
        "text_backend_complex",
    ):
        if backend_key in patch:
            value = str(patch[backend_key] or "").strip()
            if value:
                validate_backend_value(value, backend_key)
            await svc.set_setting(backend_key, value)

    # 旁白音色：可配置字符串 id（照供应商文档填），空串 = 清除回落服务默认
    if "narration_voice" in patch:
        await svc.set_setting("narration_voice", str(patch["narration_voice"] or "").strip())

    # 旁白语速：仅做正有限数卫生校验（拒绝 0/负数/inf/nan），具体取值范围由各供应商自行约束；null = 清除
    if "narration_speed" in patch:
        speed = patch["narration_speed"]
        if speed is None:
            await svc.set_setting("narration_speed", "")
        else:
            speed = float(speed)
            if not math.isfinite(speed) or speed <= 0:
                raise HTTPException(status_code=422, detail=_t("narration_speed_must_be_positive"))
            await svc.set_setting("narration_speed", str(speed))

    # Boolean settings
    if "video_generate_audio" in patch and patch["video_generate_audio"] is not None:
        await svc.set_setting("video_generate_audio", "true" if patch["video_generate_audio"] else "false")

    # Anthropic API key (secret)
    if "anthropic_api_key" in patch:
        value = patch["anthropic_api_key"]
        if value:
            await svc.set_setting("anthropic_api_key", str(value).strip())
        else:
            await svc.set_setting("anthropic_api_key", "")

    # Integer settings with range validation
    _INT_SETTINGS_RANGES = {
        "agent_session_cleanup_delay_seconds": (10, 3600),
        "agent_max_concurrent_sessions": (1, 20),
    }
    for key, (min_val, max_val) in _INT_SETTINGS_RANGES.items():
        if key in patch and patch[key] is not None:
            value = int(patch[key])
            if not (min_val <= value <= max_val):
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} 应在 {min_val}-{max_val} 之间",
                )
            await svc.set_setting(key, str(value))

    # String settings
    for key in _STRING_SETTINGS:
        if key in patch:
            value = patch[key]
            await svc.set_setting(key, str(value).strip() if value else "")

    await session.commit()

    # Return updated config
    return await get_system_config(svc=svc, session=session)
