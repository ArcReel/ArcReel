"""
System configuration APIs.

Handles non-provider global settings: default backends, audio, anthropic config.
Provider-specific configuration (API keys, rate limits, credentials, connection test)
is managed by the providers router.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.app_version import PYPROJECT_PATH, load_app_version
from lib.backends.audio_backends.base import AudioCapability
from lib.backends.generation_type_buckets import (
    BUCKETS_BY_MEDIA_TYPE,
    BucketDimension,
    BucketFit,
    GenerationTypeBucket,
    builtin_model_bucket_fit,
    custom_model_bucket_fit,
)
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.catalog.lifecycle import Lifecycle, entry_lifecycle
from lib.config.model_setting_keys import GLOBAL_MODEL_SETTING_KEYS, MODEL_SETTING_MEDIA_TYPES
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import mask_secret
from lib.config.resolver import ConfigResolver
from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS, ConfigService
from lib.db import async_session_factory, get_async_session
from lib.i18n import DEFAULT_LOCALE, translate_or
from lib.infra.api_errors import UnprocessableError
from lib.infra.httpx_shared import get_http_client
from lib.market.sources import PROXY_PREFIX_SETTING
from lib.speech.narration_config import parse_tts_backend
from server.dependencies import get_config_service
from server.i18n import Locale, Translator
from server.routers._validators import validate_backend_value
from server.services.project.model_references import project_model_references
from server.services.project.narration_settings import default_narration_tts
from server.services.tasks.generation_context import audio_backend_capabilities

logger = logging.getLogger(__name__)

router = APIRouter()
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
    model_names: dict[str, str]


_MEDIA_TO_OPTION_LIST = {
    "video": "video_backends",
    "image": "image_backends",
    "text": "text_backends",
    "audio": "audio_backends",
}


@lru_cache(maxsize=1)
def _read_app_version() -> str:
    return load_app_version(PYPROJECT_PATH)


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
    raise_for_status_redacted(response)
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
    fit: BucketFit  # 各任务类型桶的判定，连同不进桶的原因
    display_name: str  # 按请求语言成文的模型名
    entry: str | None = None  # 所对应条目的引用（「模型厂商/官方模型名」）；对应不上条目时为 None

    @property
    def provider(self) -> str:
        return self.option.split("/", 1)[0]

    @property
    def buckets(self) -> frozenset[GenerationTypeBucket]:
        return self.fit.buckets


@dataclass(frozen=True)
class _RetiringModel:
    """一个即将下线或已下线的供应商模型，不论启用与否、在不在候选里：引用处的下线提示据此给出。"""

    option: str  # "provider_id/model_id"
    media_type: str
    lifecycle: Lifecycle


async def _enumerate_candidates(
    svc: ConfigService, session: AsyncSession, locale: str = DEFAULT_LOCALE
) -> tuple[list[_ModelCandidate], dict[str, str]]:
    """列出所有可选模型（内置 ready 供应商与自定义供应商下已启用的供应商模型），并附任务类型桶归属。

    候选只来自已启用、未下线的供应商模型，默认层与任务类型桶层同受此约束（能力过滤只加在桶层）。内置供应商的这两项
    取自供应商状态的 ``disabled_models``（见 :func:`lib.catalog.builtin_models.disabled_builtin_models`）；自定义供应商
    看模型行的启用状态与所对应条目的生命周期（:func:`lib.custom_provider.default_models.model_lifecycle`）。

    供应商名与模型名走 ``translate_or``，与 ``/providers`` 目录同一条路径：有译名表的按请求
    语言成文，没有的（纯品牌名、自定义供应商用户自填的名字）原样返回 registry / DB 里的原名。
    """
    statuses = await svc.get_all_providers_status()
    ready_providers = {s.name: s for s in statuses if s.status == "ready"}

    candidates: list[_ModelCandidate] = []
    provider_names: dict[str, str] = {}

    for provider_id, meta in PROVIDER_REGISTRY.items():
        if provider_id not in ready_providers:
            continue
        provider_names[provider_id] = translate_or(f"provider_name_{provider_id}", meta.display_name, locale)
        for model_id, model_info in catalog.list_models(provider_id).items():
            if model_id in ready_providers[provider_id].disabled_models:
                continue
            entry = catalog.provider_model_entry(provider_id, model_id, model_info.media_type)
            candidates.append(
                _ModelCandidate(
                    option=f"{provider_id}/{model_id}",
                    media_type=model_info.media_type,
                    fit=builtin_model_bucket_fit(provider_id, model_id, model_info),
                    display_name=model_info.display_name,
                    entry=entry.key.ref if entry is not None else None,
                )
            )

    from lib.custom_provider import make_provider_id
    from lib.custom_provider.default_models import model_lifecycle
    from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
    from lib.custom_provider.model_facts import model_entry
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    try:
        repo = CustomProviderRepository(session)
        providers = await repo.list_providers()
        provider_name_map = {p.id: p.display_name for p in providers}
        enabled_models = await repo.list_all_enabled_models()
        endpoint_repo = CustomEndpointRepository(session)
        for model in enabled_models:
            endpoint_spec = await resolve_endpoint_spec(model.endpoint, endpoint_repo.get)
            if model_lifecycle(model, endpoint_spec.media_type).discontinued:
                continue
            pid = make_provider_id(model.provider_id)
            entry = model_entry(model.model_id, endpoint_spec.media_type, model.entry_binding)
            candidates.append(
                _ModelCandidate(
                    option=f"{pid}/{model.model_id}",
                    media_type=endpoint_spec.media_type,
                    fit=custom_model_bucket_fit(
                        endpoint=model.endpoint,
                        model_id=model.model_id,
                        capability_overrides=model.capability_overrides,
                        endpoint_spec=endpoint_spec,
                        entry_binding=model.entry_binding,
                    ),
                    display_name=model.display_name,
                    entry=entry.key.ref if entry is not None else None,
                )
            )
            if pid not in provider_names and model.provider_id in provider_name_map:
                provider_names[pid] = provider_name_map[model.provider_id]
    except Exception:
        pass  # Non-fatal: custom providers unavailable shouldn't break the options endpoint

    return candidates, provider_names


async def _enumerate_retiring_models(session: AsyncSession) -> list[_RetiringModel]:
    """列出即将下线或已下线的供应商模型：全部内置供应商与自定义供应商，启用与否都算。

    设置与项目里的引用可能指向停用的、已下线的或所在供应商尚未配置的模型，它们不在候选里，提示仍要给出。
    官方数据删除、只剩本地保留条目的模型不在清单里，这里列不出，由 :func:`_unlisted_references` 按被引用的值补上。
    """
    retiring = [
        _RetiringModel(f"{provider_id}/{view.model_id}", view.model.media_type, view.lifecycle)
        for provider_id in PROVIDER_REGISTRY
        for view in catalog.provider_models(provider_id)
        if view.lifecycle.status != "available"
    ]

    from lib.custom_provider import make_provider_id
    from lib.custom_provider.default_models import model_lifecycle
    from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    endpoint_repo = CustomEndpointRepository(session)
    for provider, models in await CustomProviderRepository(session).list_providers_with_models():
        for model in models:
            try:
                media_type = (await resolve_endpoint_spec(model.endpoint, endpoint_repo.get)).media_type
            except ValueError:
                continue  # 调用端点已被删除：供应商模型列表同样不列这一行
            state = model_lifecycle(model, media_type)
            if state.status != "available":
                retiring.append(_RetiringModel(f"{make_provider_id(provider.id)}/{model.model_id}", media_type, state))
    return retiring


async def _referenced_models(svc: ConfigService) -> dict[str, str]:
    """全局设置与各项目的模型设置字段引用的 ``provider/model`` → 所引模型的媒体类型（取自字段）。

    逐个读项目的 ``project.json``，耗时与项目数成正比。
    """
    settings = await svc.get_all_settings()
    pairs: list[tuple[str, object]] = [(key, settings.get(key)) for key in GLOBAL_MODEL_SETTING_KEYS]
    for project in await asyncio.to_thread(project_model_references):
        pairs.extend(project.references.items())
    return {value: MODEL_SETTING_MEDIA_TYPES[key] for key, value in pairs if isinstance(value, str) and value}


def _unlisted_references(references: Mapping[str, str]) -> tuple[list[_RetiringModel], dict[str, str]]:
    """被引用、却不在内置供应商清单里的模型：按名字对应到本地已下线条目的，按条目给下线提示；其余提示不可用。

    返回（按条目提示的模型, 不可用的 ``provider/model`` → 媒体类型）。不在清单里的模型无法调用，对应到的条目
    即将下线或仍可用时同样算不可用。自定义供应商的引用不在这里判定：它们按模型行判定（:func:`_enumerate_retiring_models`）。
    """
    discontinued: list[_RetiringModel] = []
    unavailable: dict[str, str] = {}
    for option, media_type in references.items():
        provider_id, sep, model_id = option.partition("/")
        if not sep or provider_id not in PROVIDER_REGISTRY or catalog.get_model(provider_id, model_id) is not None:
            continue
        state = entry_lifecycle(catalog.referenced_entry(provider_id, model_id, media_type))
        if state.discontinued:
            discontinued.append(_RetiringModel(option, media_type, state))
        else:
            unavailable[option] = media_type
    return discontinued, unavailable


#: 引用处提示的状态：即将下线、已下线，以及引用的模型不在清单里、本地也没有已下线条目的「不可用」。
NoticeStatus = Literal["retiring", "discontinued", "unavailable"]


class LifecycleNotice(BaseModel):
    """引用处的下线提示：状态、关停日期、官方后继，以及每个引用位置可一键切换的目标。"""

    status: NoticeStatus
    shutdown_date: date | None
    #: 官方后继的条目引用（「模型厂商/官方模型名」）与条目名；没有登记后继时为 None。
    successor: str | None
    successor_name: str | None
    #: 引用位置（``default`` 为默认层，其余为该媒体类型的任务类型桶）→ 一键切换的目标 ``provider/model``；
    #: 同一供应商下没有对应到后继、且是该位置候选的供应商模型时为 None，只展示后继名称。
    switch_targets: dict[str, str | None]


def _reference_layers(media_type: str) -> tuple[str, ...]:
    """一种媒体类型的引用位置：默认层加该媒体类型的任务类型桶。"""
    return ("default", *BUCKETS_BY_MEDIA_TYPE.get(media_type, ()))


def _lifecycle_notices(
    retiring: list[_RetiringModel], candidates: list[_ModelCandidate], unavailable: Mapping[str, str] | None = None
) -> dict[str, LifecycleNotice]:
    """``provider/model`` → 下线提示。切换目标只从候选里挑，与该位置的下拉同一份资格（启用、未下线、媒体类型、
    端点、最低应用版本由候选枚举把关，任务类型桶看候选的桶归属）。``unavailable``（``provider/model`` → 媒体类型）
    的模型没有生命周期信息，提示不可用，不给后继与切换目标。"""
    notices: dict[str, LifecycleNotice] = {
        option: LifecycleNotice(
            status="unavailable",
            shutdown_date=None,
            successor=None,
            successor_name=None,
            switch_targets=dict.fromkeys(_reference_layers(media_type)),
        )
        for option, media_type in (unavailable or {}).items()
    }
    for model in retiring:
        state = model.lifecycle
        provider = model.option.split("/", 1)[0]
        successors = [
            c
            for c in candidates
            if state.successor is not None
            and c.entry == state.successor
            and c.provider == provider
            and c.media_type == model.media_type
        ]
        layers = _reference_layers(model.media_type)
        successor_key = catalog.binding_key(state.successor, model.media_type)
        successor_entry = catalog.get_entry(successor_key) if successor_key is not None else None
        notices[model.option] = LifecycleNotice(
            status="discontinued" if state.discontinued else "retiring",  # 只收即将下线与已下线的模型
            shutdown_date=state.shutdown_date,
            successor=state.successor,
            successor_name=successor_entry.display_name if successor_entry is not None else None,
            switch_targets={
                layer: next((c.option for c in successors if layer == "default" or layer in c.buckets), None)
                for layer in layers
            },
        )
    return notices


def _model_names(candidates: list[_ModelCandidate]) -> dict[str, str]:
    """``"provider_id/model_id"`` → 已成文的模型名，两个端点共用的那张表。

    不按媒体类型收窄：同屏的文本档位与旁白配音下拉取自 ``/system/config`` 的候选，共用这一份
    译名表，缺键会让它们退回 model id。
    """
    return {c.option: c.display_name for c in candidates}


async def _build_options(svc: ConfigService, session: AsyncSession, locale: str = DEFAULT_LOCALE) -> _OptionsDict:
    """Compute available backends from ready providers."""
    candidates, provider_names = await _enumerate_candidates(svc, session, locale)

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

    return _OptionsDict(
        video_backends=by_media["video_backends"],
        image_backends=by_media["image_backends"],
        text_backends=by_media["text_backends"],
        audio_backends=by_media["audio_backends"],
        provider_names=provider_names,
        model_names=_model_names(candidates),
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class FilteredCandidate(BaseModel):
    """默认层里有、某个任务类型桶因能力不满足而没有列出的模型。"""

    option: str  # "provider_id/model_id"
    # 不满足的能力维度，键与能力接口同名；为空表示判定不出（调用端点已下线、调用通道没有声明能力）。
    unmet: list[BucketDimension]


class MediaCandidates(BaseModel):
    """单一 media_type 的候选：默认层全量 + 各任务类型桶过滤后的子集，以及各桶滤掉的模型与原因。"""

    default: list[str]
    buckets: dict[GenerationTypeBucket, list[str]]
    # 只收默认层里的模型：停用、已下线或低于最低应用版本的模型不在默认层，也就不在这里。
    filtered: dict[GenerationTypeBucket, list[FilteredCandidate]]


class ModelCandidatesResponse(BaseModel):
    image: MediaCandidates
    video: MediaCandidates
    # 供应商 id → 按请求语言成文的名字；与 options 同口径（同一条 translate_or 路径）。
    provider_names: dict[str, str]
    # "provider_id/model_id" → 按请求语言成文的模型名；键覆盖全部媒体类型的候选（含 options
    # 才用到的 text / audio），前端下拉据此显示译名。
    model_names: dict[str, str]
    # "provider_id/model_id" → 下线提示，收即将下线与已下线的供应商模型（含不在候选里的），以及设置与项目里引用的
    # 清单之外的模型（已下线或不可用），覆盖全部媒体类型；引用了它的下拉就地展示。
    lifecycle: dict[str, LifecycleNotice]


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
    # 市场源 GitHub raw 代理前缀：空串 = 直连
    market_github_proxy_prefix: str | None = None
    video_generate_audio: bool | None = None
    video_poll_timeout_seconds: int | None = None
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
    locale: Locale,
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
        "video_poll_timeout_seconds": ConfigService.parse_video_poll_timeout_seconds(
            all_s.get("video_poll_timeout_seconds") or str(DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS)
        ),
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
        "market_github_proxy_prefix": all_s.get(PROXY_PREFIX_SETTING) or "",
    }

    options = await _build_options(svc, session, locale)

    return {"settings": settings, "options": options}


@router.get("/system/config/model-candidates", response_model=ModelCandidatesResponse)
async def get_model_candidates(
    svc: Annotated[ConfigService, Depends(get_config_service)],
    locale: Locale,
    session: AsyncSession = Depends(get_async_session),
) -> ModelCandidatesResponse:
    """任务类型桶下拉的候选数据源：默认层全量 + 每个任务类型桶按能力过滤后的模型列表。

    默认层不过滤 —— 默认层不承诺能力，能力不满足由解析闸报错兜底；只有桶层承诺「配进去的
    组合执行得了」，故按桶过滤。被某个桶滤掉的默认层模型连同不满足的能力维度列在 ``filtered``，原因与进桶判定
    同一个求值结果（:class:`BucketFit`）。

    另带引用处的下线提示（``lifecycle``），一键切换的目标从同一份候选里挑。设置与项目里引用了清单之外的模型时，
    按被引用的值对应本地保留的条目补上提示（:func:`_unlisted_references`）。
    """
    candidates, provider_names = await _enumerate_candidates(svc, session, locale)
    discontinued, unavailable = _unlisted_references(await _referenced_models(svc))

    media: dict[str, MediaCandidates] = {}
    for media_type, buckets in BUCKETS_BY_MEDIA_TYPE.items():
        same_media = [c for c in candidates if c.media_type == media_type]
        media[media_type] = MediaCandidates(
            default=[c.option for c in same_media],
            buckets={bucket: [c.option for c in same_media if bucket in c.buckets] for bucket in buckets},
            filtered={
                bucket: [
                    FilteredCandidate(option=c.option, unmet=list(c.fit.unmet[bucket]))
                    for c in same_media
                    if bucket in c.fit.unmet
                ]
                for bucket in buckets
            },
        )

    return ModelCandidatesResponse(
        image=media["image"],
        video=media["video"],
        provider_names=provider_names,
        model_names=_model_names(candidates),
        lifecycle=_lifecycle_notices(
            [*await _enumerate_retiring_models(session), *discontinued], candidates, unavailable
        ),
    )


class AutoBackendsResponse(BaseModel):
    """各用途在项目与全局各层都留空时自动选中的模型（``provider/model``）；没有可用供应商时为 None。"""

    t2i: str | None
    i2i: str | None
    i2v: str | None
    r2v: str | None
    text: str | None


@router.get("/system/config/auto-backends", response_model=AutoBackendsResponse)
async def get_auto_backends() -> AutoBackendsResponse:
    """「自动选择」的实际模型：与执行期解析同口径，设置界面据此显示当前模型并按它查询能力。"""
    resolved = await ConfigResolver(async_session_factory).auto_resolved_backends()
    return AutoBackendsResponse(
        **{purpose: f"{pair[0]}/{pair[1]}" if pair else None for purpose, pair in resolved.items()}
    )


class NarrationDefaultsResponse(BaseModel):
    """新建 TTS 项目的预填值；未配置任何音频供应商时 ``audio_backend`` 为 None。"""

    audio_backend: str | None
    narration_voice: str
    narration_speed: float | None


@router.get("/system/narration-defaults", response_model=NarrationDefaultsResponse)
async def get_narration_defaults(
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> NarrationDefaultsResponse:
    """全局默认音频模型、旁白音色与配音语速：创建向导与项目设置切到 TTS 配音时用它预填项目快照。"""
    try:
        backend, voice, speed = await default_narration_tts(ConfigResolver(async_session_factory))
        audio_backend: str | None = f"{backend.provider_id}/{backend.model_id}"
    except ValueError:
        audio_backend, voice, speed = None, await svc.get_narration_voice(), await svc.get_narration_speed()
    return NarrationDefaultsResponse(audio_backend=audio_backend, narration_voice=voice, narration_speed=speed)


class TtsModelCapabilitiesResponse(BaseModel):
    supports_speed: bool


@router.get("/system/tts-model-capabilities", response_model=TtsModelCapabilitiesResponse)
async def get_tts_model_capabilities(backend: str) -> TtsModelCapabilitiesResponse:
    """所选 TTS 模型（``provider/model``）是否把配音语速传给供应商；不支持时设置页置灰语速输入。"""
    pair = parse_tts_backend(backend)
    if pair is None:
        raise UnprocessableError("narration_tts_model_required")
    try:
        capabilities = await audio_backend_capabilities(*pair)
    except ModelNotInCatalogError:
        # 引用的模型不在供应商清单中：交 app 级 handler 返回要求重新选择模型的 400（ValueError 子类，须先于其捕获）
        raise
    except ValueError as exc:
        raise UnprocessableError("narration_tts_model_invalid") from exc
    return TtsModelCapabilitiesResponse(supports_speed=AudioCapability.SPEECH_SPEED in capabilities)


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


def _is_valid_proxy_prefix(prefix: str) -> bool:
    if not prefix.isprintable() or any(char.isspace() for char in prefix):
        return False
    try:
        parts = urlsplit(prefix)
        parts.port  # noqa: B018 -- 访问即校验端口，非法端口抛 ValueError
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and "?" not in prefix
        and "#" not in prefix
    )


@router.patch("/system/config")
async def patch_system_config(
    req: SystemConfigPatchRequest,
    svc: Annotated[ConfigService, Depends(get_config_service)],
    _t: Translator,
    locale: Locale,
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

    # 市场源 GitHub raw 代理前缀：只拼在 raw.githubusercontent.com 地址前，须是带主机名、不含空白与控制字符、不含用户名与密码的
    # https 地址（该值经 GET 原样回显，不当作凭证存放），且不带查询串与 fragment（raw 地址按路径拼在前缀之后）；空串 = 清除
    if "market_github_proxy_prefix" in patch:
        prefix = str(patch["market_github_proxy_prefix"] or "").strip()
        if prefix and not _is_valid_proxy_prefix(prefix):
            raise UnprocessableError("market_github_proxy_prefix_invalid")
        await svc.set_setting(PROXY_PREFIX_SETTING, prefix)

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

    if "video_poll_timeout_seconds" in patch and patch["video_poll_timeout_seconds"] is not None:
        try:
            await svc.set_video_poll_timeout_seconds(patch["video_poll_timeout_seconds"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_t("video_poll_timeout_minimum")) from exc

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
    return await get_system_config(svc=svc, locale=locale, session=session)
