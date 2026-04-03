"""
供应商配置管理 API。

提供供应商列表查询、单个供应商配置读写和连接测试端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from lib import PROJECT_ROOT
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import mask_secret
from lib.config.service import ConfigService
from lib.config.url_utils import normalize_base_url
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.credential_repository import CredentialRepository
from lib.gemini_shared import VERTEX_SCOPES
from server.dependencies import get_config_service

if TYPE_CHECKING:
    from lib.db.models.credential import ProviderCredential

logger = logging.getLogger(__name__)

MAX_VERTEX_CREDENTIALS_BYTES = 1024 * 1024  # 1 MiB

router = APIRouter(prefix="/providers", tags=["供应商管理"])

_CREDENTIAL_KEYS = frozenset({"api_key", "credentials_path", "base_url"})

# ---------------------------------------------------------------------------
# 字段元数据映射（key → label/type/placeholder）
# ---------------------------------------------------------------------------

_FIELD_META: dict[str, dict[str, str]] = {
    "api_key": {"label": "API Key", "type": "secret"},
    "base_url": {"label": "Base URL", "type": "url", "placeholder": "默认官方地址"},
    "credentials_path": {"label": "Vertex 凭证路径", "type": "text"},
    "gcs_bucket": {"label": "GCS Bucket", "type": "text"},
    "image_rpm": {"label": "图片 RPM", "type": "number"},
    "video_rpm": {"label": "视频 RPM", "type": "number"},
    "request_gap": {"label": "请求间隔(秒)", "type": "number"},
    "image_max_workers": {"label": "图片最大并发", "type": "number"},
    "video_max_workers": {"label": "视频最大并发", "type": "number"},
}


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class ModelInfoResponse(BaseModel):
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool


class ProviderSummary(BaseModel):
    id: str
    display_name: str
    description: str
    status: str
    media_types: list[str]
    capabilities: list[str]
    configured_keys: list[str]
    missing_keys: list[str]
    models: dict[str, ModelInfoResponse]


class ProvidersListResponse(BaseModel):
    providers: list[ProviderSummary]


class FieldInfo(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    is_set: bool
    value: str | None = None
    value_masked: str | None = None
    placeholder: str | None = None


class ProviderConfigResponse(BaseModel):
    id: str
    display_name: str
    description: str
    status: str
    media_types: list[str]
    fields: list[FieldInfo]


class ConnectionTestResponse(BaseModel):
    success: bool
    available_models: list[str]
    message: str


class CredentialResponse(BaseModel):
    id: int
    provider: str
    name: str
    api_key_masked: str | None = None
    credentials_filename: str | None = None
    base_url: str | None = None
    is_active: bool
    created_at: str


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    name: str
    api_key: str | None = None
    base_url: str | None = None


class UpdateCredentialRequest(BaseModel):
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _validate_provider(provider_id: str) -> None:
    """验证供应商 ID 是否存在，不存在则抛 404。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")


async def _get_credential_or_404(
    repo: CredentialRepository,
    provider_id: str,
    cred_id: int,
) -> ProviderCredential:
    """获取凭证并校验归属，不存在则抛 404。"""
    cred = await repo.get_by_id(cred_id)
    if not cred or cred.provider != provider_id:
        raise HTTPException(status_code=404, detail="凭证不存在")
    return cred


def _cred_to_response(cred: ProviderCredential) -> CredentialResponse:
    return CredentialResponse(
        id=cred.id,
        provider=cred.provider,
        name=cred.name,
        api_key_masked=mask_secret(cred.api_key) if cred.api_key else None,
        credentials_filename=Path(cred.credentials_path).name if cred.credentials_path else None,
        base_url=cred.base_url,
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at) or "",
    )


async def _invalidate_caches(request: Request) -> None:
    from server.services.generation_tasks import invalidate_backend_cache

    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()


