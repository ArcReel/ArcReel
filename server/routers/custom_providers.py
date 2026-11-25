"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连通性检查端点。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import (
    VideoAudioMode,
)
from lib.backends.artifact_download_guard import artifact_http_client
from lib.backends.http_status_errors import raise_for_status_redacted
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import custom_image_quality_options
from lib.backends.video_capabilities import projected_video_capabilities
from lib.capabilities.override_schema import LEGACY_AUDIO_KEYS, OVERRIDE_KEYS, same_override_value
from lib.catalog.model_id import has_dot_segment
from lib.catalog.query import binding_key, find_bound_entry
from lib.config.model_setting_keys import PROJECT_MODEL_SETTING_KEYS
from lib.config.repository import mask_secret
from lib.config.resolver import VoiceConsistency, derive_voice_consistency
from lib.custom_provider import is_custom_endpoint, make_provider_id
from lib.custom_provider.capabilities import (
    custom_image_snapshot,
    custom_video_snapshot,
    filter_valid_overrides,
)
from lib.custom_provider.capability_view import (
    CapabilityView,
    OverrideRejected,
    custom_capability_view,
    overridable_keys,
    validate_snapshot_overrides,
    validate_text_overrides,
)
from lib.custom_provider.discovery_formats import endpoint_attachment_holds, is_comfyui_protocol
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row, resolve_endpoint_spec
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    EndpointSpec,
    endpoint_spec_to_dict,
    get_endpoint_spec,
    infer_endpoint,
    static_media_type,
)
from lib.custom_provider.model_facts import (
    COMFYUI_VIDEO_OVERRIDE_KEYS,
    matched_entry_summary,
    model_fact_overrides,
    model_fact_type_name,
    model_fact_value_matches,
    text_capability_snapshot,
)
from lib.custom_provider.path_overrides import BaseUrlRule, effective_path_overrides, path_problem
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.custom_provider import CustomProviderModel
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.infra.api_errors import BadRequestError
from lib.infra.async_thread import run_sync_transaction
from server.i18n import Translator


def _validate_endpoint(value: str) -> str:
    """Pydantic 层校验内置键或合法的自定义端点键形状；存在性由写入事务异步确认。"""
    if value not in ENDPOINT_REGISTRY and not is_custom_endpoint(value):
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google", "comfyui"]

MaxWorkers = Annotated[int | None, Field(default=None, ge=1)]

CAPABILITY_OVERRIDE_ALLOWLIST = frozenset().union(*OVERRIDE_KEYS.values(), LEGACY_AUDIO_KEYS)


_IMAGE_CAPABILITY_BY_OVERRIDE_KEY: dict[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
    "image_to_image": ImageCapability.IMAGE_TO_IMAGE,
}


def _comfyui_override_keys(media_type: str) -> frozenset[str]:
    """ComfyUI 端点上接受的覆盖维度：只有视频行的可选时长（docs/adr/0104）。"""
    return COMFYUI_VIDEO_OVERRIDE_KEYS if media_type == "video" else frozenset()


def _narrow_to_allowlist(overrides: dict[str, object]) -> dict[str, object]:
    """收窄至开放白名单。写入侧与回显侧共用，两处键集合同源而非各自推导后靠注释约定一致。"""
    return {k: v for k, v in overrides.items() if k in CAPABILITY_OVERRIDE_ALLOWLIST}


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTIVITY_CHECK_TIMEOUT = 15

_COMFYUI_SYSTEM_STATS_PATH = "/system_stats"

_BACKEND_SETTING_KEYS = (
    "default_video_backend",
    "default_image_backend",
    "default_image_backend_t2i",
    "default_image_backend_i2i",
    "default_text_backend",
    "default_audio_backend",
    "text_backend_simple",
    "text_backend_complex",
)

