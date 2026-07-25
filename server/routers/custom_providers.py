"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连接测试端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import BadRequestError
from lib.config.repository import mask_secret
from lib.custom_provider import make_provider_id
from lib.custom_provider.capabilities import (
    CAPABILITY_OVERRIDE_FIELDS,
    capability_value_matches,
    filter_valid_overrides,
    system_video_capabilities,
)
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_spec_to_dict,
    endpoint_to_image_capabilities,
    endpoint_to_media_type,
    get_endpoint_spec,
)
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.i18n import Translator
from lib.image_backends.base import ImageCapability
from server.auth import CurrentUser


def _validate_endpoint(value: str) -> str:
    """Endpoint 校验：值必须存在于 ENDPOINT_REGISTRY，避免硬编码 Literal 漂移。"""
    if value not in ENDPOINT_REGISTRY:
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


# 写入路径上的 endpoint 字段统一走运行时校验，键集合自动跟随 ENDPOINT_REGISTRY；
# 响应路径不需校验，直接 str。
EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google"]

# 并发上限定型字段：可空正整数（≥1）；None = 未设置 → 容量装载回退全局默认。
MaxWorkers = Annotated[int | None, Field(default=None, ge=1)]

# 开放给用户覆盖的能力维度。DB 列与合成函数对 VideoCapabilities 全字段通用，写入侧在此收窄：
# 未列入的维度即便是合法字段名也拒收，扩容只需往这里加键名，无需 DB 迁移或改合成语义。
CAPABILITY_OVERRIDE_ALLOWLIST = frozenset({"last_frame"})

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTION_TEST_TIMEOUT = 15  # 秒

# 全局 DB settings 中可能引用自定义供应商的键（删除 provider / 删除 model 时清理悬空引用）
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

