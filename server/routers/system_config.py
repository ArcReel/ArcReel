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
from server.services.tasks.generation_context import audio_backend_profile

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
    """Route dependency for version reading; inject a callable rather than a version value because routes own read-failure handling."""
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
    """An available model and its task-type bucket membership, shared by unfiltered defaults and bucket-filtered layers."""

    option: str  # "provider_id/model_id"
    media_type: str
    fit: BucketFit  # Task-type bucket decisions, including exclusion reasons.
    display_name: str  # Model name rendered in the request language.
    entry: str | None = None  # Matching entry reference (model vendor/official model name); None without a match.

    @property
    def provider(self) -> str:
        return self.option.split("/", 1)[0]

    @property
    def buckets(self) -> frozenset[GenerationTypeBucket]:
        return self.fit.buckets


@dataclass(frozen=True)
class _RetiringModel:
    """A retiring or retired provider model, whether enabled or a candidate; supplies retirement notices at references."""

    option: str  # "provider_id/model_id"
    media_type: str
    lifecycle: Lifecycle


async def _enumerate_candidates(
    svc: ConfigService, session: AsyncSession, locale: str = DEFAULT_LOCALE
) -> tuple[list[_ModelCandidate], dict[str, str]]:
    """List available models (enabled provider models under ready built-in providers and custom providers), with task-type bucket membership.

    Candidates include only enabled, nonretired provider models, at both default and bucket layers
    (capability filtering applies only to buckets). Built-in status derives from ``disabled_models``
    (see :func:`lib.catalog.builtin_models.disabled_builtin_models`); custom providers use model-row enabled
    state and matched-entry lifecycle (:func:`lib.custom_provider.default_models.model_lifecycle`).

    Provider/model names use ``translate_or``, as the ``/providers`` catalog does: render translated names
    in the request language when available; otherwise return original registry/DB names (brands and user-entered custom names)."""
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
    """List retiring/retired models across all built-in and custom providers, regardless of enabled state.

    Settings/project references may target disabled, retired, or unconfigured-provider models absent from
    candidates; notices must still appear. Models removed from official data with only retained local
    entries are unlisted here; :func:`_unlisted_references` supplements them from referenced values."""
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
                continue  # Call endpoint deleted: provider model lists omit this row too.
            state = model_lifecycle(model, media_type)
            if state.status != "available":
                retiring.append(_RetiringModel(f"{make_provider_id(provider.id)}/{model.model_id}", media_type, state))
    return retiring


async def _referenced_models(svc: ConfigService) -> dict[str, str]:
    """``provider/model`` referenced by global/project model-setting fields → model media type (from the field).

    Read each project's ``project.json``; time scales with project count."""
    settings = await svc.get_all_settings()
    pairs: list[tuple[str, object]] = [(key, settings.get(key)) for key in GLOBAL_MODEL_SETTING_KEYS]
    for project in await asyncio.to_thread(project_model_references):
        pairs.extend(project.references.items())
    return {value: MODEL_SETTING_MEDIA_TYPES[key] for key, value in pairs if isinstance(value, str) and value}


def _unlisted_references(references: Mapping[str, str]) -> tuple[list[_RetiringModel], dict[str, str]]:
    """Referenced models absent from built-in provider lists: show entry-based retirement notices for names matching locally retired entries; otherwise unavailable.

    Return (entry-noticed models, unavailable ``provider/model`` → media type). Unlisted models cannot be
    called; even matching retiring or active entries count as unavailable. Custom-provider references are
    evaluated by model row instead (:func:`_enumerate_retiring_models`)."""
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


#: Reference-notice states: retiring, retired, or unavailable when unlisted without a locally retired matching entry.
NoticeStatus = Literal["retiring", "discontinued", "unavailable"]


class LifecycleNotice(BaseModel):
    """Retirement notice at a reference: status, shutdown date, official successor, and one-click replacement targets for each reference location."""

    status: NoticeStatus
    shutdown_date: date | None
    #: Official successor entry reference (model vendor/official model name) and entry name; None without a registered successor.
    successor: str | None
    successor_name: str | None
    #: Reference location (``default`` for defaults, otherwise this media type's task bucket) → one-click target ``provider/model``;
    #: None when the same provider has no successor-matching model eligible there; display only the successor name.
    switch_targets: dict[str, str | None]


def _reference_layers(media_type: str) -> tuple[str, ...]:
    """Reference locations for one media type: default layer plus its task-type buckets."""
    return ("default", *BUCKETS_BY_MEDIA_TYPE.get(media_type, ()))


