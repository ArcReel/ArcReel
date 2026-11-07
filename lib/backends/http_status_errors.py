"""Shared provider HTTP failures: redacted statuses, artifact retrieval failures, and ambiguous submissions.

Independent of backend layers: lower shared modules such as ``lib.backends.vidu_shared`` also raise
redacted status errors. Putting them in channel packages would make lower modules depend on backend
layers. Likewise ``lib.billing.call_failure`` identifies download failures by exception type while
accounting sits below backends.
"""

from __future__ import annotations

import httpx


class ProviderRejectedError(httpx.HTTPStatusError):
    """Deterministic upstream 4xx rejection, optionally carrying a redacted/truncated reason summary.

    Still ``httpx.HTTPStatusError``: retry predicates and existing ``except httpx.HTTPStatusError`` branches
    inspect status_code normally. Extra ``provider_reason`` supports separate persistence of upstream
    original text from localized copy, allowing original-text display without translation-template insertion.

    Summaries may be absent for empty bodies, hidden authentication statuses, or unrecognized fields;
    then ``provider_reason`` is ``None``. Still persist rejection structurally, or readers lose status
    codes and input-repair next actions.
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
    """Provider task succeeded but artifact retrieval exhausted retries; resume the same task to retry downloading."""

    code = "artifact_download_failed"

    def __init__(self, *, detail: str) -> None:
        self.params = {"detail": detail}
        super().__init__(detail)


class AmbiguousSubmitError(RuntimeError):
    """Ambiguous failure during nonidempotent create/submit, which creates and bills tasks.

    Requests may have arrived, persisted, and billed while responses were lost through ReadTimeout,
    write timeouts, disconnects, RemoteProtocolError, etc. Automatic retries would duplicate tasks/charges,
    so do not retry; fail terminally. error_message carries ``[create_ambiguous]`` for operators to verify
    provider task state before manual retries, matching ``[restart_lost]`` / ``[resume_unsupported]``
    precedent preferring manual retries over duplicate billing, Agent-facing and exempt from i18n.
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
    """Same response, new message excluding query strings and userinfo.

    ``raise_for_status`` includes entire request URLs, exposing query-carried api_key and Basic-style
    base_url passwords in userinfo. ``str(exc)`` reaches ``task.error_message``, logs, and API responses.
    Keep exception type and ``response`` unchanged; retry predicates still use status_code.

    Nonempty ``provider_reason`` selects :class:`ProviderRejectedError`. For actual submission refusals
    without summaries, construct that subtype directly through :func:`provider_rejected_error`.
    """
    if provider_reason:
        return provider_rejected_error(exc, provider_reason=provider_reason)
    return httpx.HTTPStatusError(_redacted_message(exc), request=exc.request, response=exc.response)


def provider_rejected_error(exc: httpx.HTTPStatusError, *, provider_reason: str | None) -> ProviderRejectedError:
    """Deterministic 4xx submission refusal: same response, message without query/userinfo, optional summary.

    Use regardless of summary presence: readers recognize rejection by type, localize messages, and
    provide FIX_INPUT. Branching only by summaries would demote empty-body/hidden-auth failures to raw statuses.
    """
    return ProviderRejectedError(
        _redacted_message(exc), request=exc.request, response=exc.response, provider_reason=provider_reason
    )


def _redacted_message(exc: httpx.HTTPStatusError) -> str:
    # Strip both userinfo/query: user-configured base_url may be https://user:pw@host, embedding passwords
    # in authority fields retained by httpx. Messages enter task.error_message and return in task lists.
    return f"{exc.response.status_code} response for {exc.request.url.copy_with(query=None, userinfo=b'')}"


def raise_for_status_redacted(response: httpx.Response) -> None:
    """Validate status; failures raise ``HTTPStatusError`` without query strings or userinfo."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise redacted_status_error(exc) from None