# project.json 中的项目级覆盖键（与全局键名不同：resolver 按媒体读 video_backend /
# audio_backend / image_provider_*，文本档位键与项目默认模型键与全局同名），清理项目悬空引用时用此集合
_PROJECT_BACKEND_KEYS = (
    "video_backend",
    "audio_backend",
    "image_provider_t2i",
    "image_provider_i2i",
    "text_backend_simple",
    "text_backend_complex",
    "default_text_backend",
)

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class ModelInput(BaseModel):
    model_id: str
    display_name: str
    endpoint: EndpointType
    is_default: bool = False
    is_enabled: bool = True
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None
    resolution: str | None = None
    # 稀疏覆盖字典，键名对齐 VideoCapabilities 字段名；None 或键缺席 = 跟随系统判定。
    # 保存模型列表是整体替换语义，本字段必须随列表回传，否则存量覆盖被清空。
    capability_overrides: dict[str, object] | None = None

    def to_db_dict(self) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。

        视频类 endpoint：supported_durations 缺省（None）或显式传 []（空列表，下游视为非法）时，
        统一归一为缺省并由 duration_presets 启发式填补。
        非视频类 endpoint 保持 None。
        """
        from lib.custom_provider.duration_presets import infer_supported_durations
        from lib.custom_provider.endpoints import endpoint_to_media_type

        d = self.model_dump()
        durations = self.supported_durations
        is_video = endpoint_to_media_type(self.endpoint) == "video"
        # video endpoint：把 [] 当作缺省（下游/前端都不接受空列表），交给 preset 兜底
        if is_video and durations is not None and len(durations) == 0:
            durations = None
        if durations is None and is_video:
            # endpoint 经 EndpointType 校验，值必在 ENDPOINT_REGISTRY 内，无需 ValueError 兜底
            durations = infer_supported_durations(self.model_id)
        d["supported_durations"] = json.dumps(durations) if durations is not None else None
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
    """PUT 全量更新：provider 元数据 + 模型列表在同一事务中。"""

    display_name: str
    base_url: str
    api_key: str | None = None  # None = 不修改
    models: list[ModelInput]
    # 并发上限随 PUT 全量提交（空输入 → None → 全局默认）；None 即清除，非"不修改"。
    image_max_workers: MaxWorkers
    video_max_workers: MaxWorkers
    audio_max_workers: MaxWorkers


class ProviderConnectionRequest(BaseModel):
    # 连接测试故意接受任意字符串，由 _run_connection_test 软失败返回 200 + success=False。
    discovery_format: str
    base_url: str
    api_key: str


class ReplaceModelsRequest(BaseModel):
    models: list[ModelInput]


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    endpoint: str
    is_default: bool
    is_enabled: bool
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None
    supported_durations: list[int] | None = None
    resolution: str | None = None
    # 系统判定值（四字段全量），video endpoint 才有；非 video 或 endpoint 声明异常时为 None。
    system_capabilities: dict[str, object] | None = None
    # 用户覆盖（稀疏字典），与 system_capabilities 平凡合并即为生效值。
    capability_overrides: dict[str, object] | None = None


class CapabilityOverridesRequest(BaseModel):
    """单模型能力覆盖写入体：携带完整覆盖字典，整体替换存量（非逐键 merge）。

    字段无默认值——必须显式传 null 或字典才表示"整体替换"；请求体缺失该键（如拼写错误、
    序列化遗漏）会被 Pydantic 拒为 422，而不是静默当作 null 把已有覆盖清空。
    """

    capability_overrides: dict[str, object] | None


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


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict]


class DiscoverAnthropicRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class CredentialsResponse(BaseModel):
    base_url: str
    api_key: str


class EndpointDescriptor(BaseModel):
    """前端从 catalog API 拿到的单条 endpoint 描述（与 lib.custom_provider.endpoints.EndpointSpec 对齐，去掉闭包）。"""

    key: str
    media_type: str
    family: str
    display_name_key: str
    request_method: str
    request_path_template: str
    image_capabilities: list[str] | None = None  # image 类填能力字符串列表，其他为 None


class EndpointCatalogResponse(BaseModel):
    endpoints: list[EndpointDescriptor]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _system_capabilities_for(endpoint: str, model_id: str) -> dict[str, object] | None:
    """读该 model 的系统判定能力（四字段全量）；非 video endpoint 返回 None。

    判定失败（endpoint 已下线、注册表声明异常）时降级为 None 而非 500：列表端点要能把
    其余模型正常呈现出来，单行判定不出来只是设置页少一段"判定值"提示。
    """
    try:
        if endpoint_to_media_type(endpoint) != "video":
            return None
        return asdict(system_video_capabilities(endpoint=endpoint, model_id=model_id))
    except ValueError:
        logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
        return None


def _effective_overrides_for_response(
    endpoint: str, model_id: str, overrides: object | None
) -> dict[str, object] | None:
    """回显前按写入侧同一判定过滤，剔除执行层不会采用的键值。

    存量行 / 非 API 写入可能留下已不兼容的覆盖（如 endpoint 不再 end_image_capable 后的
    last_frame=True）：原样回显会让界面显示"覆盖已生效"，但执行层其实静默忽略；且客户端
    普通保存时把它原样回传，会被写入侧白名单拒为 422，堵住与该覆盖无关的编辑。
    """
    filtered = filter_valid_overrides(endpoint=endpoint, model_id=model_id, overrides=overrides)
    return filtered or None


def _model_to_response(m) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
        system_capabilities=_system_capabilities_for(m.endpoint, m.model_id),
        capability_overrides=_effective_overrides_for_response(m.endpoint, m.model_id, m.capability_overrides),
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        endpoint=m.endpoint,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        price_unit=m.price_unit,
        price_input=m.price_input,
        price_output=m.price_output,
        currency=m.currency,
        supported_durations=durations,
        resolution=m.resolution,
    )


def _provider_to_response(provider, models) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        discovery_format=provider.discovery_format,
        base_url=provider.base_url,
        api_key_masked=mask_secret(provider.api_key),
        models=[_model_to_response(m) for m in models],
        created_at=dt_to_iso(provider.created_at),
        image_max_workers=provider.image_max_workers,
        video_max_workers=provider.video_max_workers,
        audio_max_workers=provider.audio_max_workers,
    )


def _cleanup_project_refs(prefix: str, setting_keys: tuple[str, ...]) -> None:
    """删除 provider 后，清理所有项目 project.json 中的悬空引用。"""
    from lib.config.resolver import get_project_manager

    pm = get_project_manager()
    for proj_name in pm.list_projects():
        try:

            def _mutate(p: dict, _prefix=prefix, _keys=setting_keys) -> None:
                for key in _keys:
                    val = p.get(key, "")
                    if isinstance(val, str) and val.startswith(_prefix):
                        p.pop(key, None)

            pm.update_project(proj_name, _mutate)
        except Exception:
            pass  # 读取失败或项目不可写，跳过（非致命）


def _check_duplicate_model_ids(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验模型列表：无重复 model_id；启用模型有合法 model_id 和 endpoint；价格组合自洽。"""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail=_t("model_id_required"))
        if m.is_enabled and not m.endpoint:
            raise HTTPException(status_code=422, detail=_t("endpoint_required"))
        if m.price_output is not None and m.price_input is None:
            raise HTTPException(status_code=422, detail=_t("price_input_required"))
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=m.model_id))
        if m.model_id:
            seen.add(m.model_id)


