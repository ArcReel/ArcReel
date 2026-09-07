"""供应商调用失败的机器稳定编码（记账层）。

失败的 ``api_calls`` 行除了原文（``error_message``）还落一个机器码与一组参数：读侧据此按当前
语言渲染失败短语，切换语言后历史失败原因跟着翻译。认不出的异常只留原文，``error_code`` 与
``error_params`` 留空——读侧原样展示。

与 :mod:`lib.task_failure` 的任务失败码**不共用枚举**：那一套的码要在 ``lib/i18n`` 的 errors
表里各有一条服务端译文（``FAILURE_CODE_KEYS`` 强制登记），渲染发生在任务读接口内；这里的四个码
是供应商调用的失败分类，渲染在前端。混进同一枚举会让两侧互相要求对方登记自己的码。

序列化形式沿用那边的 ``[code] {json}``——机器码加一个 JSON 参数字典，参数值须是 JSON 原生值。
只是 ``api_calls`` 已有 ``error_code`` / ``error_params`` 两列，两半分列存放，不再拼成一个字符串
让读侧再解析一次。

分类依据取异常类型与其结构化属性（HTTP 状态、上游错误体的 ``code``），不做消息文本猜测。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from openai import APITimeoutError

from lib.http_status_errors import ArtifactDownloadError


class CallErrorCode(StrEnum):
    """失败调用的机器码取值集合（读侧按码渲染短语）。"""

    RATE_LIMITED = "rate_limited"
    CONTENT_POLICY = "content_policy"
    TIMEOUT = "timeout"
    DOWNLOAD_FAILED = "download_failed"


# 上游错误体里表示「内容被策略拦下」的 ``code``。OpenAI 兼容协议（官方、Azure、各中转）把它
# 放在错误体的 code 字段并由 SDK 挂到异常上，是机器字段而非消息文本。
CONTENT_POLICY_PROVIDER_CODES: frozenset[str] = frozenset(
    {
        "content_filter",
        "content_policy_violation",
        "moderation_blocked",
    }
)

# 状态码本身就在说「等太久了」：408 请求超时、504 网关超时。
_TIMEOUT_STATUS_CODES: frozenset[int] = frozenset({408, 504})

_RATE_LIMIT_STATUS_CODE = 429


@dataclass(frozen=True, slots=True)
class CallFailure:
    """失败调用落库的三元组：原文 + 机器码 + 参数。认不出的异常只有原文。"""

    error_message: str
    error_code: str | None = None
    error_params: dict[str, Any] | None = None


def classify_call_failure(exc: BaseException) -> CallFailure:
    """把记账括号捕获的异常编码为落库三元组。

    顺序即优先级：产物下载失败先判——下载耗尽的根因常是一次超时或一个 HTTP 状态，但对用户而言
    它是「下载失败」（可重试取件），不是一次超时的生成。其余三类互不重叠。
    """
    message = str(exc)
    if isinstance(exc, ArtifactDownloadError):
        return CallFailure(message, CallErrorCode.DOWNLOAD_FAILED, _download_params(exc))
    status = _http_status(exc)
    if status == _RATE_LIMIT_STATUS_CODE:
        # 429 覆盖 openai.RateLimitError（响应挂在异常上）与 google 的 ResourceExhausted
        # （状态码挂在异常自身的 code 上），无需按各家 SDK 的类型逐一枚举。
        return CallFailure(message, CallErrorCode.RATE_LIMITED, _retry_after_params(exc))
    if _provider_error_code(exc) in CONTENT_POLICY_PROVIDER_CODES:
        return CallFailure(message, CallErrorCode.CONTENT_POLICY, {})
    if isinstance(exc, TimeoutError | httpx.TimeoutException | APITimeoutError) or status in _TIMEOUT_STATUS_CODES:
        # TimeoutError 覆盖内置与 asyncio 别名（视频轮询超时抛的就是它）；httpx 与 openai 的
        # 超时异常各有自己的基类，不继承 TimeoutError，须显式列出。
        return CallFailure(message, CallErrorCode.TIMEOUT, {})
    return CallFailure(message)


def _download_params(exc: ArtifactDownloadError) -> dict[str, Any]:
    """产物下载失败的参数：能问出 HTTP 状态就带上。

    ``ArtifactDownloadError`` 自身只有 detail 文本，状态码在它包裹的下载异常上
    （``raise ... from exc``），沿 ``__cause__`` 链取。
    """
    status = _http_status_in_chain(exc)
    return {} if status is None else {"status": status}


def _retry_after_params(exc: BaseException) -> dict[str, Any]:
    """限流的参数：响应带整数秒 ``Retry-After`` 时带上重试等待秒数。"""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return {}
    raw = headers.get("Retry-After")
    # HTTP-date 形式的 Retry-After 不解析：读侧要的是「等多少秒」，把一个绝对时刻算成秒数需要
    # 知道响应时刻，那不是这里能可靠拿到的。
    if not isinstance(raw, str) or not raw.strip().isdigit():
        return {}
    return {"retry_after_seconds": int(raw.strip())}


def _http_status(exc: BaseException) -> int | None:
    """异常携带的 HTTP 状态码：httpx 系挂在 ``response`` 上，google 系挂在异常自身。"""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        # bool 是 int 子类，排掉；``code`` 在本仓库的能力异常上是字符串，isinstance 一并挡住。
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _http_status_in_chain(exc: BaseException) -> int | None:
    """沿 ``__cause__`` 链找出第一个带 HTTP 状态的异常。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        status = _http_status(current)
        if status is not None:
            return status
        seen.add(id(current))
        current = current.__cause__
    return None


def _provider_error_code(exc: BaseException) -> str | None:
    """上游错误体的 ``code``（字符串形态）；没有或不是字符串返回 ``None``。"""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) else None