def _lifecycle_notices(
    retiring: list[_RetiringModel], candidates: list[_ModelCandidate], unavailable: Mapping[str, str] | None = None
) -> dict[str, LifecycleNotice]:
    """``provider/model`` → retirement notice. Select replacement targets only from candidates, sharing
    eligibility with that location's dropdown (candidate enumeration checks enabled/nonretired status,
    media type, endpoint, minimum app version; bucket membership controls task-type buckets).
    ``unavailable`` (``provider/model`` → media type) models have no lifecycle information: show unavailable
    without successors or replacement targets."""
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
            status="discontinued" if state.discontinued else "retiring",  # Include retiring/retired models only.
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
    """``"provider_id/model_id"`` → rendered model name, the table shared by both endpoints.

    Do not narrow by media type: text-tier and narration dropdowns on the same screen use
    ``/system/config`` candidates and share this translation table; missing keys fall back to model IDs."""
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
    """A default-layer model excluded from a task-type bucket because capabilities do not satisfy it."""

    option: str  # "provider_id/model_id"
    # Unmet capability dimensions, using capability API keys; empty means indeterminate (retired endpoint or channel declaring no capabilities).
    unmet: list[BucketDimension]


class MediaCandidates(BaseModel):
    """Candidates for one media_type: full default layer, filtered subsets per task-type bucket, and excluded models/reasons for each bucket."""

    default: list[str]
    buckets: dict[GenerationTypeBucket, list[str]]
    # Include only default-layer models: disabled, retired, or below-minimum-app-version models are absent there and here.
    filtered: dict[GenerationTypeBucket, list[FilteredCandidate]]


class ModelCandidatesResponse(BaseModel):
    image: MediaCandidates
    video: MediaCandidates
    # Provider ID → request-language name, using the same translate_or path as options.
    provider_names: dict[str, str]
    # "provider_id/model_id" → request-language model name; keys cover candidates across all media types (including text/audio
    # used only by options), letting frontend dropdowns display translated names.
    model_names: dict[str, str]
    # "provider_id/model_id" → retirement notices for retiring/retired provider models (including noncandidates), plus unlisted
    # references in settings/projects (retired or unavailable), across every media type; show in referencing dropdowns.
    lifecycle: dict[str, LifecycleNotice]


class SystemConfigPatchRequest(BaseModel):
    default_video_backend: str | None = None
    # Video task buckets (docs/adr/0054): i2v = image-to-video / grids, r2v = reference-to-video; empty = default-layer fallback.
    default_video_backend_i2v: str | None = None
    default_video_backend_r2v: str | None = None
    default_image_backend: str | None = None
    default_image_backend_t2i: str | None = None
    default_image_backend_i2i: str | None = None
    default_text_backend: str | None = None
    default_audio_backend: str | None = None
    narration_voice: str | None = None
    narration_speed: float | None = None
    # Market-source GitHub raw proxy prefix: empty string = direct connection.
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
    # Text task tiers (docs/adr/0051): call sites have fixed code-level tiers; configure only each tier's backend here;
    # unset tiers fall back to default_text_backend.
    text_backend_simple: str | None = None
    text_backend_complex: str | None = None


# Setting keys that map directly to string DB settings
#
# DEPRECATED: anthropic_api_key / anthropic_base_url migrated to agent_anthropic_credentials
# (spec 2026-05-11-agent-url-config-optimization). Keep anthropic_base_url reads/writes only for old-client
# compatibility; new UI uses /api/v1/agent/credentials/*. Planned removal in 0.14.0: anthropic_api_key /
# anthropic_base_url fields; retain anthropic_*_model (still managed by Section 2 Model Routing).
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
    # New credential-store compatibility: when old system_settings is empty but agent_anthropic_credentials has an active entry,
    # count as is_set too, avoiding false unconfigured dashboard indicators.
    if not anthropic_key:
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        active_cred = await AgentCredentialRepository(session).get_active()
        if active_cred is not None:
            anthropic_key = active_cred.api_key

    # Speech-rate settings store strings; treat corrupt values (e.g. manual DB edits) as unset.
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
    """Candidate source for task-type bucket dropdowns: all defaults plus capability-filtered model lists per bucket.

    Defaults are unfiltered because that layer promises no capabilities; resolution gates report unsupported
    capabilities. Only buckets promise executable configured combinations, hence bucket filtering. Default
    models excluded from a bucket appear in ``filtered`` with unmet capability dimensions, using the same
    :class:`BucketFit` evaluation as bucket admission.

    Also include retirement notices at references (``lifecycle``), choosing one-click targets from the same
    candidates. For settings/project references to unlisted models, supplement notices by matching values
    to retained local entries (:func:`_unlisted_references`)."""
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
    """Models automatically selected when project/global layers are all empty for each purpose (``provider/model``); None without an available provider."""

    t2i: str | None
    i2i: str | None
    i2v: str | None
    r2v: str | None
    text: str | None