def _check_capability_overrides(
    overrides: dict[str, object] | None,
    endpoint: str,
    model_id: str,
    _t: Callable[..., str],
) -> None:
    """写入侧白名单校验：合成函数对脏值只降级不抛，合法性把关全在这里。

    None 与空字典都表示"全部跟随系统判定"，一律放行。非空字典要求 endpoint 为 video 类，
    且每个键都是 VideoCapabilities 字段、在开放白名单内、值类型与该字段一致。
    """
    if not overrides:
        return
    if endpoint_to_media_type(endpoint) != "video":
        raise HTTPException(
            status_code=422,
            detail=_t("capability_overrides_video_only", model_id=model_id, endpoint=endpoint),
        )
    for key, value in overrides.items():
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None:
            raise HTTPException(
                status_code=422,
                detail=_t("capability_override_unknown_key", model_id=model_id, capability=key),
            )
        if key not in CAPABILITY_OVERRIDE_ALLOWLIST:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_not_open",
                    model_id=model_id,
                    capability=key,
                    allowed=", ".join(sorted(CAPABILITY_OVERRIDE_ALLOWLIST)),
                ),
            )
        # 值类型判定复用合成层的同一函数：两边各写一份会漂移，届时写入侧放行的值被合成
        # 静默忽略，正是本能力覆盖链路要消灭的「界面允许、执行反悔」。
        if not capability_value_matches(value, expected):
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_invalid_value",
                    model_id=model_id,
                    capability=key,
                    expected=expected.__name__,
                ),
            )
        # last_frame 覆盖为 True 时，endpoint 的 delegate.generate() 必须真的会读取
        # end_image 下传尾帧约束——否则覆盖只是让合成层宣称支持，执行层仍静默生成无约束视频。
        if key == "last_frame" and value is True and not get_endpoint_spec(endpoint).end_image_capable:
            raise HTTPException(
                status_code=422,
                detail=_t(
                    "capability_override_last_frame_unsupported",
                    model_id=model_id,
                    endpoint=endpoint,
                ),
            )


def _check_model_capability_overrides(
    models: list[ModelInput],
    _t: Callable[..., str],
    *,
    stored_state: Mapping[str, tuple[str, object | None]] | None = None,
) -> None:
    """对整批模型逐个跑覆盖白名单校验（保存模型列表的写入路径）。

    ``stored_state`` 传入时按 model_id 对照当前落库的 ``(endpoint, capability_overrides)``：
    两者都未变更才跳过白名单校验——校验结果本就是 endpoint 相关的（白名单本身不区分 endpoint，
    但 last_frame=True 还要求 endpoint 的 end_image_capable；non-video endpoint 直接拒绝非空
    覆盖），只比对覆盖值而不比对 endpoint 会让「model_id 不变、覆盖字典不变、endpoint 悄悄换了」
    的整表 PUT 绕过针对新 endpoint 的校验。这条端点承载的是模型基础字段（名称/价格/是否启用等）
    的整表写入，覆盖内容的编辑走专门的 PATCH 端点（``update_model_capability_overrides``），白
    名单收紧只在那里把关。否则前端把 GET 回显（``filter_valid_overrides`` 只做结构性校验，不过
    滤白名单）原样带回本端点时，历史行 / 非 API 写入产生的、已不在开放白名单内但结构合法的覆盖
    值会让用户连改个显示名都被拒绝，且没有入口能清掉它。
    """
    stored_state = stored_state or {}
    for m in models:
        stored = stored_state.get(m.model_id)
        if stored is not None and stored == (m.endpoint, m.capability_overrides):
            continue
        _check_capability_overrides(m.capability_overrides, m.endpoint, m.model_id, _t)


