"""
供应商配置管理 API。

提供供应商列表查询、单个供应商配置读写和连接测试端点。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from lib import PROJECT_ROOT
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.service import ConfigService
from lib.db import get_async_session

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
    "file_service_base_url": {"label": "文件服务 URL", "type": "url"},
    "image_rpm": {"label": "图片 RPM", "type": "number"},
    "video_rpm": {"label": "视频 RPM", "type": "number"},
    "request_gap": {"label": "请求间隔(秒)", "type": "number"},
    "image_max_workers": {"label": "图片最大并发", "type": "number"},
    "video_max_workers": {"label": "视频最大并发", "type": "number"},
}


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------


def _get_config_service(
    session: AsyncSession = Depends(get_async_session),
) -> ConfigService:
    return ConfigService(session)


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class ProviderSummary(BaseModel):
    id: str
    display_name: str
    status: str
    media_types: list[str]
    capabilities: list[str]
    configured_keys: list[str]
    missing_keys: list[str]


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
    status: str
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
    svc: Annotated[ConfigService, Depends(_get_config_service)],
) -> ProvidersListResponse:
    """返回所有供应商及其状态。"""
    statuses = await svc.get_all_providers_status()
    providers = [
        ProviderSummary(
            id=s.name,
            display_name=s.display_name,
            status=s.status,
            media_types=s.media_types,
            capabilities=s.capabilities,
            configured_keys=s.configured_keys,
            missing_keys=s.missing_keys,
        )
        for s in statuses
    ]
    return ProvidersListResponse(providers=providers)


@router.get("/{provider_id}/config", response_model=ProviderConfigResponse)
async def get_provider_config(
    provider_id: str,
    svc: Annotated[ConfigService, Depends(_get_config_service)],
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
        status=status,
        fields=fields,
    )


@router.patch("/{provider_id}/config", status_code=204)
async def patch_provider_config(
    provider_id: str,
    body: dict[str, Optional[str]],
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """更新供应商配置。值为 null 表示删除该键。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")

    svc = ConfigService(session)
    for key, value in body.items():
        if value is None:
            await svc.delete_provider_config(provider_id, key)
        else:
            await svc.set_provider_config(provider_id, key, value)

    await session.commit()
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
        pass
    os.replace(tmp_path, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass

    # Also store the path in provider_config so status becomes "ready"
    svc = ConfigService(session)
    await svc.set_provider_config("gemini-vertex", "credentials_path", str(dest))
    await session.commit()

    return {"ok": True, "filename": dest.name, "project_id": payload.get("project_id")}


@router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
async def test_provider_connection(
    provider_id: str,
    svc: Annotated[ConfigService, Depends(_get_config_service)],
) -> ConnectionTestResponse:
    """连接测试（目前为占位实现，仅校验供应商存在且必填键已配置）。"""
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

    return ConnectionTestResponse(
        success=True,
        available_models=[],
        message="连接测试暂未实现",
    )
