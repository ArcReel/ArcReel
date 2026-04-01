"""
自定义供应商管理 API。

提供自定义供应商 CRUD、模型管理、模型发现和连接测试端点。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.repository import mask_secret
from lib.custom_provider import make_provider_id
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-providers", tags=["自定义供应商"])

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
    media_type: str  # "text" | "image" | "video"
    is_default: bool = False
    is_enabled: bool = True
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def _check_price_consistency(self):
        if self.price_output is not None and self.price_input is None:
            raise ValueError("设置 price_output 时必须同时设置 price_input")
        return self


class CreateProviderRequest(BaseModel):
    display_name: str
    api_format: str  # "openai" or "google"
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
    api_format: str
    base_url: str
    api_key: str


class ReplaceModelsRequest(BaseModel):
    models: list[ModelInput]


class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
    media_type: str
    is_default: bool
    is_enabled: bool
    price_unit: str | None = None
    price_input: float | None = None
    price_output: float | None = None
    currency: str | None = None


class ProviderResponse(BaseModel):
    id: int
    display_name: str
    api_format: str
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


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _model_to_response(m) -> ModelResponse:
    return ModelResponse(
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
        media_type=m.media_type,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        price_unit=m.price_unit,
        price_input=m.price_input,
        price_output=m.price_output,
        currency=m.currency,
    )


def _provider_to_response(provider, models) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
        api_format=provider.api_format,
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


def _check_duplicate_model_ids(models: list[ModelInput]) -> None:
    """校验模型列表中无重复 model_id 且启用模型有合法 model_id。"""
    seen: set[str] = set()
    for m in models:
        if m.is_enabled and not m.model_id.strip():
            raise HTTPException(status_code=422, detail="已启用的模型必须填写 model_id")
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=f"model_id 重复: {m.model_id}")
        if m.model_id:
            seen.add(m.model_id)


def _check_unique_defaults(models: list[ModelInput]) -> None:
    """校验每个 media_type 最多只有一个 is_default=True 的模型。"""
    defaults_by_type: dict[str, list[str]] = {}
    for m in models:
        if m.is_default:
            defaults_by_type.setdefault(m.media_type, []).append(m.model_id)
    duplicates = {mt: ids for mt, ids in defaults_by_type.items() if len(ids) > 1}
    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=f"每个 media_type 最多只能有一个默认模型，冲突: {'; '.join(parts)}",
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


@router.post("", status_code=201)
async def create_provider(
    body: CreateProviderRequest,
    request: Request,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """创建自定义供应商，可同时创建模型列表。"""
    if body.models:
        _check_duplicate_model_ids(body.models)
        _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    model_dicts = [m.model_dump() for m in body.models] if body.models else None
    provider = await repo.create_provider(
        display_name=body.display_name,
        api_format=body.api_format,
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
    session: AsyncSession = Depends(get_async_session),
):
    """获取单个自定义供应商详情。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    models = await repo.list_models(provider_id)
    return _provider_to_response(provider, models)


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    request: Request,
    _user: CurrentUser,
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
        raise HTTPException(status_code=400, detail="至少需要提供一个更新字段")

    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")

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
    session: AsyncSession = Depends(get_async_session),
):
    """原子更新供应商元数据 + 模型列表（单一事务）。"""
    _check_duplicate_model_ids(body.models)
    _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    kwargs: dict = {"display_name": body.display_name, "base_url": body.base_url}
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    provider = await repo.update_provider(provider_id, **kwargs)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    model_dicts = [m.model_dump() for m in body.models]
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
    session: AsyncSession = Depends(get_async_session),
):
    """删除自定义供应商（级联删除模型，清理悬空默认配置）。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
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
    session: AsyncSession = Depends(get_async_session),
):
    """替换供应商的整个模型列表。"""
    _check_duplicate_model_ids(body.models)
    _check_unique_defaults(body.models)
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    # 记录旧模型 ID，用于清理悬空引用
    old_models = await repo.list_models(provider_id)
    old_model_ids = {m.model_id for m in old_models}
    new_model_ids = {m.model_id for m in body.models}
    deleted_model_ids = old_model_ids - new_model_ids

    model_dicts = [m.model_dump() for m in body.models]
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
):
    """模型发现：根据 api_format + base_url + api_key 查询可用模型。"""
    from lib.custom_provider.discovery import discover_models

    try:
        models = await discover_models(
            api_format=body.api_format,
            base_url=body.base_url or None,
            api_key=body.api_key,
        )
        return DiscoverResponse(models=models)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=f"模型发现失败: {err_msg}")


@router.post("/test")
async def test_connection(
    body: ProviderConnectionRequest,
    _user: CurrentUser,
):
    """连接测试：验证 api_format + base_url + api_key 的连通性。"""
    return await _run_connection_test(body.api_format, body.base_url, body.api_key)


@router.post("/{provider_id}/test")
async def test_connection_by_id(
    provider_id: int,
    _user: CurrentUser,
    session: AsyncSession = Depends(get_async_session),
):
    """使用已存储凭证测试指定供应商的连通性。"""
    repo = CustomProviderRepository(session)
    provider = await repo.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return await _run_connection_test(provider.api_format, provider.base_url, provider.api_key)


async def _run_connection_test(api_format: str, base_url: str, api_key: str) -> ConnectionTestResponse:
    """共用的连接测试逻辑。"""
    try:
        if api_format == "openai":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_openai, base_url, api_key),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        elif api_format == "google":
            result = await asyncio.wait_for(
                asyncio.to_thread(_test_google, base_url, api_key),
                timeout=_CONNECTION_TEST_TIMEOUT,
            )
        else:
            return ConnectionTestResponse(
                success=False,
                message=f"不支持的 api_format: {api_format}",
            )
        return result
    except TimeoutError:
        return ConnectionTestResponse(
            success=False,
            message="连接超时，请检查网络或 API 配置",
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", api_format, err_msg)
        return ConnectionTestResponse(
            success=False,
            message=f"连接失败: {err_msg}",
        )


def _test_openai(base_url: str, api_key: str) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI 兼容 API。"""
    from openai import OpenAI

    from lib.config.url_utils import ensure_openai_base_url

    client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
    models = client.models.list()
    count = sum(1 for _ in models)
    return ConnectionTestResponse(
        success=True,
        message="连接成功",
        model_count=count,
    )


def _test_google(base_url: str, api_key: str) -> ConnectionTestResponse:
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
        message="连接成功",
        model_count=count,
    )