_PROJECT_BACKEND_KEYS = (
    "video_backend",
    "audio_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "default_image_backend",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)

_GLOBAL_BUCKET_REFERENCE_KEYS = (
    *_BACKEND_SETTING_KEYS,
    "default_video_backend_i2v",
    "default_video_backend_r2v",
)


class ModelInput(BaseModel):
    model_id: str
    display_name: str
    endpoint: EndpointType
    is_default: bool = False
    is_enabled: bool = True
    resolution: str | None = None
    capability_overrides: dict[str, object] | None = None
    path_overrides: dict[str, str] | None = None

    @field_validator("path_overrides")
    @classmethod
    def _empty_path_overrides_to_none(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return value or None

    multiplier: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    entry_binding: str | None = None

    @field_validator("entry_binding")
    @classmethod
    def _entry_binding_shape(cls, value: str | None) -> str | None:
        """条目绑定只校验形状「模型厂商/官方模型名」，不校验条目是否还在目录里：官方数据删掉条目后，存量绑定
        随整表 PUT 回传时不能把与它无关的编辑堵在 422 上；读取侧对找不到的绑定回到按名字对应。"""
        value = (value or "").strip() or None
        if value is not None and binding_key(value, "text") is None:
            raise ValueError("entry_binding must look like 'maker/name'")
        return value

    @field_validator("capability_overrides")
    @classmethod
    def _keep_open_capabilities_only(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        """静默剔除开放白名单外的覆盖键，空字典收敛为 None。

        白名单外的键只能由手工改库产生：界面既不显示也没有入口能删，写入侧若为此报错，用户
        会被堵在一个自己无法处置的 422 上，连改显示名都保存不了。剔除即这类键的唯一出口，
        落库的覆盖字典因此只含当前开放的维度。剔除对用户静默，但落一条 warning：这是数据被
        丢弃的唯一痕迹，运维排查"我改的库值怎么没了"时需要它。
        """
        if not value:
            return None
        kept = _narrow_to_allowlist(value)
        if dropped := sorted(value.keys() - kept.keys()):
            logger.warning("能力覆盖含未开放键，保存时已剔除: %s", ", ".join(dropped))
        return kept or None

    def to_db_dict(self, endpoint_spec: EndpointSpec) -> dict:
        """返回适合写入数据库的字典。

        价格不在模型行上：模型按所对应条目的价目表计价（docs/adr/0106）。可选时长与最大输出 token 只以能力覆盖
        的形式保存，保存时不预填：预设只用于新建模型条目时预填与运行时回落（``docs/adr/0105``）。

        ComfyUI 视频端点的可选时长默认只含 workflow 的原生时长，用户增删后的档位存为 ``durations`` 覆盖
        （``docs/adr/0082``、``0104``）：与端点推导的档位相同时不存；端点驱动不了时长（档位为空集）时一律不存，
        这一维由 workflow 固定。

        媒体类型读调用方已解析好的 spec：``ce-`` 端点的媒体类型写在它那份定义里，键前缀推不出来。
        """
        d = self.model_dump()
        overrides = d.get("capability_overrides")
        if endpoint_spec.kind == COMFYUI_KIND and isinstance(overrides, dict) and "durations" in overrides:
            endpoint_durations = endpoint_spec.endpoint_durations or []
            durations = overrides.get("durations")
            if not endpoint_durations or (
                isinstance(durations, list) and sorted(set(durations)) == sorted(set(endpoint_durations))
            ):
                overrides = {k: v for k, v in overrides.items() if k != "durations"}
            d["capability_overrides"] = overrides or None
        return d


class CreateProviderRequest(BaseModel):
    display_name: str
    discovery_format: DiscoveryFormatLiteral
    base_url: str
    api_key: str
    models: list[ModelInput] = []
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class UpdateProviderRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class FullUpdateProviderRequest(BaseModel):
    """PUT 全量更新：provider 元数据，以及可选的模型列表。

    ``models`` 不传（``None``）时不改模型：供应商详情的模型经供应商模型接口逐个保存，整表回传会覆盖其间单行的
    启停与设置。传列表时整体替换。
    """

    display_name: str
    base_url: str
    api_key: str | None = None
    models: list[ModelInput] | None = None
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class ConnectivityCheckRequest(BaseModel):
    discovery_format: str
    base_url: str
    api_key: str


class EndpointRequestDescriptor(BaseModel):
    """调用端点发出的一条可覆盖路径的请求，与 lib.custom_provider.path_overrides.EndpointRequest 对齐。"""

    name: str
    display_name_key: str
    default_path: str
    placeholders: list[str]
    base_url_rule: BaseUrlRule


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    endpoint: str
    is_default: bool
    is_enabled: bool
    resolution: str | None = None
    system_capabilities: dict[str, object] | None = None
    audio_track: VideoAudioMode | None = None
    reference_route_audio_track: VideoAudioMode | None = None
    voice_consistency: VoiceConsistency | None = None
    capability_overrides: dict[str, object] | None = None
    path_overrides: dict[str, str] | None = None
    requests: list[EndpointRequestDescriptor] = []
    multiplier: float = 1.0
    entry_binding: str | None = None
    matched_entry: dict[str, object] | None = None
    qualities: list[str] = []


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    discovery_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None
    image_max_workers: int | None = None
    video_max_workers: int | None = None
    audio_max_workers: int | None = None


class ConnectivityCheckResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict] = []
    not_applicable: bool = False
    reason: str | None = None


class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class EndpointDescriptor(BaseModel):
    """前端从 catalog API 拿到的单条 endpoint 描述（与 lib.custom_provider.endpoints.EndpointSpec 对齐，去掉闭包）。"""

    key: str
    media_type: str
    family: str
    kind: str
    source: Literal["builtin", "custom"] = "builtin"
    display_name_key: str
    display_name: str | None = None
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None
    end_image_capable: bool = False
    size_fixed: bool = False
    duration_fixed: bool = False
    duration_frame_rate_missing: bool = False
    duration_tier_empty: bool = False
    native_resolution: str | None = None
    requests: list[EndpointRequestDescriptor] = []


class EndpointCatalogResponse(BaseModel):
    endpoints: list[EndpointDescriptor]


def _effective_overrides_for_response(
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> dict[str, object] | None:
    if endpoint_spec is None:
        try:
            endpoint_spec = get_endpoint_spec(endpoint)
        except ValueError:
            return None
    media_type = endpoint_spec.media_type
    if media_type == "video":
        return (
            filter_valid_overrides(
                endpoint=endpoint,
                model_id=model_id,
                overrides=overrides,
                endpoint_spec=endpoint_spec,
                entry_binding=entry_binding,
            )
            or None
        )
    if media_type == "image":
        try:
            snapshot = custom_image_snapshot(
                endpoint=endpoint,
                model_id=model_id,
                overrides=overrides,
                endpoint_spec=endpoint_spec,
                entry_binding=entry_binding,
            )
        except ValueError:
            return None
        return snapshot.override_dict() or None
    if endpoint_spec.kind == COMFYUI_KIND:
        return None
    if media_type == "text":
        return text_capability_snapshot(model_id, overrides, entry_binding=entry_binding).override_dict() or None
    return dict(model_fact_overrides(overrides, media_type, model_id=model_id)) or None


async def _read_endpoint_specs(
    session: AsyncSession,
    models,
    *,
    on_unknown: Callable[[str], None] | None = None,
) -> dict[str, EndpointSpec]:
    """逐个不同的 endpoint 解析一次 spec，供整批模型共用。

    ``on_unknown`` 决定解析不出来时的去向：回显侧只记日志、把该行按存量脏配置降级渲染；
    写入侧传入抛 422 的回调，不让一行引用不存在的端点入库。
    """
    repo = CustomEndpointRepository(session)
    specs: dict[str, EndpointSpec] = {}
    for endpoint in {model.endpoint for model in models}:
        try:
            specs[endpoint] = await resolve_endpoint_spec(endpoint, repo.get)
        except ValueError:
            if on_unknown is not None:
                on_unknown(endpoint)
            logger.warning("无法解析模型 endpoint，按存量脏配置回显: endpoint=%r", endpoint)
    return specs


def _model_to_response(
    m,
    endpoint_spec: EndpointSpec | None = None,
) -> ModelResponse:
    requests = endpoint_spec.requests if endpoint_spec is not None else ()
    try:
        media_type = endpoint_spec.media_type if endpoint_spec is not None else static_media_type(m.endpoint)
    except ValueError:
        media_type = None
    audio_caps = None
    system = None
    applied = None
    if media_type == "video":
        try:
            snapshot = custom_video_snapshot(
                endpoint=m.endpoint,
                model_id=m.model_id,
                overrides=m.capability_overrides,
                endpoint_spec=endpoint_spec,
                entry_binding=m.entry_binding,
            )
            audio_caps = projected_video_capabilities(snapshot)
            system = {
                k: v
                for k, v in asdict(projected_video_capabilities(snapshot, base=True)).items()
                if k not in LEGACY_AUDIO_KEYS
            }
            applied = snapshot.override_dict() or None
        except ValueError:
            logger.warning("无法判定视频能力: endpoint=%r model_id=%r", m.endpoint, m.model_id)
    else:
        applied = _effective_overrides_for_response(
            m.endpoint, m.model_id, m.capability_overrides, endpoint_spec, m.entry_binding
        )
    return ModelResponse(
        audio_track=audio_caps.audio_track_for_route("i2v") if audio_caps else None,
        reference_route_audio_track=audio_caps.audio_track_for_route("r2v") if audio_caps else None,
        voice_consistency=derive_voice_consistency(
            reference_audio_mode=audio_caps.reference_audio_mode,
            generation_mode=None,
            has_audio=audio_caps.audio_track_for_route("i2v").has_audio_track,
            character_voice_binding=None,
        )
        if audio_caps
        else None,
        path_overrides=effective_path_overrides(m.path_overrides, requests) or None,
        requests=[EndpointRequestDescriptor(**asdict(r)) for r in requests],
        system_capabilities=system,
        capability_overrides=applied,
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        endpoint=m.endpoint,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        resolution=m.resolution,
        multiplier=m.multiplier,
        entry_binding=m.entry_binding,
        matched_entry=(
            matched_entry_summary(m.model_id, media_type, m.entry_binding) if media_type is not None else None
        ),
        qualities=(
            list(custom_image_quality_options(m.model_id, endpoint_spec=endpoint_spec, entry_binding=m.entry_binding))
            if endpoint_spec is not None and endpoint_spec.media_type == "image"
            else []
        ),
    )


def _provider_to_response(
    provider,
    models,
    endpoint_specs: Mapping[str, EndpointSpec] | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        discovery_format=provider.discovery_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[
            _model_to_response(
                m,
                None if endpoint_specs is None else endpoint_specs.get(m.endpoint),
            )
            for m in models
        ],
        created_at=dt_to_iso(provider.created_at),
        image_max_workers=provider.image_max_workers,
        video_max_workers=provider.video_max_workers,
        audio_max_workers=provider.audio_max_workers,
    )


async def _clear_global_provider_refs(
    session: AsyncSession, provider_id: int, model_ids: set[str] | None = None
) -> None:
    """清空全局 settings 中指向该 provider 的悬空引用，只动 _BACKEND_SETTING_KEYS；不提交事务。

    ``model_ids`` 为 None 时清理指向该 provider 任一模型的键（删除供应商），否则只清指向其中
    模型的键（保存时删掉的模型）。
    """
    if model_ids is not None and not model_ids:
        return
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    prefix = f"{make_provider_id(provider_id)}/"
    for key in _BACKEND_SETTING_KEYS:
        val = await svc.get_setting(key, "")
        if val.startswith(prefix) and (model_ids is None or val[len(prefix) :] in model_ids):
            await svc.set_setting(key, "")


async def _rename_global_provider_refs(session: AsyncSession, provider_id: int, old_id: str, new_id: str) -> None:
    """模型改 ID 后，把全局 settings 里指向旧 ID 的引用改写成新 ID（含视频桶键）；不提交事务。"""
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    prefix = make_provider_id(provider_id)
    old_ref, new_ref = f"{prefix}/{old_id}", f"{prefix}/{new_id}"
    for key in _GLOBAL_BUCKET_REFERENCE_KEYS:
        if await svc.get_setting(key, "") == old_ref:
            await svc.set_setting(key, new_ref)


def _mutate_all_projects(mutate: Callable[[dict], None]) -> None:
    """对每个项目的 project.json 应用 ``mutate``；读取失败或项目不可写的跳过（非致命）。"""
    from lib.project.project_manager import get_project_manager

    pm = get_project_manager()
    for proj_name in pm.list_projects():
        try:
            pm.update_project(proj_name, mutate)
        except Exception:
            logger.warning("同步项目 %s 的模型引用失败，已跳过", proj_name, exc_info=True)


def _cleanup_project_refs(prefix: str, setting_keys: tuple[str, ...], model_ids: set[str] | None = None) -> None:
    """删除 provider 或其中的模型后，清理所有项目 project.json 中的悬空引用。

    ``model_ids`` 为 None 时清理指向该 provider 任一模型的引用（删除供应商），否则只清指向其中模型的引用。
    ``model_settings`` 里这些模型的条目不受 ``setting_keys`` 约束一律清掉：被删模型的分辨率覆盖已没有用处，
    留着还会被之后同 ID 的新模型接管。
    """
    if model_ids is not None and not model_ids:
        return

    def _hits(ref: object) -> bool:
        return (
            isinstance(ref, str) and ref.startswith(prefix) and (model_ids is None or ref[len(prefix) :] in model_ids)
        )

    def _mutate(p: dict) -> None:
        for key in setting_keys:
            if _hits(p.get(key, "")):
                p.pop(key, None)
        model_settings = p.get("model_settings")
        if isinstance(model_settings, dict):
            for ref in [r for r in model_settings if _hits(r)]:
                del model_settings[ref]

    _mutate_all_projects(_mutate)


def _rename_project_refs(prefix: str, old_id: str, new_id: str) -> None:
    """模型改 ID 后，把所有项目里指向旧 ID 的引用改写成新 ID：10 个引用键与 ``model_settings`` 的键。"""
    old_ref, new_ref = f"{prefix}{old_id}", f"{prefix}{new_id}"

    def _mutate(p: dict) -> None:
        for key in PROJECT_MODEL_SETTING_KEYS:
            if p.get(key) == old_ref:
                p[key] = new_ref
        model_settings = p.get("model_settings")
        if isinstance(model_settings, dict) and old_ref in model_settings:
            model_settings[new_ref] = model_settings.pop(old_ref)

    _mutate_all_projects(_mutate)


async def cleanup_project_model_refs(provider_id: int, model_ids: set[str]) -> None:
    """已提交的模型删除之后，清理项目里指向这些模型的引用（同步文件 I/O，放到线程池）。"""
    if model_ids:
        await run_sync_transaction(
            _cleanup_project_refs, f"{make_provider_id(provider_id)}/", _PROJECT_BACKEND_KEYS, model_ids
        )


async def rename_project_model_refs(provider_id: int, old_id: str, new_id: str) -> None:
    """已提交的模型改 ID 之后，把项目里指向旧 ID 的引用改写成新 ID（同步文件 I/O，放到线程池）。"""
    if old_id != new_id:
        await run_sync_transaction(_rename_project_refs, f"{make_provider_id(provider_id)}/", old_id, new_id)


def _check_duplicate_model_ids(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验模型列表：同一供应商下 API 模型名唯一；启用模型有合法 model_id 和 endpoint。"""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail=_t("model_id_required"))
        if m.is_enabled and not m.endpoint:
            raise HTTPException(status_code=422, detail=_t("endpoint_required"))
        if has_dot_segment(m.model_id):
            raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=m.model_id))
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=m.model_id))
        if m.model_id:
            seen.add(m.model_id)


def _check_capability_overrides(
    overrides: dict[str, object] | None,
    endpoint: str,
    model_id: str,
    _t: Callable[..., str],
    spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
    carried: Mapping[str, object] | None = None,
    pending: CatalogEntry | None = None,
    explicit: bool = True,
) -> dict[str, object] | None:
    if not overrides:
        return None
    resolved_spec = spec or get_endpoint_spec(endpoint)
    media_type = resolved_spec.media_type
    if media_type in ("video", "image"):
        try:
            if media_type == "video":
                snapshot = custom_video_snapshot(
                    endpoint=endpoint,
                    model_id=model_id,
                    endpoint_spec=resolved_spec,
                    entry_binding=entry_binding,
                    pending=pending,
                )
            else:
                snapshot = custom_image_snapshot(
                    endpoint=endpoint,
                    model_id=model_id,
                    endpoint_spec=resolved_spec,
                    entry_binding=entry_binding,
                    pending=pending,
                )
            return validate_snapshot_overrides(
                snapshot,
                overrides,
                model_id=model_id,
                endpoint=endpoint,
                carried=carried,
                explicit=explicit,
            )
        except OverrideRejected as exc:
            raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_not_applicable",
                    model_id=model_id,
                    endpoint=endpoint,
                    media_type=media_type,
                    capability=str(exc),
                ),
            ) from exc
    if media_type == "text" and resolved_spec.kind != COMFYUI_KIND:
        try:
            snapshot = text_capability_snapshot(model_id, entry_binding=entry_binding, pending=pending)
            return validate_text_overrides(
                snapshot,
                overrides,
                model_id=model_id,
                endpoint=endpoint,
                carried=carried,
                explicit=explicit,
            )
        except OverrideRejected as exc:
            raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc
    if carried:
        valid = _effective_overrides_for_response(endpoint, model_id, overrides, resolved_spec, entry_binding) or {}
        overrides = {
            k: v
            for k, v in overrides.items()
            if k in valid or k not in carried or not same_override_value(k, carried[k], v)
        }
    applicable = overridable_keys(media_type)
    for key, value in overrides.items():
        if key not in applicable:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_not_applicable",
                    model_id=model_id,
                    endpoint=endpoint,
                    media_type=media_type,
                    capability=key,
                ),
            )
        if not model_fact_value_matches(key, value):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_invalid_value",
                    model_id=model_id,
                    capability=key,
                    expected=model_fact_type_name(key),
                ),
            )
        if key in _IMAGE_CAPABILITY_BY_OVERRIDE_KEY and value is True:
            implemented = resolved_spec.image_capabilities or frozenset()
            if _IMAGE_CAPABILITY_BY_OVERRIDE_KEY[key] not in implemented:
                raise HTTPException(
                    status_code=422,
                    detail=_t(
                        "capability_override_image_unsupported",
                        model_id=model_id,
                        endpoint=endpoint,
                        capability=key,
                    ),
                )
    return overrides


def _check_model_capability_overrides(
    models: list[ModelInput],
    _t: Callable[..., str],
    specs: Mapping[str, EndpointSpec],
    stored: Mapping[str, CustomProviderModel] | None = None,
    pending: CatalogEntry | None = None,
    explicit: bool = True,
) -> None:
    for m in models:
        row = (stored or {}).get(m.model_id)
        carried = row.capability_overrides if row and row.endpoint != m.endpoint else None
        m.capability_overrides = _check_capability_overrides(
            m.capability_overrides,
            m.endpoint,
            m.model_id,
            _t,
            specs[m.endpoint],
            m.entry_binding,
            carried if isinstance(carried, Mapping) else None,
            pending,
            explicit,
        )


def _check_model_path_overrides(
    models: list[ModelInput],
    specs: Mapping[str, EndpointSpec],
    _t: Callable[..., str],
) -> None:
    """路径覆盖的写入校验：请求名属于当前端点，路径以 ``/`` 开头、占位符在该请求允许的范围内。

    ComfyUI 端点与没有声明请求的端点没有可覆盖的请求，任何覆盖都按请求名不属于端点拒绝。
    """
    for m in models:
        requests = {r.name: r for r in specs[m.endpoint].requests}
        for name, path in (m.path_overrides or {}).items():
            request = requests.get(name)
            if request is None:
                raise HTTPException(
                    status_code=422,
                    detail=_t("path_override_unknown_request", model_id=m.model_id, request=name),
                )
            if problem := path_problem(request, path):
                raise HTTPException(
                    status_code=422,
                    detail=_t(
                        problem,
                        model_id=m.model_id,
                        path=path,
                        allowed=", ".join(f"{{{{ {p} }}}}" for p in request.placeholders),
                    ),
                )


def _drop_rebound_path_overrides(m: ModelInput, stored: CustomProviderModel | None) -> ModelInput:
    """更换调用端点时丢弃原有路径覆盖（docs/adr/0101）。

    整表 PUT 会把原有覆盖原样回传：换了端点的行，与库里同名同值的条目即原有覆盖，丢弃；换端点后
    新填的条目保留，交给 :func:`_check_model_path_overrides` 按新端点校验。
    """
    if stored is None or stored.endpoint == m.endpoint or not m.path_overrides:
        return m
    original = stored.path_overrides or {}
    kept = {name: path for name, path in m.path_overrides.items() if original.get(name) != path}
    return m.model_copy(update={"path_overrides": kept or None})


def _check_protocol_constraints(
    models: list[ModelInput],
    discovery_format: str,
    specs: Mapping[str, EndpointSpec],
    _t: Callable[..., str],
) -> None:
    """ComfyUI 协议特有的两条写入约束：端点挂接双向配对，能力覆盖关闭。

    挂接配对是双向的（``docs/adr/0081``）：ComfyUI 端点的运行时只对 ComfyUI 服务有意义，声明式
    端点对 ComfyUI 服务同样无意义。错挂在保存期毫无征兆，要等发起生成才在传输层露出来，故两个
    方向都在写入侧拒。判定本身在 :func:`endpoint_attachment_holds`，此处只负责挑出违规的那个方向。

    本判定须排在 :func:`_check_model_capability_overrides` 之前：ComfyUI 端点的能力位全空，
    它的覆盖同样过不了通用校验，先判协议才给得出「该协议不支持覆盖」这条专用文案，而不是
    「endpoint 不支持尾帧」。

    能力覆盖对该协议关闭：ComfyUI 端点的能力由节点绑定推导，覆盖值在执行层没有对应物。回显侧
    的对应处置是忽略存量值（见 :func:`lib.custom_provider.capabilities.filter_valid_overrides`）。

    只对 comfyui 协议生效，其余协议的自由挂接与覆盖编辑一概不变。
    """
    comfyui_provider = is_comfyui_protocol(discovery_format)
    for m in models:
        spec = specs[m.endpoint]
        if not endpoint_attachment_holds(endpoint_kind=spec.kind, discovery_format=discovery_format):
            key = (
                "comfyui_provider_requires_comfyui_endpoint"
                if comfyui_provider
                else "comfyui_endpoint_requires_comfyui_provider"
            )
            raise HTTPException(status_code=422, detail=_t(key, model_id=m.model_id, endpoint=m.endpoint))
        if (
            comfyui_provider
            and m.capability_overrides
            and not (
                spec.kind == COMFYUI_KIND and m.capability_overrides.keys() <= _comfyui_override_keys(spec.media_type)
            )
        ):
            raise HTTPException(
                status_code=422,
                detail=_t("capability_overrides_not_supported_for_comfyui", model_id=m.model_id),
            )


async def _resolve_model_endpoint_specs(
    session: AsyncSession,
    models: list[ModelInput],
    _t: Callable[..., str],
) -> dict[str, EndpointSpec]:
    """写入侧解析：任一模型行引用的端点不存在即 422，不落库。"""

    def reject(endpoint: str) -> None:
        raise HTTPException(status_code=422, detail=_t("unknown_endpoint", endpoint=endpoint))

    return await _read_endpoint_specs(session, models, on_unknown=reject)


def _check_unique_defaults(models: list[ModelInput], specs: dict[str, EndpointSpec], _t: Callable[..., str]) -> None:
    """校验默认模型互斥。

    - 非 image endpoint（text / video / audio）：同一 media_type 至多 1 个 is_default=True。
    - image endpoint：image capability 集合两两不相交（即同一 capability 至多 1 个默认）。

    媒体类型与能力位都读 ``specs`` 里已解析好的 spec：``ce-`` 端点的这两项写在定义里，键前缀推
    不出来，内置查表对它一律抛错。``specs`` 由 :func:`_resolve_model_endpoint_specs` 现解析，
    解析不出的行已在那里被 422 拦下。
    """
    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        spec = specs[m.endpoint]
        if spec.media_type != "image":
            text_video_defaults.setdefault(spec.media_type, []).append(m.model_id)
            continue
        image_defaults.append((m.model_id, spec.image_capabilities or frozenset()))

    duplicates: dict[str, list[str]] = {mt: ids for mt, ids in text_video_defaults.items() if len(ids) > 1}

    cap_to_ids: dict[ImageCapability, list[str]] = {}
    for mid, caps in image_defaults:
        for c in caps:
            cap_to_ids.setdefault(c, []).append(mid)
    conflict_ids = [mid for ids in cap_to_ids.values() if len(ids) > 1 for mid in ids]
    if len(image_defaults) > 1 and any(not caps for _mid, caps in image_defaults):
        conflict_ids = [mid for mid, _caps in image_defaults]
    if conflict_ids:
        duplicates["image"] = list(dict.fromkeys(conflict_ids))

    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )


async def invalidate_provider_caches(request: Request) -> None:
    """清空 backend 实例缓存 + 刷新 worker 限流配置。"""
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


CUSTOM_MODEL_SETTING_FIELDS = frozenset(
    {
        "model_id",
        "endpoint",
        "is_default",
        "is_enabled",
        "resolution",
        "capability_overrides",
        "path_overrides",
        "multiplier",
        "entry_binding",
    }
)


def _row_to_input(row: CustomProviderModel, changes: Mapping[str, object] | None = None) -> ModelInput:
    data: dict[str, object] = {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "endpoint": row.endpoint,
        "is_default": row.is_default,
        "is_enabled": row.is_enabled,
        "resolution": row.resolution,
        "capability_overrides": row.capability_overrides,
        "path_overrides": row.path_overrides,
        "multiplier": row.multiplier,
        "entry_binding": row.entry_binding,
    }
    data.update(changes or {})
    return ModelInput.model_validate(data)


def _default_group_conflicts(target: ModelInput, other: ModelInput, specs: Mapping[str, EndpointSpec]) -> bool:
    """两个默认模型是否落在同一个默认分组里（规则同 :func:`_check_unique_defaults`）。"""
    target_spec, other_spec = specs[target.endpoint], specs[other.endpoint]
    if target_spec.media_type != other_spec.media_type:
        return False
    if target_spec.media_type != "image":
        return True
    target_caps = target_spec.image_capabilities or frozenset()
    other_caps = other_spec.image_capabilities or frozenset()
    return not target_caps or not other_caps or bool(target_caps & other_caps)


async def update_custom_model(
    session: AsyncSession,
    provider_id: int,
    model_id: str,
    changes: Mapping[str, object],
    _t: Callable[..., str],
    *,
    pending: CatalogEntry | None = None,
) -> CustomProviderModel:
    """保存一个自定义供应商模型的供应商设置，不提交事务；返回保存后的模型行。

    ``changes`` 只含要改的字段（见 :data:`CUSTOM_MODEL_SETTING_FIELDS`），校验规则与整表保存相同。停用时同时
    取消默认；设为默认时同时启用，并取消同一默认分组里其他模型的默认。改了模型 ID 时，全局设置里指向旧 ID 的
    引用随之改写成新 ID；项目里的引用由调用方在提交后用 :func:`rename_project_model_refs` 改写。

    条目绑定只校验形状；是否在目录里、是否与保存后的调用端点同媒体类型，由调用方在提交前核对（见
    ``server/routers/provider_models.py`` 的 ``_apply_binding``），因为同一次保存新建的条目这时还不在目录里。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    rows = await repo.list_models(provider_id)
    row = next((r for r in rows if r.model_id == model_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    patch = dict(changes)
    if isinstance(patch.get("model_id"), str):
        patch["model_id"] = str(patch["model_id"]).strip()
    if patch.get("is_enabled") is False:
        patch["is_default"] = False
    elif patch.get("is_default") is True:
        patch["is_enabled"] = True
    try:
        target = _row_to_input(row, patch)
    except ValidationError as exc:
        raise _model_input_rejected(exc, patch.get("endpoint"), patch.get("entry_binding"), _t) from exc
    if not target.model_id:
        raise HTTPException(status_code=422, detail=_t("model_id_required"))
    if target.model_id != row.model_id and has_dot_segment(target.model_id):
        raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=target.model_id))
    if target.model_id != row.model_id and row.display_name in ("", row.model_id):
        target = target.model_copy(update={"display_name": target.model_id})
    others = [r for r in rows if r is not row]
    if any(r.model_id == target.model_id for r in others):
        raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=target.model_id))

    specs = await _resolve_model_endpoint_specs(session, [target], _t)
    target = _drop_rebound_path_overrides(target, row)
    _check_protocol_constraints([target], provider.discovery_format, specs, _t)
    _check_model_capability_overrides(
        [target], _t, specs, {target.model_id: row}, pending, explicit="capability_overrides" in patch
    )
    _check_model_path_overrides([target], specs, _t)
    if target.is_default:
        default_rows = [r for r in others if r.is_default]
        default_specs = await _read_endpoint_specs(session, default_rows)
        for other in default_rows:
            other_spec = default_specs.get(other.endpoint)
            if other_spec is None:
                continue
            if _default_group_conflicts(
                target, _row_to_input(other), {target.endpoint: specs[target.endpoint], other.endpoint: other_spec}
            ):
                other.is_default = False

    old_model_id = row.model_id
    for key, value in target.to_db_dict(specs[target.endpoint]).items():
        setattr(row, key, value)
    await session.flush()
    if target.model_id != old_model_id:
        await _rename_global_provider_refs(session, provider_id, old_model_id, target.model_id)
    return row


def _model_input_rejected(
    exc: ValidationError, endpoint: object, entry_binding: object, _t: Callable[..., str]
) -> HTTPException:
    """单个模型的调用端点或条目绑定形状不对时的 422：说清是哪一个被拒。"""
    if any(error["loc"][:1] == ("entry_binding",) for error in exc.errors()):
        return HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=entry_binding))
    return HTTPException(status_code=422, detail=_t("unknown_endpoint", endpoint=endpoint))


