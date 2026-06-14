"""MiniMax（海螺）共享工具模块。

供 text_backends factory / config / 连接测试复用。MiniMax 本身 OpenAI 兼容，
单 `/v1` base（无 DashScope 那种文本/原生双 base 派生），故只需：
- MINIMAX_BASE_URL — 国内站默认 base（含 `/v1`）
- MINIMAX_INTL_BASE_URL — 国际站 base，供配置覆盖参考
- resolve_minimax_api_key — Bearer API Key 解析（缺失即 raise，不走 env fallback）
- minimax_text_base_url — 归一化为 {host}/v1，容忍用户填 host 或带 `/v1` 后缀
- minimax_headers — Bearer 鉴权头
"""

from __future__ import annotations

# 国内站默认 base（含 /v1）；国际站经配置覆盖 base_url 指向 MINIMAX_INTL_BASE_URL。
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_INTL_BASE_URL = "https://api.minimax.io/v1"

# 单一已知路径后缀，归一化 host 时剥除以容忍用户填入完整 base。
_V1_SUFFIX = "/v1"


def resolve_minimax_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 MiniMax API Key")
    return api_key.strip()


def _minimax_host(configured: str | None) -> str:
    """从配置的 base_url 提取 host 段（剥除 `/v1` 后缀），缺省回落国内站 host。"""
    # 先 strip 再判空：纯空白串（"   "）是真值会绕过 or，回落必须在 strip 之后，
    # 否则 base 变空串、派生出 "/v1" 这类非法相对 URL。
    base = ((configured or "").strip() or MINIMAX_BASE_URL).rstrip("/")
    if base.endswith(_V1_SUFFIX):
        return base[: -len(_V1_SUFFIX)]
    return base


def minimax_text_base_url(configured: str | None = None) -> str:
    """文本（OpenAI 兼容）base：{host}/v1。"""
    return f"{_minimax_host(configured)}{_V1_SUFFIX}"


def minimax_headers(api_key: str) -> dict[str, str]:
    """Bearer 鉴权头。"""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
