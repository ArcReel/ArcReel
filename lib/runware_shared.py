"""Runware 共享工具模块。

供 image_backends / config 复用。Runware 聚合推理平台用单一 `/v1` base + Bearer 单 key 鉴权：
- RUNWARE_API_BASE — 默认 base（含 `/v1`）
- resolve_runware_api_key — Bearer API Key 解析（缺失即 raise，不走 env fallback）
- runware_base_url — 归一化为 {host}/v1，容忍用户填 host 或带 `/v1` 后缀
- runware_headers — Bearer 鉴权头
"""

from __future__ import annotations

# 默认 base（含 /v1）；用户可经配置覆盖 base_url 指向自建中转。
RUNWARE_API_BASE = "https://api.runware.ai/v1"

# 单一已知路径后缀，归一化 base 时剥除以容忍用户填入完整 base。
_V1_SUFFIX = "/v1"


def resolve_runware_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Runware API Key")
    return api_key.strip()


def runware_base_url(configured: str | None = None) -> str:
    """归一化 base：{host}/v1，容忍用户填 host 或带 /v1 后缀。"""
    base = ((configured or "").strip() or RUNWARE_API_BASE).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base
    return f"{base}{_V1_SUFFIX}"


def runware_headers(api_key: str) -> dict[str, str]:
    """Bearer 鉴权头。复用 resolve_runware_api_key 校验空 key。"""
    api_key = resolve_runware_api_key(api_key)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