@router.get("/system/config/auto-backends", response_model=AutoBackendsResponse)
async def get_auto_backends() -> AutoBackendsResponse:
    """Actual automatically selected models, matching execution resolution; settings display them and query capabilities accordingly."""
    resolved = await ConfigResolver(async_session_factory).auto_resolved_backends()
    return AutoBackendsResponse(
        **{purpose: f"{pair[0]}/{pair[1]}" if pair else None for purpose, pair in resolved.items()}
    )


class NarrationDefaultsResponse(BaseModel):
    """Prefills for new TTS projects; ``audio_backend`` is None without any configured audio provider."""

    audio_backend: str | None
    narration_voice: str
    narration_speed: float | None


@router.get("/system/narration-defaults", response_model=NarrationDefaultsResponse)
async def get_narration_defaults(
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> NarrationDefaultsResponse:
    """Global default audio model, narration voice, and speech rate: creation wizards and project settings switching to TTS use these to prefill project snapshots."""
    try:
        backend, voice, speed = await default_narration_tts(ConfigResolver(async_session_factory))
        audio_backend: str | None = f"{backend.provider_id}/{backend.model_id}"
    except ValueError:
        audio_backend, voice, speed = None, await svc.get_narration_voice(), await svc.get_narration_speed()
    return NarrationDefaultsResponse(audio_backend=audio_backend, narration_voice=voice, narration_speed=speed)


class TtsVoiceOption(BaseModel):
    id: str
    label: str


class TtsModelCapabilitiesResponse(BaseModel):
    supports_speed: bool
    # Voice catalog for this model's endpoint (endpoints under one provider may differ); settings list choices from it, while free-form voices remain allowed.
    voices: list[TtsVoiceOption]


@router.get("/system/tts-model-capabilities", response_model=TtsModelCapabilitiesResponse)
async def get_tts_model_capabilities(backend: str, _t: Translator) -> TtsModelCapabilitiesResponse:
    """Whether the selected TTS model (``provider/model``) forwards speech rate to the provider, and its available voices.

    Settings disable speech-rate input when unsupported. Some providers use lib/i18n translation keys as
    voice labels, rendered by request locale; for providers using display names directly, _t() returns them
    unchanged when no key matches."""
    pair = parse_tts_backend(backend)
    if pair is None:
        raise UnprocessableError("narration_tts_model_required")
    try:
        profile = await audio_backend_profile(*pair)
    except ModelNotInCatalogError:
        # Referenced model absent from provider list: app-level handler returns model-reselection 400 (ValueError subclass, caught before ValueError).
        raise
    except ValueError as exc:
        raise UnprocessableError("narration_tts_model_invalid") from exc
    return TtsModelCapabilitiesResponse(
        supports_speed=AudioCapability.SPEECH_SPEED in profile.capabilities,
        voices=[TtsVoiceOption(id=v.id, label=_t(v.label)) for v in profile.voices],
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


def _is_valid_proxy_prefix(prefix: str) -> bool:
    if not prefix.isprintable() or any(char.isspace() for char in prefix):
        return False
    try:
        parts = urlsplit(prefix)
        parts.port  # noqa: B018 -- Access validates the port; invalid ports raise ValueError
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

    # Narration voice: string ID per provider documentation; empty clears it to the service default.
    if "narration_voice" in patch:
        await svc.set_setting("narration_voice", str(patch["narration_voice"] or "").strip())

    # Market-source GitHub raw proxy prefix: prepended only to raw.githubusercontent.com URLs; require an https URL with hostname, no whitespace/control characters or credentials
    # (GET echoes this value verbatim, so it is not credential storage), and no query/fragment (append raw URL as a path); empty clears it.
    if "market_github_proxy_prefix" in patch:
        prefix = str(patch["market_github_proxy_prefix"] or "").strip()
        if prefix and not _is_valid_proxy_prefix(prefix):
            raise UnprocessableError("market_github_proxy_prefix_invalid")
        await svc.set_setting(PROXY_PREFIX_SETTING, prefix)

    # Narration rate: positive finite-number hygiene only (reject 0/negative/inf/nan); providers constrain actual ranges; null clears it.
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
