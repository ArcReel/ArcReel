"""阿里百炼 DashScope (Model Studio) 共享工具模块。

供 image_backends / video_backends / text_backends factory / custom_provider / config
复用。包含：
- DASHSCOPE_BASE_URL — 百炼 host 段（不含路径后缀），北京地域起点
- DASHSCOPE_RETRYABLE_ERRORS — 瞬态错误集合（网络层 + 字符串兜底覆盖 5xx/429）
- resolve_dashscope_api_key — API Key 解析（缺失即 raise，不走 env fallback）
- dashscope_text_base_url / dashscope_native_base_url — 由 host 派生双 base
  （文本走 /compatible-mode/v1，原生图像/视频走 /api/v1），容忍带/不带后缀
- dashscope_headers — Bearer 鉴权头，视频异步额外带 X-DashScope-Async: enable
- 视频异步任务工具 — 状态判定 / 失败原因 / task_id / video_url / 计费时长提取
- extract_image_url — 同步图像响应（multimodal choices）URL 提取
- safe_body_for_log — 日志白名单视图（避免 base64 图片或长 prompt 进日志）
"""

from __future__ import annotations

import base64
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import httpx

from lib.retry import BASE_RETRYABLE_ERRORS

logger = logging.getLogger(__name__)

# host 段（scheme://host），不含 /api/v1 或 /compatible-mode/v1 后缀；两 base 由此派生。
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"

# 故意不含 httpx.HTTPStatusError —— 这是经核实的设计决策，勿据"它不是 RequestError 子类"加入。
#
# HTTPStatusError 与 RequestError 同为 HTTPXError 的兄弟类（互不继承），但重试无需把它列进
# 类型元组：with_retry_async 与 poll_with_retry 都经 lib.retry._should_retry，该函数在类型
# 不匹配时回退到字符串匹配——HTTPStatusError 的 str() 含状态码短语（如 "Server error '503 …'"），
# 故 429/500/502/503/504 仍被重试；而 400/401/403/404 的 str 不命中 RETRYABLE_STATUS_PATTERNS，
# 保持快速失败。若把 HTTPStatusError 直接加进本元组，isinstance 会对全部 4xx 也判 True →
# 鉴权/参数等业务错误被重试到超时，反而破坏 fail-fast。此为全仓后端一致约定。
DASHSCOPE_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    *BASE_RETRYABLE_ERRORS,
    httpx.RequestError,
)

# 任务状态机（图像/视频异步两步式）
DASHSCOPE_STATUS_PENDING = "PENDING"
DASHSCOPE_STATUS_RUNNING = "RUNNING"
DASHSCOPE_STATUS_SUCCEEDED = "SUCCEEDED"
DASHSCOPE_STATUS_FAILED = "FAILED"
DASHSCOPE_STATUS_CANCELED = "CANCELED"
# task_id 超过 24h 有效期后查询返回 UNKNOWN（resume 路径据此判过期）
DASHSCOPE_STATUS_UNKNOWN = "UNKNOWN"

_TERMINAL_STATES = frozenset(
    {
        DASHSCOPE_STATUS_SUCCEEDED,
        DASHSCOPE_STATUS_FAILED,
        DASHSCOPE_STATUS_CANCELED,
        DASHSCOPE_STATUS_UNKNOWN,
    }
)
_FAILURE_STATES = frozenset({DASHSCOPE_STATUS_FAILED, DASHSCOPE_STATUS_CANCELED})

_IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# 百炼建议轮询间隔 15 秒；task_id / video_url 均 24h 过期。
DASHSCOPE_POLL_INTERVAL_SECONDS = 15.0

# 已知路径后缀，派生 host 时剥除以容忍用户填入完整 base（含地域切换）。
_KNOWN_SUFFIXES = ("/compatible-mode/v1", "/api/v1")


def resolve_dashscope_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 DashScope API Key")
    return api_key.strip()