async def remove_custom_model(session: AsyncSession, provider_id: int, model_id: str, _t: Callable[..., str]) -> None:
    """从自定义供应商移除一个模型，并清空全局设置里指向它的引用；不提交事务。

    项目里的引用由调用方在提交后用 :func:`cleanup_project_model_refs` 清理。
    """
    repo = CustomProviderRepository(session)
    row = await repo.get_model_by_ids(provider_id, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail=_t("provider_model_not_found", model=model_id))
    await repo.delete_model(row.id)
    await _clear_global_provider_refs(session, provider_id, {model_id})


async def _comfyui_default_endpoint(session: AsyncSession, media_type: str | None) -> str | None:
    """ComfyUI 供应商添加模型时的默认调用端点：媒体类型一致的 ComfyUI 端点优先，否则取第一个 ComfyUI 端点。"""
    specs: list[EndpointSpec] = []
    for row in await CustomEndpointRepository(session).list_all():
        try:
            spec = endpoint_spec_from_row(row)
        except ValueError:
            continue
        if spec.kind == COMFYUI_KIND:
            specs.append(spec)
    preferred = next((spec for spec in specs if spec.media_type == media_type), None)
    chosen = preferred or (specs[0] if specs else None)
    return chosen.key if chosen is not None else None


async def add_custom_model(
    session: AsyncSession,
    provider_id: int,
    model_id: str,
    *,
    endpoint: str | None,
    media_type: str | None,
    entry_binding: str | None,
    _t: Callable[..., str],
) -> CustomProviderModel:
    """向自定义供应商添加一个模型，不提交事务；返回新的模型行。

    没给调用端点时按 docs/adr/0105 推断：有条目绑定或名字对应上条目的，在条目的媒体类型内选（两栖模型按
    ``media_type`` 取条目）；都没有时，``media_type`` 给了就在这个媒体类型内选，否则按名字一并推断。ComfyUI 供应商取已导入的 ComfyUI 端点。
    这个媒体类型还没有默认模型时，新模型成为默认。校验规则与整表保存相同。
    """
    from lib.custom_provider.discovery import match_discovered_model

    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    model_id = model_id.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail=_t("builtin_model_id_required"))
    if has_dot_segment(model_id):
        raise HTTPException(status_code=422, detail=_t("model_id_dot_segment", model=model_id))
    rows = await repo.list_models(provider_id)
    if any(r.model_id == model_id for r in rows):
        raise HTTPException(status_code=422, detail=_t("builtin_model_duplicate", model=model_id))

    bound = find_bound_entry(entry_binding, media_type) if entry_binding and media_type else None
    if endpoint is None:
        if is_comfyui_protocol(provider.discovery_format):
            endpoint = await _comfyui_default_endpoint(session, bound.media_type if bound else media_type)
            if endpoint is None:
                raise HTTPException(status_code=422, detail=_t("comfyui_model_endpoint_missing"))
        elif bound is not None:
            endpoint = infer_endpoint(model_id, provider.discovery_format, bound.media_type)
        else:
            entry, by_name = match_discovered_model(model_id, provider.discovery_format, media_type)
            endpoint = (
                infer_endpoint(model_id, provider.discovery_format, media_type)
                if entry is None and media_type is not None
                else by_name
            )
    try:
        target = ModelInput(
            model_id=model_id, display_name=model_id, endpoint=endpoint, is_enabled=True, entry_binding=entry_binding
        )
    except ValidationError as exc:
        raise _model_input_rejected(exc, endpoint, entry_binding, _t) from exc

    specs = await _resolve_model_endpoint_specs(session, [target], _t)
    _check_protocol_constraints([target], provider.discovery_format, specs, _t)
    if entry_binding and find_bound_entry(entry_binding, specs[target.endpoint].media_type) is None:
        raise HTTPException(status_code=422, detail=_t("provider_model_binding_not_found", binding=entry_binding))
    default_rows = [r for r in rows if r.is_default]
    default_specs = await _read_endpoint_specs(session, default_rows)
    taken = any(
        other.endpoint in default_specs
        and _default_group_conflicts(
            target,
            _row_to_input(other),
            {target.endpoint: specs[target.endpoint], other.endpoint: default_specs[other.endpoint]},
        )
        for other in default_rows
    )
    if not taken:
        target = target.model_copy(update={"is_default": True})
    row = CustomProviderModel(provider_id=provider_id, **target.to_db_dict(specs[target.endpoint]))
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class CustomModelSettings:
    """Sheet 上自定义供应商模型独有的设置：调用端点、请求地址与默认分辨率，以及「这个供应商支持的参数」。"""

    endpoint: str
    resolution: str | None
    path_overrides: dict[str, str] | None
    requests: list[EndpointRequestDescriptor]
    capabilities: CapabilityView | None