def _check_unique_defaults(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验默认模型互斥。

    - 非 image endpoint（text / video / audio）：同一 media_type 至多 1 个 is_default=True。
    - image endpoint：image capability 集合两两不相交（即同一 capability 至多 1 个默认）。
    """
    text_video_defaults: dict[str, list[str]] = {}
    image_defaults: list[tuple[str, frozenset[ImageCapability]]] = []
    for m in models:
        if not m.is_default:
            continue
        try:
            mt = endpoint_to_media_type(m.endpoint)
        except ValueError:
            continue  # endpoint 已在 ModelInput validator 校验，此处跳过未知值
        if mt != "image":
            text_video_defaults.setdefault(mt, []).append(m.model_id)
            continue
        try:
            caps = endpoint_to_image_capabilities(m.endpoint)
        except ValueError:
            continue
        image_defaults.append((m.model_id, caps))

    duplicates: dict[str, list[str]] = {}
    for mt, ids in text_video_defaults.items():
        if len(ids) > 1:
            duplicates[mt] = ids

    # image：按 capability 反向索引，任一槽位有 >1 个默认即视为冲突（O(n) 替代 O(n²) 两两 caps 求交）
    cap_to_ids: dict[ImageCapability, list[str]] = {}
    for mid, caps in image_defaults:
        for c in caps:
            cap_to_ids.setdefault(c, []).append(mid)
    conflict_ids = [mid for ids in cap_to_ids.values() if len(ids) > 1 for mid in ids]
    if conflict_ids:
        duplicates["image"] = list(dict.fromkeys(conflict_ids))

    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )


async def _invalidate_caches(request: Request) -> None:
    """清空 backend 实例缓存 + 刷新 worker 限流配置。"""
    from server.services.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_providers(
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """列出所有自定义供应商（含模型列表）。"""
    repo = CustomProviderRepository(session)
    pairs = await repo.list_providers_with_models()
    return {"providers": [_provider_to_response(p, models) for p, models in pairs]}


# /endpoints 必须先于 /{provider_id} 注册，否则 FastAPI 会把字符串 "endpoints" 当作 provider_id。
@router.get("/endpoints", response_model=EndpointCatalogResponse)
async def list_endpoint_catalog(_user: CurrentUser) -> EndpointCatalogResponse:
    """暴露 ENDPOINT_REGISTRY 作为前端单一真相源：渲染下拉、显示路径与分组都派生自此返回值。"""
    return EndpointCatalogResponse(
        endpoints=[EndpointDescriptor(**endpoint_spec_to_dict(spec)) for spec in ENDPOINT_REGISTRY.values()],
    )


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """创建自定义供应商，可同时创建模型列表。"""
    if body.models:
        _check_duplicate_model_ids(body.models, _t)
        _check_unique_defaults(body.models, _t)
        _check_model_capability_overrides(body.models, _t)
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict() for m in body.models] if body.models else None
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
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider.id)
    return _provider_to_response(provider, models)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """获取单个自定义供应商详情。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.get("/{provider_id}/credentials", response_model=CredentialsResponse)
async def get_provider_credentials(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """返回明文 base_url + api_key，供智能体配置导入复用。

    仅 CurrentUser 鉴权,与现有 PATCH 接口对齐;日志不打印 body。
    多用户场景需重新评估细粒度授权。
    """
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return CredentialsResponse(
        base_url=provider.base_url or "",
        api_key=provider.api_key or "",
    )


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
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
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.put("/{provider_id}")
async def full_update_provider(
    provider_id: int,
    body: FullUpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """原子更新供应商元数据 + 模型列表（单一事务）。"""
    _check_duplicate_model_ids(body.models, _t)
    _check_unique_defaults(body.models, _t)
    repo = CustomProviderRepository(session)
    old_models = await repo.list_models(provider_id)
    stored_state = {m.model_id: (m.endpoint, m.capability_overrides) for m in old_models}
    _check_model_capability_overrides(body.models, _t, stored_state=stored_state)
    kwargs: dict = {
        "display_name": body.display_name,
        "base_url": body.base_url,
        # PUT 为并发上限的权威来源：始终写入（含 None 清除），不做"仅非空更新"
        "image_max_workers": body.image_max_workers,
        "video_max_workers": body.video_max_workers,
        "audio_max_workers": body.audio_max_workers,
    }
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    model_dicts = [m.to_db_dict() for m in body.models]
    await repo.replace_models(provider_id, model_dicts)
    await session.commit()
    await _invalidate_caches(request)
    await session.refresh(provider)
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    request: Request,
    _user: CurrentUser,
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
    # 清理引用该 provider 的全局默认 backend 配置
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    for key in _BACKEND_SETTING_KEYS:
        val = await svc.get_setting(key, "")
        if val and val.startswith(prefix):
            await svc.set_setting(key, "")
    await session.commit()
    await _invalidate_caches(request)
    # 清理引用该 provider 的项目级配置（同步文件 I/O，放到线程池避免阻塞事件循环）
    await asyncio.to_thread(_cleanup_project_refs, prefix, _PROJECT_BACKEND_KEYS)


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


@router.put("/{provider_id}/models")
async def replace_models(
    provider_id: int,
    body: ReplaceModelsRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """替换供应商的整个模型列表。"""
    _check_duplicate_model_ids(body.models, _t)
    _check_unique_defaults(body.models, _t)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    # 记录旧模型 ID，用于清理悬空引用；同时对照旧 (endpoint, 覆盖值) 判定白名单校验是否可跳过
    old_models = await repo.list_models(provider_id)
    old_model_ids = {m.model_id for m in old_models}
    stored_state = {m.model_id: (m.endpoint, m.capability_overrides) for m in old_models}
    _check_model_capability_overrides(body.models, _t, stored_state=stored_state)
    new_model_ids = {m.model_id for m in body.models}
    deleted_model_ids = old_model_ids - new_model_ids

    model_dicts = [m.to_db_dict() for m in body.models]
    new_models = await repo.replace_models(provider_id, model_dicts)

    # 清理引用已删除模型的全局配置
    if deleted_model_ids:
        from lib.config.service import ConfigService

        svc = ConfigService(session)
        prefix = f"{make_provider_id(provider_id)}/"
        for key in _BACKEND_SETTING_KEYS:
            val = await svc.get_setting(key, "")
            if val and val.startswith(prefix):
                _, model_part = val.split("/", 1)
                if model_part in deleted_model_ids:
                    await svc.set_setting(key, "")

    await session.commit()
    await _invalidate_caches(request)
    return [_model_to_response(m) for m in new_models]


@router.patch("/{provider_id}/models/{model_id:path}/capability-overrides", response_model=ModelResponse)
async def update_model_capability_overrides(
    provider_id: int,
    model_id: str,
    body: CapabilityOverridesRequest,
    request: Request,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ModelResponse:
    """整体替换单个模型的能力覆盖字典。

    请求体携带完整覆盖字典（不是逐键 patch）：省略某键即该维度回到跟随系统判定，传 None 或
    空字典即清空全部覆盖。走单模型端点而非重发整张模型列表，设置页切一个三态控件不必回传
    其余模型，也不触碰列表的整体替换语义。
    """
    repo = CustomProviderRepository(session)
    model = await repo.get_model_by_ids(provider_id, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=_t("custom_model_not_found", model_id=model_id))

    overrides = body.capability_overrides
    _check_capability_overrides(overrides, model.endpoint, model_id, _t)
    # 空字典与 None 都表示"全部跟随"，统一落 NULL，避免 DB 里出现两种等价表示。
    # 按行首查到的旧主键（model.id）更新：并发的整表 PUT（replace_models）会先删后按同一
    # model_id 重建新行，旧主键随之失效。update_model 返回 None 即命中该竞态——此时旧主键已
    # 不存在，写入根本没有落到任何行，须回 409 而非静默按更新成功处理。一并传入业务标识
    # （provider_id/model_id）：SQLite 整表删除重建会复用释放出的主键，仅按主键匹配可能命中
    # 业务上无关的新行，一并校验业务标识可挡住这类静默写错模型。再传入校验时读到的旧 endpoint
    # （model.endpoint）：业务身份不变但并发 PUT 把模型换到了另一个 endpoint 时，上面的校验是
    # 针对旧 endpoint 做的，若不核对 endpoint 是否仍一致，写入会落到一个校验结果并不适用的行。
    if (
        await repo.update_model(
            model.id,
            expect_provider_id=provider_id,
            expect_model_id=model_id,
            expect_endpoint=model.endpoint,
            capability_overrides=overrides or None,
        )
        is None
    ):
        raise HTTPException(status_code=409, detail=_t("custom_model_concurrent_update", model_id=model_id))
    await session.commit()
    await _invalidate_caches(request)

    updated = await repo.get_model_by_ids(provider_id, model_id)
    # 刚更新成功，行必然存在；理论上并发删除可致 None，此时按 404 语义回报而非 500。
    if updated is None:
        raise HTTPException(status_code=404, detail=_t("custom_model_not_found", model_id=model_id))
    return _model_to_response(updated)


# ---------------------------------------------------------------------------
# 无状态操作
# ---------------------------------------------------------------------------


@router.post("/discover")
async def discover_models_endpoint(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """模型发现：根据 discovery_format + base_url + api_key 查询可用模型。"""
    return await _run_discover(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/discover-anthropic", response_model=DiscoverResponse)
async def discover_anthropic_models_endpoint(
    body: DiscoverAnthropicRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """Anthropic 协议模型发现：智能体配置专用。

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

    base_url = body.base_url if not needs_url else (cred.base_url if cred else None)

    return await _run_discover("anthropic", base_url, api_key, _t)


@router.post("/{provider_id}/discover")
async def discover_models_by_id(
    provider_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    """使用已存储凭证发现指定供应商的可用模型。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_discover(provider.discovery_format, provider.base_url, provider.api_key, _t)


@router.post("/test")
async def test_connection(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
    _t: Translator,
):
    """连接测试：验证 discovery_format + base_url + api_key 的连通性。"""
    return await _run_connection_test(body.discovery_format, body.base_url, body.api_key, _t)


@router.post("/{provider_id}/test")
async def test_connection_by_id(
    provider_id: int, _user: CurrentUser, _t: Translator, session: AsyncSession = Depends(get_async_session)
):
    """使用已存储凭证测试指定供应商的连通性。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_t("provider_not_found"))
    return await _run_connection_test(provider.discovery_format, provider.base_url, provider.api_key, _t)


async def _run_discover(
    discovery_format: str, base_url: str | None, api_key: str, _t: Callable[..., str]
) -> DiscoverResponse:
    """共用的模型发现逻辑（明文凭证 / 已存储凭证两条入口共用）。"""
    from lib.custom_provider.discovery import UnsupportedDiscoveryFormatError, discover_models

    try:
        models = await discover_models(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        return DiscoverResponse(models=models)
    except UnsupportedDiscoveryFormatError as exc:
        raise BadRequestError("invalid_discovery_format", discovery_format=discovery_format) from exc
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg))


async def _run_connection_test(
    discovery_format: str, base_url: str, api_key: str, _t: Callable[..., str]
) -> ConnectionTestResponse:
    """共用的连接测试逻辑。"""
    try:
        if discovery_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_openai, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        elif discovery_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_google, base_url, api_key, _t),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        else:
            return ConnectionTestResponse(
                success=False,
                message=_t("unsupported_discovery_format", discovery_format=discovery_format),
            )
        return result
    except TimeoutError:
        return ConnectionTestResponse(
            success=False,
            message=_t("connection_timeout"),
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", discovery_format, err_msg)
        return ConnectionTestResponse(
            success=False,
            message=_t("connection_failed", err_msg=err_msg),
        )


def _test_openai(base_url: str, api_key: str, _t: Callable[..., str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI 兼容 API。"""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectionTestResponse(
        success=True,
        message=_t("connection_success"),
        model_count=count,
    )


def _test_google(base_url: str, api_key: str, _t: Callable[..., str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 Google genai API。"""
    from google import genai

    from lib.config.url_utils import ensure_google_base_url

    effective_url = ensure_google_base_url(base_url)
    http_options = {"base_url": effective_url} if effective_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)  # type: ignore[arg-type]
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectionTestResponse(
        success=True,
        message=_t("connection_success"),
        model_count=count,
    )
