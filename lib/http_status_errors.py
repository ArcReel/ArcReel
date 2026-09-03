"""HTTP 状态错误的脱敏封装。

独立于 backend 层：``lib.vidu_shared`` 等 backend 之下的共享模块也要抛脱敏后的状态错误，
放在 ``lib.video_backends.base`` 会把它们拖成 backend 层的下游。
"""

from __future__ import annotations

import httpx


class ProviderRejectedError(httpx.HTTPStatusError):
    """上游确定性 4xx 拒绝，额外携带脱敏截断后的拒因摘要。

    仍是 ``httpx.HTTPStatusError``：重试谓词与既有 ``except httpx.HTTPStatusError`` 分支
    照常按 status_code 判定，只是多出一个 ``provider_reason`` 供落库侧单独取用——摘要是
    上游原文，与本地化文案分开存放，读侧才能把它按原文展示而不塞进译文模板。
    """

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response, provider_reason: str) -> None:
        super().__init__(message, request=request, response=response)
        self.provider_reason = provider_reason


def redacted_status_error(exc: httpx.HTTPStatusError, *, provider_reason: str | None = None) -> httpx.HTTPStatusError:
    """同一个响应，换一条不含查询串的消息。

    ``raise_for_status`` 把整条请求 URL 写进异常消息，而按 query 传凭证的通道会把 api_key
    渲染在那条 URL 里；该消息经 ``str(exc)`` 落进 ``task.error_message``、日志与 API 响应。
    类型与 ``response`` 原样保留，重试谓词照常按 status_code 判定。

    给了非空 ``provider_reason`` 就换成 :class:`ProviderRejectedError`，把上游拒因摘要挂在
    异常上一并带出去。
    """
    url = exc.request.url.copy_with(query=None)
    message = f"{exc.response.status_code} response for {url}"
    if provider_reason:
        return ProviderRejectedError(
            message, request=exc.request, response=exc.response, provider_reason=provider_reason
        )
    return httpx.HTTPStatusError(message, request=exc.request, response=exc.response)


def raise_for_status_redacted(response: httpx.Response) -> None:
    """校验响应状态；失败时抛出不含查询串的 ``HTTPStatusError``。"""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise redacted_status_error(exc) from None