async def custom_model_settings(session: AsyncSession, row: CustomProviderModel) -> CustomModelSettings:
    """读一个自定义供应商模型的 Sheet 设置；调用端点解析不出时请求地址与参数视图为空。"""
    specs = await _read_endpoint_specs(session, [row])
    spec = specs.get(row.endpoint)
    if spec is None:
        return CustomModelSettings(row.endpoint, row.resolution, None, [], None)
    return CustomModelSettings(
        endpoint=row.endpoint,
        resolution=row.resolution,
        path_overrides=effective_path_overrides(row.path_overrides, spec.requests) or None,
        requests=[EndpointRequestDescriptor(**asdict(r)) for r in spec.requests],
        capabilities=custom_capability_view(
            model_id=row.model_id,
            endpoint=row.endpoint,
            spec=spec,
            raw_overrides=row.capability_overrides,
            applied_overrides=None
            if spec.media_type in ("video", "text")
            else _effective_overrides_for_response(
                row.endpoint, row.model_id, row.capability_overrides, spec, row.entry_binding
            ),
            entry_binding=row.entry_binding,
        ),
    )


@router.get("")
async def list_providers(
    session: AsyncSession = Depends(get_async_session),
):
    """列出所有自定义供应商（含模型列表）。"""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    endpoint_specs = await _read_endpoint_specs(session, [model for _, models in pairs for model in models])
    return {
        "providers": [
            _provider_to_response(
                p,
                models,
                endpoint_specs,
            )
            for p, models in pairs
        ]
    }