def _dashscope_host(configured: str | None) -> str:
    """从配置的 base_url 提取 host 段（剥除已知路径后缀），缺省回落北京 host。"""
    # 先 strip 再判空：纯空白串（"   "）是真值会绕过 or，回落必须在 strip 之后，
    # 否则 base 变空串、派生出 "/api/v1" 这类非法相对 URL。
    base = ((configured or "").strip() or DASHSCOPE_BASE_URL).rstrip("/")
    for suffix in _KNOWN_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def dashscope_text_base_url(configured: str | None = None) -> str:
    """文本（OpenAI 兼容模式）base：{host}/compatible-mode/v1。"""
    return f"{_dashscope_host(configured)}/compatible-mode/v1"


def dashscope_native_base_url(configured: str | None = None) -> str:
    """原生（图像/视频）base：{host}/api/v1。"""
    return f"{_dashscope_host(configured)}/api/v1"


def dashscope_headers(api_key: str, *, async_mode: bool = False) -> dict[str, str]:
    """Bearer 鉴权头；视频/异步图像须带 X-DashScope-Async: enable，同步图像不带。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    return headers


def image_to_data_uri(image_path: Path) -> str:
    """本地图片 → base64 data URI（百炼 media/image 接受 URL 或 data URI）。"""
    mime = _IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/png")
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── 视频异步任务状态工具 ──────────────────────────────────────────────────────


def _as_dict(value: object) -> dict:
    """把任意值归一化为 dict：非 dict（含 None / list / str 等异常上游结构）一律回空 dict。

    DashScope 文档保证 output/usage 为对象，但代理中转或错误响应可能给出非 dict 真值，
    用此 helper 统一兜底，避免对其调用 .get 抛 AttributeError。
    """
    return value if isinstance(value, dict) else {}


def _task_status(payload: dict) -> str | None:
    return _as_dict(payload.get("output")).get("task_status")


def is_dashscope_succeeded(payload: dict) -> bool:
    return _task_status(payload) == DASHSCOPE_STATUS_SUCCEEDED


def is_dashscope_terminal(payload: dict) -> bool:
    """SUCCEEDED / FAILED / CANCELED / UNKNOWN 均视为终态，停止轮询。"""
    return _task_status(payload) in _TERMINAL_STATES


def is_dashscope_expired(payload: dict) -> bool:
    """task_id 过期（24h）查询返回 UNKNOWN；resume 路径据此抛 ResumeExpiredError。"""
    return _task_status(payload) == DASHSCOPE_STATUS_UNKNOWN


def dashscope_failure_reason(payload: dict) -> str | None:
    """FAILED/CANCELED 返回错误描述；UNKNOWN 不算失败（由 expired 单独处理）。

    同时兜底提交阶段的顶层错误响应（``{code, message, request_id}`` 无 output）。
    """
    output = _as_dict(payload.get("output"))
    status = output.get("task_status")
    if status in _FAILURE_STATES:
        code = output.get("code") or "unknown"
        message = output.get("message") or ""
        return f"DashScope 任务失败 status={status} code={code}: {message}".strip()
    # 提交阶段顶层错误（如 InvalidApiKey），无 output.task_status
    if status is None and payload.get("code"):
        return f"DashScope 提交失败 code={payload.get('code')}: {payload.get('message', '')}".strip()
    return None


def extract_task_id(submit_payload: dict) -> str:
    """从提交响应提取 output.task_id。"""
    task_id = _as_dict(submit_payload.get("output")).get("task_id")
    if not task_id:
        reason = dashscope_failure_reason(submit_payload)
        raise RuntimeError(reason or f"DashScope 提交响应缺少 task_id: {submit_payload}")
    return task_id


def extract_video_url(payload: dict) -> str:
    """从 SUCCEEDED 轮询响应提取 output.video_url。"""
    url = _as_dict(payload.get("output")).get("video_url")
    if not url:
        raise RuntimeError(f"DashScope 任务完成但缺少 video_url: {payload}")
    return url


def extract_billing_duration(payload: dict) -> int | None:
    """从 usage.duration 取真实计费时长（wan2.7-r2v 含输入视频时长）。

    容忍 int / float / 数字字符串；按 half-up 取整（4.5→5）而非截断或银行家舍入，避免少计费秒数。
    非正值（0 / 负 / 无法解析）一律回 None，由 caller 回落请求时长，不记 0 秒账。
    """
    raw = _as_dict(payload.get("usage")).get("duration")
    if raw is None:
        return None
    try:
        value = int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value > 0 else None


# ── 同步图像响应工具 ──────────────────────────────────────────────────────────


def extract_image_url(payload: dict) -> str:
    """从同步图像响应 output.choices[0].message.content[*].image 提取首个 URL。"""
    choices = _as_dict(payload.get("output")).get("choices")
    if not isinstance(choices, list) or not choices:
        reason = dashscope_failure_reason(payload)
        raise RuntimeError(reason or f"DashScope 图像响应缺少 choices: {payload}")
    # 上游异常结构（choices[0]/message 非 dict）归一化为空 dict，避免 .get 抛 AttributeError
    content = _as_dict(_as_dict(choices[0]).get("message")).get("content")
    # 显式校验 list：content 为 truthy 非 list（如 int / bool / dict）时 `or []` 兜不住，
    # 直接 for 会抛 TypeError；isinstance 守卫统一落到下方 RuntimeError。
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and (url := item.get("image")):
                return url
    raise RuntimeError(f"DashScope 图像响应 content 无 image 字段: {payload}")


# ── 同步音频响应工具 ──────────────────────────────────────────────────────────


def extract_audio_url(payload: dict) -> str:
    """从同步语音合成响应 output.audio.url 提取音频文件 URL（wav，24h 有效）。"""
    url = _as_dict(_as_dict(payload.get("output")).get("audio")).get("url")
    if not url:
        reason = dashscope_failure_reason(payload)
        raise RuntimeError(reason or f"DashScope 语音合成响应缺少 audio.url: {payload}")
    return url


# ── 日志脱敏 ──────────────────────────────────────────────────────────────────

# 仅允许进日志的标量字段白名单；其余（含 base64 image / media url）一律不入日志。
_SAFE_LOG_KEYS: frozenset[str] = frozenset(
    {"model", "size", "resolution", "ratio", "duration", "n", "watermark", "prompt_extend", "seed"}
)


def safe_body_for_log(body: dict) -> dict:
    """生成安全日志视图：白名单标量 + prompt 截断 + media/messages 仅计数。

    body 含 input.messages（图像）或 input.media（视频），内部嵌 base64/URL；
    一律不展开，避免敏感数据进日志（对齐 CodeQL clear-text-logging 约束）。
    """
    # _as_dict 而非 `or {}`：truthy 非 dict（如 parameters/input 为 list/str）下 `or {}` 兜不住，
    # 后续 .get / in 会抛 AttributeError/TypeError，反而让 fail-safe 日志辅助遮蔽原始异常。
    params = _as_dict(body.get("parameters"))
    view: dict = {"model": body.get("model")}
    for key in _SAFE_LOG_KEYS:
        if key in params:
            view[key] = params[key]

    inp = _as_dict(body.get("input"))
    prompt = inp.get("prompt")
    if isinstance(prompt, str):
        view["prompt"] = prompt[:120] + ("…" if len(prompt) > 120 else "")

    media = inp.get("media")
    if isinstance(media, list) and media:
        view["media"] = f"<{len(media)} item>"

    messages = inp.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        content = messages[0].get("content")
        if isinstance(content, list):
            images = sum(1 for c in content if isinstance(c, dict) and "image" in c)
            texts = sum(1 for c in content if isinstance(c, dict) and "text" in c)
            view["content"] = f"<{images} image, {texts} text>"
    return view
