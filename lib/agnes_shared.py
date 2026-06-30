"""Agnes 共享工具模块。

供 image_backends / video_backends / text_backends / config / 连接测试复用。Agnes 经
apihub 网关提供 OpenAI 风格端点，单 `/v1` base，Bearer 单 key 鉴权：
- AGNES_BASE_URL — 默认 base（含 `/v1`）
- resolve_agnes_api_key — Bearer API Key 解析（缺失即 raise，不走 env fallback）
- agnes_base_url — 归一化为 {host}/v1，容忍用户填 host 或带 `/v1` 后缀
- agnes_headers — Bearer 鉴权头
"""

from __future__ import annotations

# 默认 base（含 /v1）；用户可经配置覆盖 base_url 指向自建中转。
AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"

# 单一已知路径后缀，归一化 host 时剥除以容忍用户填入完整 base。
_V1_SUFFIX = "/v1"


def resolve_agnes_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Agnes API Key")
    return api_key.strip()


def _agnes_host(configured: str | None) -> str:
    """从配置的 base_url 提取 host 段（剥除 `/v1` 后缀），缺省回落默认 host。"""
    # 先 strip 再判空：纯空白串（"   "）是真值会绕过 or，回落必须在 strip 之后，
    # 否则 base 变空串、派生出 "/v1" 这类非法相对 URL。
    base = ((configured or "").strip() or AGNES_BASE_URL).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base[: -len(_V1_SUFFIX)]
    return base


def agnes_base_url(configured: str | None = None) -> str:
    """OpenAI 兼容 base：{host}/v1。"""
    return f"{_agnes_host(configured)}{_V1_SUFFIX}"


def agnes_headers(api_key: str) -> dict[str, str]:
    """Bearer 鉴权头。

    复用 resolve_agnes_api_key 校验：空串 / 纯空白即本地 raise，避免拼出
    ``Authorization: Bearer `` 把缺失 key 拖到请求期才收上游 401。
    """
    api_key = resolve_agnes_api_key(api_key)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