# /endpoints 必须先于 /{provider_id} 注册，否则 FastAPI 会把字符串 "endpoints" 当作 provider_id。
@router.get("/endpoints", response_model=EndpointCatalogResponse)
async def list_endpoint_catalog(
    session: AsyncSession = Depends(get_async_session),
) -> EndpointCatalogResponse:
    """暴露两个命名空间的 endpoint 作为前端单一真相源：渲染下拉、显示路径与分组都派生自此返回值。

    内置取自 ENDPOINT_REGISTRY，自定义由 custom_endpoint 表的定义现构造——不做启动时全量装载，
    定义原地改完、目录下次拉取即是新的。单行定义构造不出 spec（只可能来自手工改库）时跳过并
    告警，而不是让整份目录失败：一条坏定义不该把端点下拉整个打空。
    """
    specs = list(ENDPOINT_REGISTRY.values())
    for row in await CustomEndpointRepository(session).list_all():
        try:
            specs.append(endpoint_spec_from_row(row))
        except (KeyError, TypeError, ValueError):
            logger.warning("自定义调用端点定义无法构造 spec，已跳过: id=%s", row.id, exc_info=True)
    return EndpointCatalogResponse(
        endpoints=[EndpointDescriptor(**endpoint_spec_to_dict(spec)) for spec in specs],
    )


