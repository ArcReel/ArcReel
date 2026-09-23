"""供应商 HTTP 失败的共享异常：脱敏后的状态错误、产物取件失败与提交歧义态。

独立于 backend 层：``lib.backends.vidu_shared`` 等 backend 之下的共享模块也要抛脱敏后的状态错误，
放在某个通道包里会把它们拖成 backend 层的下游；``lib.billing.call_failure``
（记账层的失败分类）同理——它按异常类型认出「下载失败」，而记账层在 backend 层之下。
"""

from __future__ import annotations

import httpx


class ProviderRejectedError(httpx.HTTPStatusError):
    """上游确定性 4xx 拒绝，可另带脱敏截断后的拒因摘要。

    仍是 ``httpx.HTTPStatusError``：重试谓词与既有 ``except httpx.HTTPStatusError`` 分支
    照常按 status_code 判定，只是多出一个 ``provider_reason`` 供落库侧单独取用——摘要是
    上游原文，与本地化文案分开存放，读侧才能把它按原文展示而不塞进译文模板。

    摘要可以缺席（响应体为空、认证类状态码不透传、字段全不认得），此时 ``provider_reason``
    为 ``None``：拒绝这件事本身仍要结构化落库，否则读侧拿不到状态码与「修改输入」的后续动作。
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        provider_reason: str | None = None,
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.provider_reason = provider_reason


class ArtifactDownloadError(RuntimeError):
    """供应商任务已成功、仅产物下载耗尽，可接续原任务重试取件。"""

    code = "artifact_download_failed"

    def __init__(self, *, detail: str) -> None:
        self.params = {"detail": detail}
        super().__init__(detail)


class AmbiguousSubmitError(RuntimeError):
    """create/submit（非幂等的「创建 + 计费」）阶段的歧义态失败。

    请求可能已抵达服务端并已落库 + 已计费，但响应在途丢失（ReadTimeout、写超时、
    连接中途断开、RemoteProtocolError 等）。此时自动重试会重复建任务 + 重复计费，故
    不重试、直接终态失败；error_message 带 ``[create_ambiguous]`` 前缀提示运维到供应商侧
    确认任务状态后再手动重试（与 ``[restart_lost]`` / ``[resume_unsupported]``
    「宁可手动重试、不可重复计费」先例一致，agent-facing 豁免 i18n）。
    """

    def __init__(self, *, provider: str, message: str = "") -> None:
        self.provider = provider
        super().__init__(
            message
            or f"[create_ambiguous] {provider} 创建请求可能已送达服务端但响应在途丢失"
            "（读超时/连接中途断开），为避免重复建任务与重复计费不自动重试；"
            "请到供应商侧确认任务状态后再手动重试"
        )


def redacted_status_error(exc: httpx.HTTPStatusError, *, provider_reason: str | None = None) -> httpx.HTTPStatusError:
    """同一个响应，换一条不含查询串与 userinfo 的消息。

    ``raise_for_status`` 把整条请求 URL 写进异常消息，而按 query 传凭证的通道会把 api_key
    渲染在那条 URL 里，Basic 认证式的 base_url 还会把口令留在 userinfo 段；该消息经
    ``str(exc)`` 落进 ``task.error_message``、日志与 API 响应。
    类型与 ``response`` 原样保留，重试谓词照常按 status_code 判定。

    给了非空 ``provider_reason`` 就换成 :class:`ProviderRejectedError`；摘要缺席但确实是
    一次提交被拒时，用 :func:`provider_rejected_error` 直接构造该子类。
    """
    if provider_reason:
        return provider_rejected_error(exc, provider_reason=provider_reason)
    return httpx.HTTPStatusError(_redacted_message(exc), request=exc.request, response=exc.response)


def provider_rejected_error(exc: httpx.HTTPStatusError, *, provider_reason: str | None) -> ProviderRejectedError:
    """确定性 4xx 的提交被拒：同一响应，换一条不含查询串与 userinfo 的消息，摘要可缺席。

    摘要有无都走这条：读侧靠异常类型认出「被拒」，据此渲染本地化文案并给出 FIX_INPUT；
    只按摘要有无分流会让空响应体与不透传的认证失败退回裸状态行。
    """
    return ProviderRejectedError(
        _redacted_message(exc), request=exc.request, response=exc.response, provider_reason=provider_reason
    )


def _redacted_message(exc: httpx.HTTPStatusError) -> str:
    # userinfo 与查询串一并剥掉：base_url 允许用户自填，写成 https://user:pw@host 时口令就在
    # 权限段里，httpx 原样保留它，这条消息又会落进 task.error_message 并随任务列表回传。
    return f"{exc.response.status_code} response for {exc.request.url.copy_with(query=None, userinfo=b'')}"


def raise_for_status_redacted(response: httpx.Response) -> None:
    """校验响应状态；失败时抛出不含查询串与 userinfo 的 ``HTTPStatusError``。"""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise redacted_status_error(exc) from None
