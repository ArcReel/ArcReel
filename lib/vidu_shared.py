"""Vidu (生数科技) 共享工具模块。

供 image_backends / video_backends / cost_calculator / providers router 复用。包含：
- VIDU_BASE_URL — Vidu 开放平台 API 基础 URL
- resolve_vidu_api_key — API Key 解析（缺失即 raise，不再走 env fallback）
- create_vidu_client — httpx.AsyncClient 工厂
- image_to_data_uri — 本地图片 → base64 data URI（API 限制 body ≤20MB）
- fetch_vidu_task / is_vidu_done / vidu_failure_reason / extract_vidu_url — 任务轮询工具
- VIDU_RETRYABLE_ERRORS — Vidu HTTPX 瞬态错误集合（仅 NetworkError / TimeoutException）
- VIDU_MAX_BODY_BYTES / assert_vidu_body_size — 20MB 请求体上限校验
- safe_body_for_log — 日志输出白名单视图（避免泄漏 base64 图片或意外字段）
- calculate_vidu_cost — 计费（fork-only，从 cost_calculator 抽出以降低上游侵入）
- test_vidu_connection — 连接测试（fork-only，从 providers router 抽出）
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx

from lib.retry import BASE_RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

VIDU_BASE_URL = "https://api.vidu.cn/ent/v2"

# 仅重试瞬态网络错误：HTTPError 涵盖 HTTPStatusError，会让 401/404 等业务错误持续
# 重试到超时浪费 API 配额；这里只取 NetworkError + TimeoutException。
VIDU_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    *BASE_RETRYABLE_ERRORS,
    httpx.NetworkError,
    httpx.TimeoutException,
)

# Vidu 文档限制单次请求体 ≤ 20MB；base64 自带 ~33% 膨胀，留 2MB 余量给非 images 字段。
VIDU_MAX_BODY_BYTES = 18 * 1024 * 1024

_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def resolve_vidu_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Vidu API Key")
    return api_key.strip()


def create_vidu_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> httpx.AsyncClient:
    """创建带 Authorization 头的 httpx.AsyncClient。"""
    token = resolve_vidu_api_key(api_key)
    return httpx.AsyncClient(
        base_url=base_url or VIDU_BASE_URL,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )


def image_to_data_uri(image_path: Path) -> str:
    """本地图片 → base64 data URI（Vidu 接受 URL 或 data URI；走 data URI 免依赖文件服务）。"""
    suffix = image_path.suffix.lower()
    mime = _IMAGE_MIME_TYPES.get(suffix, "image/png")
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# 日志输出仅允许的字段白名单（避免 CodeQL 担心 body 里其他字段含敏感数据）。
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "duration", "resolution", "aspect_ratio", "seed", "audio", "off_peak"}
)


def safe_body_for_log(body: dict) -> dict:
    """生成安全的日志视图：仅保留白名单字段 + prompt 截断 + images 仅计数。"""
    view: dict = {k: body[k] for k in _SAFE_LOG_KEYS if k in body}
    if "prompt" in body:
        prompt = body["prompt"] or ""
        view["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")
    imgs = body.get("images") or []
    if imgs:
        view["images"] = f"<{len(imgs)} data uri>"
    return view


def assert_vidu_body_size(body: dict) -> None:
    """Vidu 单次请求体硬上限 20MB；这里在序列化后估算并提前拒绝（留 2MB 余量）。"""
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(encoded) > VIDU_MAX_BODY_BYTES:
        raise ValueError(
            f"Vidu 请求体大小 {len(encoded) / 1024 / 1024:.1f}MB 超过上限 "
            f"{VIDU_MAX_BODY_BYTES / 1024 / 1024:.0f}MB（base64 编码自带 ~33% 膨胀，"
            f"请减少参考图数量或压缩图片后重试）。"
        )


# 终态状态枚举
_DONE_STATE = "success"
_FAILED_STATE = "failed"


async def fetch_vidu_task(client: httpx.AsyncClient, task_id: str) -> dict:
    """查询 Vidu 任务状态（GET /tasks/{id}/creations）。"""
    resp = await client.get(f"/tasks/{task_id}/creations")
    resp.raise_for_status()
    return resp.json()


def is_vidu_done(payload: dict) -> bool:
    return payload.get("state") == _DONE_STATE


def vidu_failure_reason(payload: dict) -> str | None:
    if payload.get("state") == _FAILED_STATE:
        err = payload.get("err_code") or "unknown"
        return f"Vidu 任务失败: err_code={err}"
    return None


def extract_vidu_url(payload: dict) -> str:
    """从 success 响应中提取生成物 URL。"""
    creations = payload.get("creations") or []
    if not creations:
        raise RuntimeError(f"Vidu 任务无 creations: task_id={payload.get('id')}")
    url = creations[0].get("url")
    if not url:
        raise RuntimeError(f"Vidu creations[0].url 为空: task_id={payload.get('id')}")
    return url


# ---------------------------------------------------------------------------
# 计费（fork-only，原住在 lib/cost_calculator.py，抽出以降低上游侵入面）
# ---------------------------------------------------------------------------
# Vidu 响应直接返回 ``credits``，按 1 积分 = 0.03125 CNY 折算
# （Vidu 标准积分包 500 元 / 16000 积分；其他套餐费率不同，用户需自行核对）
# 实际计费 99% 走"响应 credits × 费率"路径，下面的 fallback 表仅在响应未带
# credits（极少数失败/超时情况）时使用，数值为参考估算，并未逐项对账官方
# pricing 页。准确价格请以 https://platform.vidu.cn/docs/pricing 为准。
VIDU_CREDIT_TO_CNY = 0.03125
VIDU_VIDEO_CREDITS_PER_SECOND: dict[str, dict[str, int]] = {
    # q3 系列（估算）
    "viduq3-pro": {"540p": 10, "720p": 25, "1080p": 30},
    "viduq3-turbo": {"540p": 8, "720p": 12, "1080p": 14},
    "viduq3-pro-fast": {"540p": 8, "720p": 12, "1080p": 14},
    "viduq3": {"540p": 10, "720p": 25, "1080p": 30},
    "viduq3-mix": {"540p": 10, "720p": 25, "1080p": 30},
    # q2 / q1 / 2.0
    "vidu2.0": {"360p": 5, "720p": 10, "1080p": 25},
}
DEFAULT_VIDU_VIDEO_MODEL = "viduq3-turbo"
# 图片按张计费（积分/张），仅作 fallback；实际从响应 credits 取
VIDU_IMAGE_CREDITS: dict[str, dict[str, int]] = {
    "viduq2": {"1080p": 8, "2k": 12, "4k": 20},
    "viduq1": {"1080p": 20},
}
DEFAULT_VIDU_IMAGE_MODEL = "viduq2"


def calculate_vidu_cost(
    *,
    call_type: str,
    usage_tokens: int | None = None,
    model: str | None = None,
    resolution: str | None = None,
    duration_seconds: int | None = None,
) -> tuple[float, str]:
    """Vidu 统一计费：优先用响应 ``credits``（=usage_tokens），否则按表估算。"""
    # credits=0 是合法值（如 off_peak / 平台促销），不能被当成"无 credits"。
    if usage_tokens is not None and usage_tokens >= 0:
        return usage_tokens * VIDU_CREDIT_TO_CNY, "CNY"

    res_key = (resolution or "").lower()

    if call_type == "image":
        m = model or DEFAULT_VIDU_IMAGE_MODEL
        table = VIDU_IMAGE_CREDITS.get(m, VIDU_IMAGE_CREDITS[DEFAULT_VIDU_IMAGE_MODEL])
        credits = table.get(res_key or "1080p", next(iter(table.values())))
        return credits * VIDU_CREDIT_TO_CNY, "CNY"

    if call_type == "video":
        m = model or DEFAULT_VIDU_VIDEO_MODEL
        table = VIDU_VIDEO_CREDITS_PER_SECOND.get(m, VIDU_VIDEO_CREDITS_PER_SECOND[DEFAULT_VIDU_VIDEO_MODEL])
        per_sec = table.get(res_key or "720p", table.get("720p", 25))
        return (duration_seconds or 5) * per_sec * VIDU_CREDIT_TO_CNY, "CNY"

    return 0.0, "CNY"


# ---------------------------------------------------------------------------
# 连接测试（fork-only，原住在 server/routers/providers.py，抽出以降低 router 侵入）
# ---------------------------------------------------------------------------
def test_vidu_connection(config: dict[str, str]) -> None:
    """通过查询不存在的 task id 验证 Vidu API Key。

    Vidu 无 list-models 端点，借助 ``GET /tasks/<bogus>/creations`` 校验认证。
    Vidu 服务端把 task id 当作整数解析，必须传数字字符串；非数字会在 auth 检查
    之前返回 400 CODEC parse error，让连接测试始终失败。

    采用白名单：凭证有效时返回 404（task 不存在）；401/403 视为凭证无效；
    其他状态码（含 5xx）一律视为不可判定，抛错避免误判成功。
    """
    api_key = resolve_vidu_api_key(config.get("api_key"))
    base_url = (config.get("base_url") or VIDU_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Token {api_key}"}
    # 用一个语法上合法但极不可能存在的数字 id（避开整数解析失败的 400 CODEC）
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{base_url}/tasks/0/creations", headers=headers)
    if resp.status_code == 404:
        return
    if resp.status_code in (401, 403):
        raise RuntimeError(f"HTTP {resp.status_code}: 凭证无效")
    raise RuntimeError(f"HTTP {resp.status_code}: 连接测试无法判定（{resp.text[:200]}）")
