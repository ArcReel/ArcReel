"""声明式端点的媒体无关运行时：提交、轮询、状态映射、二次取件与错误分类。

本模块不知道调用方产出的是视频、图片或其他素材。请求上下文由 backend 构造，extract
只读出状态、错误与二次取件 id；素材字段、输出类型和落盘上限由装配层负责。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx

from arcreel_market_core.endpoint_definition import (
    JsonPathEvaluationError,
    RenderedRequest,
    TemplateRenderError,
    extract_value,
    map_status,
    render_request,
)
from arcreel_market_core.job_contract import ProviderJobStatus, ProviderResponseStage, ResumeExpiredError
from arcreel_market_core.validation_messages import ValidationMessage
from lib.backends.backend_runtime import (
    poll_with_retry,
    request_with_scoped_credentials,
    should_retry_poll,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from lib.backends.http_status_errors import redacted_status_error
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import NonRetryableError, retry_async
from lib.infra.validation_messages import default_translate

logger = logging.getLogger(__name__)

Origin = tuple[str, str, int | None]
ResponseRecorder = Callable[[ProviderResponseStage, object], Awaitable[None]]
FinalStage = Literal["poll", "result"]

#: 定义里会渲染出 URL 的三节。
REQUEST_SECTIONS = ("submit", "poll", "result")
#: 直接以 base_url + 显式版本段起头的 URL 模板。
_VERSIONED_BASE_URL = re.compile(r"^\s*\{\{\s*base_url\s*\}\}/v\d")

#: 超过该长度且全由 base64 字符组成的字符串按素材摘要处理。真实提示词带空格与标点，不会命中。
_BASE64_LOG_THRESHOLD = 256
_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=\r\n]+")


def _url_without_auth_query(rendered: RenderedRequest) -> str:
    """日志用 URL：``auth.query`` 的凭证是明文参数，写进日志前整段摘掉。"""
    if not rendered.auth_query:
        return rendered.url
    head, separator, query = rendered.url.partition("?")
    if not separator:
        return rendered.url
    secrets = {quote(name, safe="") for name in rendered.auth_query}
    kept = [item for item in query.split("&") if item.partition("=")[0] not in secrets]
    return f"{head}?{'&'.join(kept)}" if kept else head


def _asset_payloads(context: Mapping[str, object]) -> frozenset[str]:
    """一次渲染编码出的素材值。日志据此按来源摘素材，不靠体积猜。"""
    inputs = context.get("inputs")
    if not isinstance(inputs, Mapping):
        return frozenset()
    payloads: set[str] = set()
    for value in inputs.values():
        candidates = value if isinstance(value, list) else [value]
        payloads.update(item for item in candidates if isinstance(item, str) and item)
    return frozenset(payloads)


def _body_for_log(value: object, assets: frozenset[str]) -> object:
    """日志用请求体：按来源摘掉编码后的素材，并对纯 base64 长串做形状兜底。"""
    if isinstance(value, str):
        redacted = value
        for payload in sorted(assets, key=len, reverse=True):
            redacted = redacted.replace(payload, f"<asset:{len(payload)} chars>")
        if redacted != value:
            return redacted
        if len(value) >= _BASE64_LOG_THRESHOLD and _BASE64_PATTERN.fullmatch(value):
            return f"<base64:{len(value)} chars>"
        return value
    if isinstance(value, list):
        return [_body_for_log(item, assets) for item in value]
    if isinstance(value, dict):
        return {key: _body_for_log(item, assets) for key, item in value.items()}
    return value


def _headers_with_auth_masked(headers: Mapping[str, str], auth: Mapping[str, Any] | None) -> dict[str, str]:
    """日志用 headers：按定义声明的 auth header 名整体遮蔽。"""
    auth_names = {name.lower() for name in (auth or {}).get("headers", {})}
    return {name: "***" if name.lower() in auth_names else value for name, value in headers.items()}


def request_urls(definition: Mapping[str, Any]) -> list[str]:
    """定义里三节请求各自的 URL 模板，缺席的节跳过。"""
    urls: list[str] = []
    for section in REQUEST_SECTIONS:
        node = definition.get(section)
        if isinstance(node, Mapping):
            urls.append(str(node.get("url", "")))
    return urls


def normalize_declarative_base_url(base_url: str, definition: Mapping[str, Any]) -> str:
    """补协议 + 去尾斜杠；定义带显式版本路径时剥配置末尾版本段。"""
    stripped = base_url.strip().rstrip("/")
    if stripped and "://" not in stripped:
        stripped = f"https://{stripped}"
    if any(_VERSIONED_BASE_URL.match(url) for url in request_urls(definition)):
        return re.sub(r"/v\d+(?:\.\d+)?[a-zA-Z]*$", "", stripped)
    return stripped


class DeclarativeRuntimeError(RuntimeError):
    """声明式定义执行失败，携带可持久化、本地化的稳定错误码。"""

    def __init__(self, code: str, *, detail: str | ValidationMessage) -> None:
        self.code = code
        self.params = {
            "detail": {"key": detail.key, "params": dict(detail.params)}
            if isinstance(detail, ValidationMessage)
            else detail
        }
        super().__init__(detail.render(default_translate) if isinstance(detail, ValidationMessage) else detail)


@dataclass(frozen=True)
class ProviderRuntimeState:
    """一次响应按媒体无关 extract 读出的结果。"""

    body: object
    status: ProviderJobStatus
    error: str | None
    result_id: str | None


@dataclass(frozen=True)
class DeclarativePollOutcome:
    """轮询终态、轮询本身的状态与最终素材响应。"""

    final_state: ProviderRuntimeState
    poll_state: ProviderRuntimeState
    final_stage: FinalStage
    trusted_origins: frozenset[Origin]


def text_or_none(value: object | None) -> str | None:
    """把取到的值收成文案：空白与缺席一律 ``None``。"""
    if value is None:
        return None
    return str(value).strip() or None


def extract_text(spec: object | None, body: object) -> str | None:
    """按一条提取规则取字符串；无命中或空白一律 ``None``。"""
    if spec is None:
        return None
    return text_or_none(extract_value(spec, body))


def extract_runtime_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ProviderRuntimeState:
    """按一节 extract 读取媒体无关状态、错误与二次取件 id。"""
    try:
        failure = extract_value(extract["failure"], body) if "failure" in extract else None
        if status is None:
            raw_status = extract_value(extract["status"], body)
            mapped = map_status(raw_status, status_map)
        else:
            mapped = status
        if failure is not None:
            mapped = ProviderJobStatus.FAILED
        return ProviderRuntimeState(
            body=body,
            status=mapped,
            error=extract_text(extract.get("error"), body),
            result_id=extract_text(extract.get("result_id"), body),
        )
    except JsonPathEvaluationError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc


class DeclarativeRuntime:
    """一份声明式定义的媒体无关执行通道。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        definition: Mapping[str, Any],
        provider: str,
        logger_: logging.Logger | None = None,
        log_label: str = "声明式请求",
    ) -> None:
        self._api_key = api_key
        self._base_url = normalize_declarative_base_url(base_url, definition)
        self._definition = definition
        self._provider = provider
        self._logger = logger_ or logger
        self._log_label = log_label

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def definition(self) -> Mapping[str, Any]:
        return self._definition

    @property
    def provider(self) -> str:
        return self._provider

    async def submit(
        self,
        client: httpx.AsyncClient,
        context: Mapping[str, object],
        *,
        on_response: ResponseRecorder,
    ) -> str:
        """执行提交并用定义的 task_id 路径读出供应商任务标识。"""
        section = self._definition["submit"]

        async def operation() -> httpx.Response:
            async def post() -> httpx.Response:
                response = await self._send_without_status(client, section, context)
                if response.status_code >= 400:
                    await self._record_response(response, on_response, "submit")
                return response

            return await submit_post(post, provider=self._provider)

        response = await retry_async(operation, retry_if=should_retry_submit)
        body = await self._response_body(response, on_response, "submit")
        try:
            job_id = extract_value(section["extract"]["task_id"], body)
            error = extract_text(section["extract"].get("error"), body)
        except JsonPathEvaluationError as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
        except (TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
        task_id = str(job_id).strip() if job_id is not None and not isinstance(job_id, (list, dict)) else ""
        if not task_id:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=error or "submit response did not contain a provider task id",
            )
        return task_id

    async def poll_for_result(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        *,
        context: Mapping[str, object],
        poll_timeout_seconds: float,
        is_resume: bool,
        on_response: ResponseRecorder,
    ) -> DeclarativePollOutcome:
        """轮询到成功，并在定义存在 result 节时完成二次取件。"""
        poll_context = {**context, "task_id": job_id}
        trusted_origins = {
            url_origin(self._render({"method": "GET", "url": self._definition["submit"]["url"]}, context).url)
        }

        async def fetch(
            section: Mapping[str, Any],
            section_context: Mapping[str, object],
            *,
            stage: ProviderResponseStage,
            expire_on_404: bool,
        ) -> object:
            try:
                response = await self._send(client, section, section_context)
            except httpx.HTTPStatusError as exc:
                await self._record_response(exc.response, on_response, stage)
                if expire_on_404 and is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=job_id, provider=self._provider) from exc
                raise
            trusted_origins.add(self._endpoint_origin(section, section_context))
            return await self._response_body(response, on_response, stage)

        async def poll_once() -> ProviderRuntimeState:
            return self._extract_state(
                await fetch(
                    self._definition["poll"],
                    poll_context,
                    stage="poll",
                    expire_on_404=bool(self._definition["poll"].get("expire_on_404", True)),
                ),
                self._definition["poll"]["extract"],
            )

        poll_state = await poll_with_retry(
            poll_fn=poll_once,
            is_done=lambda state: state.status is ProviderJobStatus.SUCCEEDED,
            is_failed=lambda state: (
                (state.error or "provider reported failure") if state.status is ProviderJobStatus.FAILED else None
            ),
            max_wait=poll_timeout_seconds,
            retry_if=should_retry_poll,
            label=self._provider,
        )
        final_state = poll_state
        final_stage: FinalStage = "poll"
        if "result" in self._definition:
            result_context = {**poll_context, "result_id": poll_state.result_id}

            async def fetch_result() -> object:
                return await fetch(
                    self._definition["result"],
                    result_context,
                    stage="result",
                    expire_on_404=False,
                )

            try:
                result_body = await with_artifact_retry(
                    fetch_result,
                    label=f"{self._provider} result",
                    retry_if=should_retry_poll,
                    max_wait=poll_timeout_seconds,
                )
            except (ResumeExpiredError, DeclarativeRuntimeError, NonRetryableError):
                raise
            except Exception as exc:
                raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc
            final_state = self._extract_state(
                result_body,
                self._definition["result"]["extract"],
                status=ProviderJobStatus.SUCCEEDED,
            )
            final_stage = "result"
        return DeclarativePollOutcome(
            final_state=final_state,
            poll_state=poll_state,
            final_stage=final_stage,
            trusted_origins=frozenset(trusted_origins),
        )

    async def download_artifact(
        self,
        client: httpx.AsyncClient,
        url: str,
        output_path: Path,
        context: Mapping[str, object],
        *,
        max_wait: float,
        trusted_origins: frozenset[Origin],
        max_bytes: int,
        label: str,
    ) -> None:
        """把最终素材地址流式取回目标路径，复用产物取件重试预算。"""
        credential_origin = url_origin(url)
        rendered = (
            self._render({"method": "GET", "url": url}, context) if credential_origin in trusted_origins else None
        )

        async def download_once() -> None:
            await stream_to_file(
                client,
                rendered.url if rendered is not None else url,
                output_path,
                max_bytes=max_bytes,
                headers=rendered.headers if rendered is not None else None,
                credential_origin=credential_origin if rendered is not None else None,
                auth_query=rendered.auth_query if rendered is not None else None,
            )

        try:
            await with_artifact_retry(download_once, label=label, max_wait=max_wait)
        except NonRetryableError:
            raise
        except Exception as exc:
            raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc

    def _extract_state(
        self,
        body: object,
        extract: Mapping[str, Any],
        *,
        status: ProviderJobStatus | None = None,
    ) -> ProviderRuntimeState:
        return extract_runtime_state(body, extract, status_map=self._definition.get("status_map"), status=status)

    def _render(self, section: Mapping[str, Any], context: Mapping[str, object]) -> RenderedRequest:
        try:
            return render_request(
                section,
                context,
                enum_maps=self._definition.get("enum_maps"),
                auth=self._definition.get("auth"),
            )
        except TemplateRenderError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=exc.message) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    def _endpoint_origin(self, section: Mapping[str, Any], context: Mapping[str, object]) -> Origin:
        return url_origin(self._render(section, context).url)

    async def _send(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        response = await self._send_without_status(client, section, context)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise redacted_status_error(exc) from None
        return response

    async def _send_without_status(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        rendered = self._render(section, context)
        self._logger.info(
            "%s: %s",
            self._log_label,
            format_kwargs_for_log(
                {
                    "method": rendered.method,
                    "url": _url_without_auth_query(rendered),
                    "headers": _headers_with_auth_masked(rendered.headers, self._definition.get("auth")),
                    "body": _body_for_log(rendered.body, _asset_payloads(context)),
                }
            ),
        )
        return await request_with_scoped_credentials(
            client,
            rendered.method,
            rendered.url,
            headers=rendered.headers,
            json=rendered.body if rendered.body is not None else None,
            auth_query=rendered.auth_query,
        )

    @staticmethod
    async def _response_body(
        response: httpx.Response,
        on_response: ResponseRecorder,
        stage: ProviderResponseStage,
    ) -> object:
        try:
            body = response.json()
        except ValueError as exc:
            await on_response(stage, response.text)
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed", detail="provider response was not valid JSON"
            ) from exc
        await on_response(stage, body)
        return body

    @staticmethod
    async def _record_response(
        response: httpx.Response,
        on_response: ResponseRecorder,
        stage: ProviderResponseStage,
    ) -> None:
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        await on_response(stage, body)