@router.get("/endpoints/{endpoint_key}/definition")
async def get_endpoint_definition(endpoint_key: str, _t: Translator) -> dict[str, Any]:
    """取内置声明式端点的定义 JSON，供「复制为我的」原样 POST 成 ce-<id> 副本。

    Python 实现的内置端点没有定义可取，与未知键一并回 404。返回体是定义原样（零封套），
    与导入导出的文件格式同一份东西。
    """
    spec = ENDPOINT_REGISTRY.get(endpoint_key)
    if spec is None or spec.definition is None:
        raise HTTPException(status_code=404, detail=_t("endpoint_definition_not_found", endpoint=endpoint_key))
    return dict(spec.definition)


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """创建自定义供应商，可同时创建模型列表。"""
    specs: dict[str, EndpointSpec] = {}
    if body.models:
        _check_duplicate_model_ids(body.models, _t)
        specs = await _resolve_model_endpoint_specs(session, body.models, _t)
        _check_unique_defaults(body.models, specs, _t)
        _check_protocol_constraints(body.models, body.discovery_format, specs, _t)
        _check_model_capability_overrides(body.models, _t, specs)
        _check_model_path_overrides(body.models, specs, _t)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict(specs[m.endpoint]) for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        discovery_format=body.discovery_format,
        base_url=body.base_url,
        api_key=body.api_key,
        models=model_dicts,
        image_max_workers=body.image_max_workers,
        video_max_workers=body.video_max_workers,
        audio_max_workers=body.audio_max_workers,
    )
    await session.commit()
    await invalidate_provider_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider.id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """获取单个自定义供应商详情。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """更新自定义供应商配置。"""
    repo = CustomProviderRepository(session)
    kwargs = {}
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key

    if not kwargs:
        raise HTTPException(status_code=400, detail=_t("at_least_one_field_required"))

    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))

    await session.commit()
    await invalidate_provider_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        models,
        await _read_endpoint_specs(session, models),
    )


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """原子更新供应商元数据与模型列表（单一事务），并清理全局默认中指向被删模型的引用。

    ``models`` 不传时只更新元数据与并发上限，模型原样不动。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    stored_models = {m.model_id: m for m in await repo.list_models(provider_id)}
    models: list[ModelInput] | None = None
    removed_model_ids: set[str] = set()
    specs: dict[str, EndpointSpec] = {}
    if body.models is not None:
        _check_duplicate_model_ids(body.models, _t)
        specs = await _resolve_model_endpoint_specs(session, body.models, _t)
        _check_unique_defaults(body.models, specs, _t)
        models = [_drop_rebound_path_overrides(m, stored_models.get(m.model_id)) for m in body.models]
        _check_protocol_constraints(models, provider.discovery_format, specs, _t)
        _check_model_capability_overrides(models, _t, specs, stored_models)
        _check_model_path_overrides(models, specs, _t)
    kwargs: dict = {
        "display_name": body.display_name,
        "base_url": body.base_url,
        "image_max_workers": body.image_max_workers,
        "video_max_workers": body.video_max_workers,
        "audio_max_workers": body.audio_max_workers,
    }
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    if models is not None:
        await repo.replace_models(provider_id, [m.to_db_dict(specs[m.endpoint]) for m in models])
        removed_model_ids = stored_models.keys() - {m.model_id for m in models}
        await _clear_global_provider_refs(session, provider_id, removed_model_ids)
    await session.commit()
    await invalidate_provider_caches(request)
    await cleanup_project_model_refs(provider_id, removed_model_ids)
    await session.refresh(provider)
    saved_models = await repo.list_models(provider_id)
    return _provider_to_response(
        provider,
        saved_models,
        await _read_endpoint_specs(session, saved_models),
    )


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """删除自定义供应商（级联删除模型，清理悬空默认配置）。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    prefix = f"{make_provider_id(provider_id)}/"
    await repo.delete_provider(provider_id)
    await _clear_global_provider_refs(session, provider_id)
    await session.commit()
    await invalidate_provider_caches(request)
    await run_sync_transaction(_cleanup_project_refs, prefix, _PROJECT_BACKEND_KEYS)


@router.post("/discover")
async def discover_models_endpoint(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """模型发现：根据 discovery_format + base_url + api_key 查询可用模型。"""
    return await _run_discover(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Anthropic 协议模型发现：Agent 配置专用。

    凭据缺失时 fallback 到 active credential（AgentCredentialRepository）。
    """
    body_key = (body.api_key or "").strip()
    needs_key = not body_key
    needs_url = body.base_url is None

    cred = None
    if needs_key or needs_url:
        from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

        cred = await AgentCredentialRepository(session).get_active()

    api_key = body_key if not needs_key else (cred.api_key if cred else "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

    base_url = body.base_url if not needs_url else (_credential_discovery_base(cred) if cred else None)
    if base_url:
        from lib.config.url_utils import InvalidAnthropicBaseUrlError, validate_anthropic_base_url

        try:
            base_url = validate_anthropic_base_url(base_url)
        except InvalidAnthropicBaseUrlError as exc:
            raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc

    return await _run_discover("anthropic", base_url, api_key, _t)


@router.post("/{provider_id}/discover")
async def discover_models_by_id(
    provider_id: int,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """使用已存储凭证发现指定供应商的可用模型。"""
    return await discover_custom_provider_models(session, provider_id, _t)


async def discover_custom_provider_models(
    session: AsyncSession, provider_id: int, _t: Callable[..., str]
) -> DiscoverResponse:
    """用已存储凭证获取自定义供应商的模型，每个模型带对应条目与默认调用端点。"""
    provider = await CustomProviderRepository(session).get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_discover(provider.discovery_format, provider.base_url, provider.api_key, _t)


@router.post("/test")
async def check_connectivity(
    body: ConnectivityCheckRequest,
    _t: Translator,
):
    """连通性检查：验证 discovery_format + base_url + api_key 是否可达。免费探针，不证明任何模型可生成。"""
    return await _run_connectivity_check(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/{provider_id}/test")
async def check_connectivity_by_id(
    provider_id: int, _t: Translator, session: AsyncSession = Depends(get_async_session)
):
    """用已存储凭证对指定供应商做连通性检查。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_connectivity_check(provider.discovery_format, provider.base_url, provider.api_key, _t)


def _credential_discovery_base(cred: Any) -> str | None:
    """激活凭证用于模型发现的根地址。

    预设凭证未覆盖 base_url（存储值仍等于预设 ``messages_url``）时，模型列表按预设目录的
    ``discovery_url`` 取（DeepSeek 的列表不在 messages 根之下）；自定义或已覆盖的凭证按存储值。
    与前端凭证表单「预填值不算覆盖」同一规则。
    """
    from lib.agent.agent_provider_catalog import get_preset

    preset = get_preset(cred.preset_id) if cred.preset_id else None
    if preset is not None and cred.base_url == preset.messages_url:
        return preset.discovery_url or preset.messages_url
    return cred.base_url


async def _run_discover(
    discovery_format: str,
    base_url: str | None,
    api_key: str,
    _t: Callable[..., str],
    *,
    discover_models_fn: Callable[..., Awaitable[list[dict]]] | None = None,
) -> DiscoverResponse:
    """共用的模型发现逻辑（明文凭证 / 已存储凭证两条入口共用）。"""
    from lib.config.url_utils import InvalidAnthropicBaseUrlError
    from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError, discover_models

    if is_comfyui_protocol(discovery_format):
        return DiscoverResponse(not_applicable=True, reason=_t("discovery_not_applicable_comfyui"))
    try:
        discover = discover_models_fn or discover_models
        models = await discover(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        return DiscoverResponse(models=[m for m in models if not has_dot_segment(str(m.get("model_id", "")))])
    except UnsupportedDiscoveryFormatError as exc:
        raise BadRequestError("invalid_discovery_format", discovery_format=discovery_format) from exc
    except InvalidAnthropicBaseUrlError as exc:
        raise HTTPException(status_code=422, detail=_t("agent_base_url_invalid")) from exc
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg)) from exc


async def _run_connectivity_check(
    discovery_format: str,
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    openai_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
    google_probe: Callable[[str, str, Callable[..., str]], ConnectivityCheckResponse] | None = None,
) -> ConnectivityCheckResponse:
    """共用的连通性检查逻辑：明文凭证与已存储凭证两条入口共用。"""
    try:
        if discovery_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(openai_probe or _check_openai, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif discovery_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(google_probe or _check_google, base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        elif is_comfyui_protocol(discovery_format):
            result = await asyncio.wait_for(
                _check_comfyui(base_url, api_key, _t),
                timeout=_CONNECTIVITY_CHECK_TIMEOUT,
            )
        else:
            return ConnectivityCheckResponse(
                success=False,
                message=_t("connectivity_check_unsupported_format", discovery_format=discovery_format),
            )
        return result
    except TimeoutError:
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连通性检查失败 [%s]: %s", discovery_format, err_msg)
        return ConnectivityCheckResponse(
            success=False,
            message=_t("connectivity_check_failed", err_msg=err_msg),
        )


def _check_openai(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """通过 models.list() 验证 OpenAI 兼容 API。"""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = (client_factory or OpenAI)(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _check_google(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
    *,
    client_factory: Callable[..., Any] | None = None,
) -> ConnectivityCheckResponse:
    """通过 models.list() 验证 Google genai API。"""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options: genai.types.HttpOptionsDict | None = {"base_url": effective_url} if effective_url else None
    client = (client_factory or genai.Client)(api_key=api_key, http_options=http_options)
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectivityCheckResponse(
        success=True,
        message=_t("connectivity_check_ok"),
        model_count=count,
    )


def _comfyui_probe_headers(api_key: str) -> dict[str, str]:
    """ComfyUI 探针的请求头：``api_key`` 作 Bearer 裸打，留空则完全不带凭证。

    端点定义里的 ``auth`` 节在此不渲染（``docs/adr/0081``）：那是端点级的凭据模板，供应商级
    探针拿不到，也不该替某一个端点猜。用自定义请求头做反向代理鉴权的部署因此可能探不通，
    前端为此标注「以测试连接为准」。
    """
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def _check_comfyui(
    base_url: str,
    api_key: str,
    _t: Callable[..., str],
) -> ConnectivityCheckResponse:
    """通过 ``GET {base_url}/system_stats`` 验证 ComfyUI 可达，并回显 ``comfyui_version``。

    ``model_count`` 不填：ComfyUI 没有可枚举的模型列表，填 0 会被读成「一个模型都没有」。

    出站目的地经 ``artifact_http_client`` 校验，与该协议的提交 / 轮询 / 产物下载同一道闸：
    链路本地与云元数据地址一律拒绝，环回与私网放行（自建 ComfyUI 合法地跑在其中）。被拒按
    ``_run_connectivity_check`` 的失败出口回显，与上游不可达同一形态。
    """
    url = base_url.strip().rstrip("/") + _COMFYUI_SYSTEM_STATS_PATH
    async with artifact_http_client(timeout=_CONNECTIVITY_CHECK_TIMEOUT) as client:
        resp = await client.get(url, headers=_comfyui_probe_headers(api_key))
    raise_for_status_redacted(resp)
    payload = resp.json()
    system = payload.get("system") if isinstance(payload, dict) else None
    version = system.get("comfyui_version") if isinstance(system, dict) else None
    message = (
        _t("connectivity_check_comfyui_ok", version=str(version))
        if version
        else _t("connectivity_check_comfyui_ok_unknown_version")
    )
    return ConnectivityCheckResponse(success=True, message=message)
