"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连接测试端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.custom_provider import make_provider_id
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, endpoint_spec_to_dict, endpoint_to_media_type


def _validate_endpoint(value: str) -> str:
    """Endpoint 校验：值必须存在于 ENDPOINT_REGISTRY，避免硬编码 Literal 漂移。"""
    if value not in ENDPOINT_REGISTRY:
        raise ValueError(f"unknown endpoint: {value!r}")
    return value


# 写入路径上的 endpoint 字段统一走运行时校验，键集合自动跟随 ENDPOINT_REGISTRY；
# 响应路径不需校验，直接 str。
EndpointType = Annotated[str, AfterValidator(_validate_endpoint)]
DiscoveryFormatLiteral = Literal["openai", "google"]
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.i18n import Translator
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["Custom Providers"])

_CONNECTION_TEST_TIMEOUT = 15  # 秒

_BACKEND_SETTING_KEYS = (
    "default_video_backend",
    "default_image_backend",
    "default_text_backend",
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
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

    def to_db_dict(self) -> dict:
        """返回适合写入数据库的字典（supported_durations 序列化为 JSON 字符串）。"""
        d = self.model_dump()
        d["supported_durations"] = (
            json.dumps(self.supported_durations) if self.supported_durations is not None else None
        )
        return d


class CreateProviderRequest(BaseModel):
    display_name: str
    discovery_format: DiscoveryFormatLiteral
    base_url: str
    api_key: str
    models: list[ModelInput] = []


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


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    discovery_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    model_count: int = 0


class DiscoverResponse(BaseModel):
    models: list[dict]


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


def _model_to_response(m) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
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


def _check_unique_defaults(models: list[ModelInput], _t: Callable[..., str]) -> None:
    """校验每个推算出的 media_type 最多只有一个 is_default=True 的模型。"""
    defaults_by_type: dict[str, list[str]] = {}
    for m in models:
        if m.is_default:
            try:
                media_type = endpoint_to_media_type(m.endpoint)
            except ValueError:
                continue  # endpoint 已在 ModelInput validator 校验，此处跳过未知值
            defaults_by_type.setdefault(media_type, []).append(m.model_id)
    duplicates = {mt: ids for mt, ids in defaults_by_type.items() if len(ids) > 1}
    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )


async def _invalidate_caches(request: Request) -> None:
    """清空 backend 实例缓存 + 刷新 worker 限流配置。"""
    from server.services.generation_tasks import invalidate_backend_cache

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
    repo = CustomProviderRepository(session)
    model_dicts = [m.to_db_dict() for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        discovery_format=body.discovery_format,
        base_url=body.base_url,
        api_key=body.api_key,
        models=model_dicts,
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
    kwargs: dict = {"display_name": body.display_name, "base_url": body.base_url}
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
    await asyncio.to_thread(_cleanup_project_refs, prefix, _BACKEND_SETTING_KEYS)


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
    # 记录旧模型 ID，用于清理悬空引用
    old_models = await repo.list_models(provider_id)
    old_model_ids = {m.model_id for m in old_models}
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
    from lib.custom_provider.discovery import discover_models

    try:
        models = await discover_models(
            discovery_format=discovery_format,
            base_url=base_url or None,
            api_key=api_key,
        )
        return DiscoverResponse(models=models)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
    client = genai.Client(api_key=api_key, http_options=http_options)
    pager = client.models.list()
    count = sum(1 for _ in pager)
    return ConnectionTestResponse(
        success=True,
        message=_t("connection_success"),
        model_count=count,
    )