def _build_field(
    key: str,
    required: bool,
    db_entry: dict[str, Any] | None,
) -> FieldInfo:
    """根据 key、是否必填和 DB 取出的条目，构建 FieldInfo。"""
    meta = _FIELD_META.get(key, {"label": key, "type": "text"})
    is_set = db_entry is not None and db_entry.get("is_set", False)

    field: dict[str, Any] = {
        "key": key,
        "label": meta["label"],
        "type": meta["type"],
        "required": required,
        "is_set": is_set,
    }

    if "placeholder" in meta:
        field["placeholder"] = meta["placeholder"]

    if is_set:
        if meta["type"] == "secret":
            field["value_masked"] = db_entry.get("masked", "••••")  # type: ignore[index]
        else:
            field["value"] = db_entry.get("value", "")  # type: ignore[index]
    else:
        if meta["type"] == "secret":
            field["value_masked"] = None
        else:
            field["value"] = ""

    return FieldInfo(**field)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("", response_model=ProvidersListResponse)
async def list_providers(
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> ProvidersListResponse:
    """返回所有供应商及其状态。"""
    statuses = await svc.get_all_providers_status()
    providers = [
        ProviderSummary(
            id=s.name,
            display_name=s.display_name,
            description=s.description,
            status=s.status,
            media_types=s.media_types,
            capabilities=s.capabilities,
            configured_keys=s.configured_keys,
            missing_keys=s.missing_keys,
            models={mid: ModelInfoResponse(**minfo) for mid, minfo in (s.models or {}).items()},
        )
        for s in statuses
    ]
    return ProvidersListResponse(providers=providers)


@router.get("/{provider_id}/config", response_model=ProviderConfigResponse)
async def get_provider_config(
    provider_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderConfigResponse:
    """返回单个供应商的配置字段（registry 元数据与 DB 值合并）。"""
    _validate_provider(provider_id)

    meta = PROVIDER_REGISTRY[provider_id]
    svc = ConfigService(session)
    db_values = await svc.get_provider_config_masked(provider_id)

    # 计算状态：基于凭证表是否有活跃凭证
    cred_repo = CredentialRepository(session)
    has_active = await cred_repo.has_active_credential(provider_id)
    status = "ready" if has_active else "unconfigured"

    # 构建字段列表：先必填，再可选，跳过凭证字段
    fields: list[FieldInfo] = []
    for key in meta.required_keys:
        if key not in _CREDENTIAL_KEYS:
            fields.append(_build_field(key, required=True, db_entry=db_values.get(key)))
    for key in meta.optional_keys:
        if key not in _CREDENTIAL_KEYS:
            fields.append(_build_field(key, required=False, db_entry=db_values.get(key)))

    return ProviderConfigResponse(
        id=provider_id,
        display_name=meta.display_name,
        description=meta.description,
        status=status,
        media_types=list(meta.media_types),
        fields=fields,
    )


@router.patch("/{provider_id}/config", status_code=204)
async def patch_provider_config(
    provider_id: str,
    body: dict[str, str | None],
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """更新供应商配置。值为 null 表示删除该键。"""
    _validate_provider(provider_id)

    svc = ConfigService(session)
    for key, value in body.items():
        if value is None:
            await svc.delete_provider_config(provider_id, key, flush=False)
        else:
            await svc.set_provider_config(provider_id, key, value, flush=False)

    await session.commit()

    # 配置变更后刷新缓存和并发池
    await _invalidate_caches(request)

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 凭证 CRUD 端点
# ---------------------------------------------------------------------------


@router.get("/{provider_id}/credentials", response_model=CredentialListResponse)
async def list_credentials(
    provider_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    _validate_provider(provider_id)
    repo = CredentialRepository(session)
    creds = await repo.list_by_provider(provider_id)
    return CredentialListResponse(credentials=[_cred_to_response(c) for c in creds])


@router.post("/{provider_id}/credentials", status_code=201, response_model=CredentialResponse)
async def create_credential(
    provider_id: str,
    body: CreateCredentialRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    _validate_provider(provider_id)
    repo = CredentialRepository(session)
    cred = await repo.create(
        provider=provider_id,
        name=body.name,
        api_key=body.api_key,
        base_url=body.base_url,
    )
    await session.commit()
    await _invalidate_caches(request)
    return _cred_to_response(cred)


@router.patch("/{provider_id}/credentials/{cred_id}", status_code=204)
async def update_credential(
    provider_id: str,
    cred_id: int,
    body: UpdateCredentialRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id)
    repo = CredentialRepository(session)
    cred = await _get_credential_or_404(repo, provider_id, cred_id)
    kwargs: dict = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    if "base_url" in body.model_fields_set:
        kwargs["base_url"] = body.base_url
    if kwargs:
        await repo.update(cred_id, **kwargs)
        await session.commit()
        if cred.is_active:
            await _invalidate_caches(request)
    return Response(status_code=204)


@router.delete("/{provider_id}/credentials/{cred_id}", status_code=204)
async def delete_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id)
    repo = CredentialRepository(session)
    cred = await _get_credential_or_404(repo, provider_id, cred_id)
    cred_path = cred.credentials_path  # 在 delete 前保存，避免 ORM 对象过期后无法访问
    await repo.delete(cred_id)
    await session.commit()
    await _invalidate_caches(request)
    # 删除关联的凭证文件（如 vertex_keys/ 下的 JSON），放在 commit 之后确保数据一致性
    if cred_path:
        cred_file = Path(cred_path)
        if cred_file.is_file():
            try:
                cred_file.unlink()
                logger.info("已删除凭证文件: %s", cred_file)
            except OSError:
                logger.warning("删除凭证文件失败: %s", cred_file, exc_info=True)
    return Response(status_code=204)


@router.post("/{provider_id}/credentials/{cred_id}/activate", status_code=204)
async def activate_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    _validate_provider(provider_id)
    repo = CredentialRepository(session)
    await _get_credential_or_404(repo, provider_id, cred_id)
    await repo.activate(cred_id, provider_id)
    await session.commit()
    await _invalidate_caches(request)
    return Response(status_code=204)


@router.post("/gemini-vertex/credentials/upload", status_code=201, response_model=CredentialResponse)
async def upload_vertex_credential(
    request: Request,
    name: str = "Vertex 凭证",
    session: AsyncSession = Depends(get_async_session),
    file: UploadFile = File(...),
) -> CredentialResponse:
    """上传 Vertex AI 服务账号 JSON 凭证文件，同时创建凭证记录。"""
    try:
        contents = await file.read(MAX_VERTEX_CREDENTIALS_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="读取上传文件失败")

    if len(contents) > MAX_VERTEX_CREDENTIALS_BYTES:
        raise HTTPException(status_code=413, detail="凭证文件过大")

    try:
        payload = json.loads(contents.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 凭证文件")

    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise HTTPException(status_code=400, detail="凭证文件缺少 project_id")

    repo = CredentialRepository(session)
    cred = await repo.create(provider="gemini-vertex", name=name)

    dest = PROJECT_ROOT / "vertex_keys" / f"vertex_cred_{cred.id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".tmp")
    tmp_path.write_bytes(contents)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        logger.warning("无法设置临时凭证文件权限: %s", tmp_path, exc_info=True)
    os.replace(tmp_path, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        logger.warning("无法设置凭证文件权限: %s", dest, exc_info=True)

    await repo.update(cred.id, credentials_path=str(dest))
    await session.commit()
    await _invalidate_caches(request)

    await session.refresh(cred)
    return _cred_to_response(cred)


# ---------------------------------------------------------------------------
# 连接测试：各供应商实现
# ---------------------------------------------------------------------------

_CONNECTION_TEST_TIMEOUT = 15  # 秒


def _test_gemini_aistudio(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 Gemini AI Studio API Key。"""
    from google import genai

    api_key = config["api_key"]
    base_url = normalize_base_url(config.get("base_url"))
    http_options = {"base_url": base_url} if base_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)

    pager = client.models.list()
    available = _extract_gemini_models(pager)
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )


def _test_gemini_vertex(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 Vertex AI 凭证验证连通性。"""
    from google import genai
    from google.oauth2 import service_account

    credentials_path = config.get("credentials_path", "")
    if not credentials_path or not Path(credentials_path).is_file():
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"凭证文件不存在: {credentials_path}",
        )

    with open(credentials_path) as f:
        creds_data = json.load(f)

    project_id = creds_data.get("project_id")
    if not project_id:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="凭证文件缺少 project_id",
        )

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=VERTEX_SCOPES,
    )
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
        credentials=credentials,
    )

    pager = client.models.list()
    available = _extract_gemini_models(pager)
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )


def _extract_gemini_models(pager) -> list[str]:
    """从 Gemini models.list() 结果中提取视频/图像相关模型，去除路径前缀。"""
    keywords = ("veo", "imagen", "image")
    models: set[str] = set()
    for m in pager:
        name = m.name or ""
        if not any(k in name.lower() for k in keywords):
            continue
        # 去掉 "models/" 或 "publishers/google/models/" 前缀
        short = name.rsplit("/", 1)[-1]
        models.add(short)
    return sorted(models)


def _test_ark(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 tasks.list 验证 Ark API Key。"""
    from lib.ark_shared import create_ark_client

    client = create_ark_client(api_key=config["api_key"])
    # 轻量级调用验证连通性，不创建任何资源
    client.content_generation.tasks.list(page_size=1)
    return ConnectionTestResponse(
        success=True,
        available_models=[],
        message="连接成功",
    )


def _test_grok(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list_language_models() 验证 xAI API Key。"""
    import xai_sdk

    client = xai_sdk.Client(api_key=config["api_key"])
    models = client.models.list_language_models()
    available = sorted(m.name for m in models if m.name)
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )


_OPENAI_MODEL_KEYWORDS = ("gpt", "sora", "dall", "o1", "o3", "o4")


def _test_openai(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 OpenAI API Key。"""
    from openai import OpenAI

    kwargs: dict = {"api_key": config["api_key"]}
    base_url = config.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    models = client.models.list()
    available = sorted(m.id for m in models.data if any(k in m.id.lower() for k in _OPENAI_MODEL_KEYWORDS))
    return ConnectionTestResponse(
        success=True,
        available_models=available,
        message="连接成功",
    )


_TEST_DISPATCH: dict[str, Callable[[dict[str, str]], ConnectionTestResponse]] = {
    "gemini-aistudio": _test_gemini_aistudio,
    "gemini-vertex": _test_gemini_vertex,
    "ark": _test_ark,
    "grok": _test_grok,
    "openai": _test_openai,
}


@router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
async def test_provider_connection(
    provider_id: str,
    credential_id: int | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResponse:
    """调用供应商 API 验证连通性。可指定 credential_id 测试特定凭证。"""
    _validate_provider(provider_id)

    repo = CredentialRepository(session)
    if credential_id is not None:
        cred = await _get_credential_or_404(repo, provider_id, credential_id)
    else:
        cred = await repo.get_active(provider_id)

    if cred is None:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="缺少凭证配置，请先添加密钥",
        )

    svc = ConfigService(session)
    config = await svc.get_provider_config(provider_id)
    cred.overlay_config(config)

    test_fn = _TEST_DISPATCH.get(provider_id)
    if test_fn is None:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"供应商 {provider_id} 暂不支持连接测试",
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(test_fn, config),
            timeout=_CONNECTION_TEST_TIMEOUT,
        )
    except TimeoutError:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="连接超时，请检查网络或 API 配置",
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", provider_id, err_msg)
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"连接失败: {err_msg}",
        )
    return result
