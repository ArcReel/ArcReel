"""
供应商配置管理 API。

提供供应商列表查询、单个供应商配置读写和连接测试端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from lib import PROJECT_ROOT
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.gemini_shared import VERTEX_SCOPES
from server.dependencies import get_config_service

logger = logging.getLogger(__name__)

MAX_VERTEX_CREDENTIALS_BYTES = 1024 * 1024  # 1 MiB

router = APIRouter(prefix="/providers", tags=["供应商管理"])

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
    value: Optional[str] = None
    value_masked: Optional[str] = None
    placeholder: Optional[str] = None


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


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_field(
    key: str,
    required: bool,
    db_entry: Optional[dict[str, Any]],
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
            models={
                mid: ModelInfoResponse(**minfo)
                for mid, minfo in (s.models or {}).items()
            },
        )
        for s in statuses
    ]
    return ProvidersListResponse(providers=providers)


@router.get("/{provider_id}/config", response_model=ProviderConfigResponse)
async def get_provider_config(
    provider_id: str,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> ProviderConfigResponse:
    """返回单个供应商的配置字段（registry 元数据与 DB 值合并）。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")

    meta = PROVIDER_REGISTRY[provider_id]
    db_values = await svc.get_provider_config_masked(provider_id)

    # 计算状态
    configured_keys = list(db_values.keys())
    missing = [k for k in meta.required_keys if k not in configured_keys]
    status = "ready" if not missing else "unconfigured"

    # 构建字段列表：先必填，再可选
    fields: list[FieldInfo] = []
    for key in meta.required_keys:
        fields.append(_build_field(key, required=True, db_entry=db_values.get(key)))
    for key in meta.optional_keys:
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
    body: dict[str, Optional[str]],
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """更新供应商配置。值为 null 表示删除该键。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")

    svc = ConfigService(session)
    for key, value in body.items():
        if value is None:
            await svc.delete_provider_config(provider_id, key, flush=False)
        else:
            await svc.set_provider_config(provider_id, key, value, flush=False)

    await session.commit()

    # 配置变更后刷新缓存和并发池
    from server.services.generation_tasks import invalidate_backend_cache
    invalidate_backend_cache()

    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        await worker.reload_limits()

    return Response(status_code=204)


@router.post("/gemini-vertex/credentials")
async def upload_vertex_credentials(
    session: AsyncSession = Depends(get_async_session),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """上传 Vertex AI 服务账号 JSON 凭证文件。"""
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

    # Save credentials file
    dest = PROJECT_ROOT / "vertex_keys" / "vertex_credentials.json"
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

    # Also store the path in provider_config so status becomes "ready"
    svc = ConfigService(session)
    await svc.set_provider_config("gemini-vertex", "credentials_path", str(dest))
    await session.commit()

    return {"ok": True, "filename": dest.name, "project_id": payload.get("project_id")}


# ---------------------------------------------------------------------------
# 连接测试：各供应商实现
# ---------------------------------------------------------------------------

_CONNECTION_TEST_TIMEOUT = 15  # 秒


def _test_gemini_aistudio(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 models.list() 验证 Gemini AI Studio API Key。"""
    from google import genai

    api_key = config["api_key"]
    base_url = config.get("base_url", "").strip() or None
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
        credentials_path, scopes=VERTEX_SCOPES,
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
    from volcenginesdkarkruntime import Ark

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=config["api_key"],
    )
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


_TEST_DISPATCH: dict[str, Callable[[dict[str, str]], ConnectionTestResponse]] = {
    "gemini-aistudio": _test_gemini_aistudio,
    "gemini-vertex": _test_gemini_vertex,
    "ark": _test_ark,
    "grok": _test_grok,
}


@router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
async def test_provider_connection(
    provider_id: str,
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> ConnectionTestResponse:
    """调用供应商 API 验证连通性，返回可用模型列表。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")

    meta = PROVIDER_REGISTRY[provider_id]
    configured_keys = await svc.get_provider_config_masked(provider_id)
    missing = [k for k in meta.required_keys if k not in configured_keys]

    if missing:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"缺少必填配置项：{', '.join(missing)}",
        )

    test_fn = _TEST_DISPATCH.get(provider_id)
    if test_fn is None:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"供应商 {provider_id} 暂不支持连接测试",
        )

    config = await svc.get_provider_config(provider_id)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(test_fn, config),
            timeout=_CONNECTION_TEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="连接超时，请检查网络或 API 配置",
        )
    except Exception as exc:
        err_msg = str(exc)
        # 截断过长的错误信息
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", provider_id, err_msg)
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"连接失败: {err_msg}",
        )

    return result
