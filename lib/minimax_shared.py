"""MiniMax（海螺）共享工具模块。

供 text_backends / image_backends / config / 连接测试复用。MiniMax 本身 OpenAI 兼容，
单 `/v1` base（无 DashScope 那种文本/原生双 base 派生），故只需：
- MINIMAX_BASE_URL — 国内站默认 base（含 `/v1`）
- MINIMAX_INTL_BASE_URL — 国际站 base，供配置覆盖参考
- resolve_minimax_api_key — Bearer API Key 解析（缺失即 raise，不走 env fallback）
- minimax_text_base_url — 归一化为 {host}/v1，容忍用户填 host 或带 `/v1` 后缀
- minimax_headers — Bearer 鉴权头
- extract_image_url / extract_image_base64 — 单步 image_generation 响应取首图
- minimax_failure_reason — base_resp.status_code 非零时的错误描述
- safe_body_for_log — 日志白名单视图（剥 base64/URL/prompt）
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

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


def _as_dict(value: object) -> dict:
    """把任意值归一化为 dict：非 dict（None / list / str 等异常上游结构）一律回空 dict。

    避免对中转代理 / 错误响应给出的非 dict 真值调用 .get 抛 AttributeError。
    """
    return value if isinstance(value, dict) else {}


# ── 单步 image_generation 响应工具 ────────────────────────────────────────────


def extract_image_url(payload: dict) -> str | None:
    """从 image_generation 响应 data.image_urls 取首个 URL（response_format=url，24h 有效）。

    无可用 URL（字段缺失 / 非 list / 全为空）返回 None，由 caller 回落 base64 或报错。
    """
    urls = _as_dict(payload.get("data")).get("image_urls")
    if isinstance(urls, list):
        for url in urls:
            if isinstance(url, str) and url:
                return url
    return None


def extract_image_base64(payload: dict) -> str | None:
    """从 image_generation 响应 data.image_base64 取首个 base64（response_format=base64）。

    无可用 base64 返回 None，由 caller 报错。
    """
    items = _as_dict(payload.get("data")).get("image_base64")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str) and item:
                return item
    return None


def minimax_failure_reason(payload: dict) -> str | None:
    """base_resp.status_code 非零时返回错误描述；成功（0）或缺失 base_resp 返回 None。

    MiniMax 业务错误以 HTTP 200 + base_resp.status_code 非零承载（鉴权失败等可能另走 4xx，
    由 submit_post/raise_for_status 兜住），故同步图像响应须先查 base_resp 再取图。
    """
    base = _as_dict(payload.get("base_resp"))
    status = base.get("status_code")
    if status is not None and status != 0:
        msg = base.get("status_msg") or ""
        return f"MiniMax 图像生成失败 status_code={status}: {msg}".strip()
    return None


# ── 日志脱敏 ──────────────────────────────────────────────────────────────────

# 仅允许进日志的标量字段白名单；prompt 仅记长度、subject_reference 仅计数，
# base64/URL 一律不入日志（对齐 CodeQL clear-text-logging 约束）。
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "aspect_ratio", "width", "height", "response_format", "n", "prompt_optimizer", "seed"}
)


def safe_body_for_log(body: dict) -> dict:
    """生成安全日志视图：白名单标量 + prompt 仅长度 + subject_reference 仅计数。

    subject_reference 内嵌参考图 base64/URL，prompt 为长文本，一律不展开。
    """
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    refs = body.get("subject_reference")
    if isinstance(refs, list) and refs:
        view["subject_reference"] = f"<{len(refs)} ref>"
    return view
