"""共享校验函数，供多个 router 复用。"""

from __future__ import annotations

from fastapi import HTTPException

from lib.config.registry import PROVIDER_REGISTRY

# 旧格式 provider 名 → 新格式 registry provider_id。
# 与 generation_worker._normalize_provider_id() 保持一致。
_LEGACY_PROVIDER_NAMES: dict[str, str] = {
    "gemini": "gemini-aistudio",
    "vertex": "gemini-vertex",
    "seedance": "ark",
}


def validate_backend_value(value: str, field_name: str) -> None:
    """校验 ``provider/model`` 格式的 backend 字段值。

    也接受旧格式的单 provider 名（如 ``"gemini"``），以兼容存量项目。

    Raises:
        HTTPException(400): 格式不合法或 provider 不在注册表中。
    """
    if "/" not in value:
        if value in _LEGACY_PROVIDER_NAMES or value in PROVIDER_REGISTRY:
            return  # 旧格式或裸 registry id，下游 _normalize_provider_id() 处理
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 格式应为 provider/model",
        )
    provider_id = value.split("/", 1)[0]
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"未知供应商: {provider_id}",
        )
