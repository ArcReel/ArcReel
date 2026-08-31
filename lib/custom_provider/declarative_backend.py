"""声明式 JSON 提交/轮询视频调用通道。

住在 ``lib.custom_provider`` 而非 ``lib.video_backends``：本 backend 的输入是自定义调用端点
的定义格式，读定义要用模板引擎与响应提取，而分层契约（``pyproject.toml``
``[tool.importlinter]``）不允许 backend 层反向依赖 ``lib.custom_provider``。方向与
``endpoints.py`` 装配各家 backend 一致——上层消费下层，下层不知道声明式定义的存在。
"""

from __future__ import annotations

import logging
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from lib.custom_provider.endpoint_definition import (
    AssetData,
    JsonPathEvaluationError,
    RenderedRequest,
    TemplateRenderError,
    build_context,
    encode_inputs,
    extract_value,
    map_status,
    render_request,
)
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.logging_utils import format_kwargs_for_log
from lib.retry import retry_async
from lib.validation_messages import ValidationMessage
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ProviderResponseStage,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    notify_provider_response,
    poll_with_retry,
    redacted_status_error,
    request_with_scoped_credentials,
    should_retry_poll,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)

_HTTP_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)


def _url_without_auth_query(rendered: RenderedRequest) -> str:
    """日志用 URL：``auth.query`` 的凭证是明文参数，写进日志前整段摘掉。

    ``format_kwargs_for_log`` 按键名遮蔽，URL 是单个字符串遮不到里面的凭证；渲染结果
    单列了 ``auth_query``，据此把这些参数从查询串里删掉再入日志。
    """
    if not rendered.auth_query:
        return rendered.url
    head, separator, query = rendered.url.partition("?")
    if not separator:
        return rendered.url
    secrets = {quote(name, safe="") for name in rendered.auth_query}
    kept = [item for item in query.split("&") if item.partition("=")[0] not in secrets]
    return f"{head}?{'&'.join(kept)}" if kept else head


#: 超过该长度且全由 base64 字符组成的字符串按素材摘要处理。真实提示词带空格与标点，不会命中。
_BASE64_LOG_THRESHOLD = 256
_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/=\r\n]+")


def _asset_payloads(context: Mapping[str, object]) -> frozenset[str]:
    """一次渲染编码出的素材值。日志据此按来源摘素材，不靠体积猜——一张 1×1 的 PNG 编出来
    比任何阈值都短，按长度判会把它整串写进日志。"""
    inputs = context.get("inputs")
    if not isinstance(inputs, Mapping):
        return frozenset()
    payloads: set[str] = set()
    for value in inputs.values():
        candidates = value if isinstance(value, list) else [value]
        payloads.update(item for item in candidates if isinstance(item, str) and item)
    return frozenset(payloads)


def _body_for_log(value: object, assets: frozenset[str]) -> object:
    """日志用请求体：``encoding: "base64"`` 的素材渲染出来是裸 base64 串，通用格式化器认不出
    （data URI 有前缀可识别，裸串没有），截断后仍会把用户媒体的可还原前缀写进日志。

    首选按来源摘：``assets`` 是同一次渲染编码出的素材值，出现在字符串里就整段换掉，与长度无关，
    也不要求整串相等——定义允许把素材拼进 ``"prefix{{ inputs.first_frame }}"` 这类混合文本，
    那种形状既不等值也过不了纯 base64 兜底。长的先换，短值才不会先吃掉长值的一段。

    形状兜底留给对不上任何素材值的串（如定义在渲染后又拼接过），真实提示词带空格与标点，
    不会命中。"""
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
    """日志用 headers：``auth.headers`` 允许任意名字（如 ``Ocp-Apim-Subscription-Key``），
    ``format_kwargs_for_log`` 按通用敏感词遮不到，按定义声明的键名整体遮掉。"""
    auth_names = {name.lower() for name in (auth or {}).get("headers", {})}
    return {name: "***" if name.lower() in auth_names else value for name, value in headers.items()}


#: 定义里会渲染出 URL 的三节。
REQUEST_SECTIONS = ("submit", "poll", "result")
#: 直接以 base_url + 显式版本段起头的 URL 模板。
_VERSIONED_BASE_URL = re.compile(r"^\s*\{\{\s*base_url\s*\}\}/v\d")


def request_urls(definition: Mapping[str, Any]) -> list[str]:
    """定义里三节请求各自的 URL 模板，缺席的节跳过。

    与 base_url 有关的判定（是否必需、要不要剥版本段）都按整份定义算：提交写死绝对地址、
    轮询才引用 base_url 的定义是合法的，只看提交会让判定与真正发出去的 URL 脱节。
    """
    urls: list[str] = []
    for section in REQUEST_SECTIONS:
        node = definition.get(section)
        if isinstance(node, Mapping):
            urls.append(str(node.get("url", "")))
    return urls


def normalize_declarative_base_url(base_url: str, definition: Mapping[str, Any]) -> str:
    """补协议 + 去尾斜杠；定义带显式版本路径时剥配置末尾版本段，避免拼成 ``/v1/v2``。

    无 scheme 的纯域名（如 ``api.aimlapi.com``）补 ``https://``——httpx 拒收缺协议的相对
    URL，而存量自定义供应商的 ``base_url`` 是不受约束的字符串。预览与真发共用这一份归一化：
    分叉会让预览展示的 URL 与实际提交的不一致。
    """
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
        super().__init__(detail.render() if isinstance(detail, ValidationMessage) else detail)


@dataclass(frozen=True)
class ProviderState:
    """一次供应商响应按定义读出的结果：状态、产物地址、错误、二次取件 id 与计费时长。"""

    body: object
    status: ProviderJobStatus
    video_url: str | None
    error: str | None
    result_id: str | None
    duration_seconds: int | None


def extract_provider_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ProviderState:
    """按一节 ``extract`` 读一份响应体。运行时与验证响应共用的唯一判读实现。

    验证响应之所以能替代真花钱的调用，全靠它给出的判定与运行时逐字一致；两处各读一遍
    ``extract``，只要有一处对 ``failure`` 或状态回落的处理稍有出入，验证就会在用户最信任它的
    场合给出反的结论。
    """
    try:
        failure = extract_value(extract["failure"], body) if "failure" in extract else None
        mapped = status or map_status(extract_value(extract.get("status"), body), status_map)
        if failure is not None:
            mapped = ProviderJobStatus.FAILED
        return ProviderState(
            body=body,
            status=mapped,
            video_url=extract_text(extract.get("video_url"), body),
            error=extract_text(extract.get("error"), body),
            result_id=extract_text(extract.get("result_id"), body),
            duration_seconds=extract_duration((extract.get("usage") or {}).get("duration_seconds"), body),
        )
    except JsonPathEvaluationError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc


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


def extract_duration(spec: object | None, body: object) -> int | None:
    """按 ``usage.duration_seconds`` 取计费时长：half-up 取整后越界即视为未回报。"""
    if spec is None:
        return None
    raw = extract_value(spec, body)
    try:
        value = int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except JsonPathEvaluationError:
        raise
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if 0 < value <= MAX_BILLED_DURATION_SECONDS else None


class DeclarativeVideoBackend(ProviderJobIdPersistenceMixin):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        definition: Mapping[str, Any],
        provider: str,
    ) -> None:
        self._api_key = api_key
        self._base_url = normalize_declarative_base_url(base_url, definition)
        self._model = model
        self._definition = definition
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def video_capabilities(self) -> VideoCapabilities:
        # 延迟导入：能力合成模块经 endpoints.py 反向依赖本模块，模块级导入会成环。
        from lib.custom_provider.capabilities import video_capabilities_from_definition

        return video_capabilities_from_definition(self._definition)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = self._request_context(request, require_declared_inputs=True)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._submit(client, context, request)
            # 落提交域名供续跑回放：用户在途改了供应商 base_url 时，按新域名轮旧 job 会查无，
            # 把一笔已付费的任务误判成过期丢掉。
            await self._persist_provider_job_id(request, job_id, provider=self._provider, endpoint=self._base_url)
            return await self._poll_download(client, job_id, request, context=context, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = self._request_context(request)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            return await self._poll_download(client, job_id, request, context=context, is_resume=True)

    def _request_context(
        self, request: VideoGenerationRequest, *, require_declared_inputs: bool = False
    ) -> dict[str, object]:
        """构造模板上下文。

        ``require_declared_inputs`` 只在提交路径为真：素材是提交的输入，续跑的请求本就不带
        素材（校验器也禁止 poll / result 模板引用 inputs），在续跑上查必需项会把每一笔
        「必需图输入」端点的已付费任务判死在第一次轮询之前。
        """
        declarations = self._definition.get("inputs") or {}
        try:
            # 素材读盘也在守卫内：文件在任务准备与执行之间被删掉时抛的是 OSError，落在守卫外
            # 就会绕过稳定错误码，让 worker 存下一段没有译文的裸文本。
            assets: dict[str, AssetData | list[AssetData] | None] = {
                "start_image": self._asset(request.start_image),
                "end_image": self._asset(request.end_image),
                "reference_images": self._assets(request.reference_images),
                "reference_audio_files": self._assets(request.reference_audio_files),
            }
            encoded = encode_inputs(declarations, assets)
            # 声明为必需的素材缺席时就地失败：模板会把整串占位符的键直接删掉，请求照样发得出去，
            # 于是供应商收到一个残缺请求、照常建任务照常计费。
            missing = (
                [name for name, value in encoded.items() if declarations[name].get("required") and not value]
                if require_declared_inputs
                else []
            )
            if missing:
                raise DeclarativeRuntimeError(
                    "declarative_template_render_failed",
                    detail=f"required inputs are missing: {', '.join(sorted(missing))}",
                )
            return build_context(
                {
                    "api_key": self._api_key,
                    # 续跑回放提交时的域名（提交路径恒 None）：域名是连接维度，不是协议维度。
                    "base_url": request.submitted_base_url or self._base_url,
                    "model": self._model,
                    "prompt": request.prompt,
                    "duration": request.duration_seconds,
                    "duration_seconds": request.duration_seconds,
                    "aspect_ratio": request.aspect_ratio,
                    "resolution": request.resolution,
                    "generate_audio": request.generate_audio,
                    "seed": request.seed,
                },
                encoded,
                self._definition.get("defaults"),
            )
        except TemplateRenderError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=exc.message) from exc
        except OSError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    @staticmethod
    def _asset(path: Path | None) -> AssetData | None:
        if path is None:
            return None
        mime = (
            IMAGE_MIME_TYPES.get(path.suffix.lower())
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream"
        )
        return AssetData(mime, path.read_bytes())

    @classmethod
    def _assets(cls, paths: list[Path] | None) -> list[AssetData] | None:
        return [asset for path in paths or [] if (asset := cls._asset(path)) is not None] or None

    def _render(self, section: Mapping[str, Any], context: Mapping[str, object]):
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

    def _endpoint_origin(
        self, section: Mapping[str, Any], context: Mapping[str, object]
    ) -> tuple[str, str, int | None]:
        """该节渲染出的请求地址的源——凭证实际发往的那个。

        取的是渲染结果而不是响应上的 URL：后者是跟随重定向之后的终点，跨源跳转时凭证早已
        被卸掉，把它当可信源等于把凭证发给一个从没验证过的第三方主机。
        """
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

    async def _submit(
        self,
        client: httpx.AsyncClient,
        context: Mapping[str, object],
        request: VideoGenerationRequest,
    ) -> str:
        section = self._definition["submit"]

        async def operation() -> httpx.Response:
            async def post() -> httpx.Response:
                response = await self._send_without_status(client, section, context)
                if response.status_code >= 400:
                    await self._record_response(response, request, "submit")
                return response

            return await submit_post(
                post,
                provider=self._provider,
            )

        response = await retry_async(operation, retry_if=should_retry_submit)
        body = await self._response_body(response, request, "submit")
        try:
            job_id = extract_value(section["extract"]["task_id"], body)
            error = extract_text(section["extract"].get("error"), body)
        except JsonPathEvaluationError as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
        except (TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
        # accept="scalar" 的定义可以命中数字或布尔，格式说明的口径是「按字符串化交给下游」；
        # 这里按 str 收，与产物地址、result_id 的取值口径一致。
        task_id = str(job_id).strip() if job_id is not None and not isinstance(job_id, (list, dict)) else ""
        if not task_id:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=error or "submit response did not contain a provider task id",
            )
        return task_id

    async def _send_without_status(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        rendered = self._render(section, context)
        logger.info(
            "声明式视频请求: %s",
            format_kwargs_for_log(
                {
                    "method": rendered.method,
                    "url": _url_without_auth_query(rendered),
                    "headers": _headers_with_auth_masked(rendered.headers, self._definition.get("auth")),
                    "body": _body_for_log(rendered.body, _asset_payloads(context)),
                }
            ),
        )
        # 提交 / 轮询 / 二次取件都带着渲染出的 auth 节：重定向必须自己逐跳跟随，跨源卸凭证。
        return await request_with_scoped_credentials(
            client,
            rendered.method,
            rendered.url,
            headers=rendered.headers,
            json=rendered.body if rendered.body is not None else None,
            auth_query=rendered.auth_query,
        )

    async def _poll_download(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        request: VideoGenerationRequest,
        *,
        context: Mapping[str, object],
        is_resume: bool,
    ) -> VideoGenerationResult:
        poll_context = {**context, "task_id": job_id}
        # 定义可以把端点写在与 base_url 不同的主机上，产物地址往往就出自那些主机。信任集只收
        # 真实带凭证访问的请求源：提交源进入轮询前已带凭证访问过（续跑时它同样是任务所在的
        # 供应商源），轮询与二次取件的源在各自成功取件后加入（见 fetch）。全绝对地址定义下
        # 配置的 base_url 可能从未被请求，凭配置信任会把凭证发给一个从没验证过的源；其余
        # （对象存储 / CDN）裸请求。
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
            """取件一次，失败响应一律留痕。

            只有轮询端点的 404 意味着「远端任务没了」：那是任务本身的地址。result 端点的
            404 说的是产物还没就绪，续跑期把它判成过期会永久丢掉一条已经成功的付费任务，
            它该留给取件预算去重试。
            """
            try:
                response = await self._send(client, section, section_context)
            except httpx.HTTPStatusError as exc:
                await self._record_response(exc.response, request, stage)
                if expire_on_404 and is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=job_id, provider=self._provider) from exc
                raise
            trusted_origins.add(self._endpoint_origin(section, section_context))
            return await self._response_body(response, request, stage)

        async def poll_once() -> ProviderState:
            return self._extract_state(
                # 缺省 true（404=任务已不存在，续跑一击判过期）；供应商对在跑任务可能瞬时 404 的
                # 定义写 false，按瞬态错误重试到预算耗尽。
                await fetch(
                    self._definition["poll"],
                    poll_context,
                    stage="poll",
                    expire_on_404=bool(self._definition["poll"].get("expire_on_404", True)),
                ),
                self._definition["poll"]["extract"],
            )

        final = await poll_with_retry(
            poll_fn=poll_once,
            is_done=lambda state: state.status is ProviderJobStatus.SUCCEEDED,
            # 终态失败必须给出非空理由：返回 None 会让 poll_with_retry 认为任务仍在进行，
            # 一路轮询到 max_wait 才超时，而供应商早已判负。
            is_failed=lambda state: (
                (state.error or "provider reported failure") if state.status is ProviderJobStatus.FAILED else None
            ),
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label=self._provider,
        )
        video_url = final.video_url
        duration = final.duration_seconds
        if "result" in self._definition:
            result_context = {**poll_context, "result_id": final.result_id}

            async def fetch_result() -> object:
                return await fetch(self._definition["result"], result_context, stage="result", expire_on_404=False)

            # 走到这里供应商任务已经成功、钱已经花了，二次取件与产物下载同属「任务已建成后的
            # 幂等取件」：共用同一份预算，别让一次 429 / 5xx / 尚未收敛的 404 直接废掉成片。
            # 预算耗尽同样落 artifact_download_failed——「重试下载」走 resume 会重跑轮询与二次
            # 取件，恢复路径与下载失败完全一致，不必重新提交。
            try:
                result_body = await with_artifact_retry(
                    fetch_result,
                    label=f"{self._provider} result",
                    retry_if=should_retry_poll,
                    max_wait=request.poll_timeout_seconds,
                )
            except (ResumeExpiredError, DeclarativeRuntimeError):
                raise
            except Exception as exc:
                raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc
            result_state = self._extract_state(
                result_body,
                self._definition["result"]["extract"],
                status=ProviderJobStatus.SUCCEEDED,
            )
            video_url = result_state.video_url
            duration = result_state.duration_seconds or duration
            final = result_state
        if not video_url:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=final.error or "provider reported success but no video URL matched the definition",
            )
        await self._download(
            client,
            video_url,
            request.output_path,
            context,
            request.poll_timeout_seconds,
            trusted_origins=trusted_origins,
        )
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            duration_seconds=duration or request.duration_seconds,
            video_uri=video_url,
            task_id=job_id,
            generate_audio=request.generate_audio,
        )

    def _extract_state(
        self,
        body: object,
        extract: Mapping[str, Any],
        *,
        status: ProviderJobStatus | None = None,
    ) -> ProviderState:
        return extract_provider_state(body, extract, status_map=self._definition.get("status_map"), status=status)

    @staticmethod
    async def _response_body(
        response: httpx.Response, request: VideoGenerationRequest, stage: ProviderResponseStage
    ) -> object:
        try:
            body = response.json()
        except ValueError as exc:
            # 2xx 但不是 JSON（网关的 HTML 错误页、被截断的响应）：原文先留痕再抛。诊断字段
            # 若停在上一次轮询的响应上，正好在最需要看到这次响应的场合给出误导。
            await notify_provider_response(request, stage, response.text)
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed", detail="provider response was not valid JSON"
            ) from exc
        await notify_provider_response(request, stage, body)
        return body

    @staticmethod
    async def _record_response(
        response: httpx.Response, request: VideoGenerationRequest, stage: ProviderResponseStage
    ) -> None:
        """留痕失败响应；非 JSON 体存截断后的原文，不因此让任务多失败一种方式。"""
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        await notify_provider_response(request, stage, body)

    async def _download(
        self,
        client: httpx.AsyncClient,
        url: str,
        output_path: Path,
        context: Mapping[str, object],
        max_wait: float,
        trusted_origins: set[tuple[str, str, int | None]],
    ) -> None:
        # 与已携带凭证访问过的某个源同源，才按 auth 节渲染凭证；其余（对象存储 / CDN 的
        # 签名 URL）裸请求——附带凭证会破坏签名、并把 query 凭证写进第三方访问日志。
        # 凭证的作用域取产物地址自身的源，一路带到重定向跟随处，换源即卸。
        credential_origin = url_origin(url)
        rendered = (
            self._render({"method": "GET", "url": url}, context) if credential_origin in trusted_origins else None
        )

        async def download_once() -> None:
            await stream_to_file(
                client,
                rendered.url if rendered is not None else url,
                output_path,
                headers=rendered.headers if rendered is not None else None,
                credential_origin=credential_origin if rendered is not None else None,
                auth_query=rendered.auth_query if rendered is not None else None,
            )

        try:
            await with_artifact_retry(
                download_once,
                label=f"{self._provider} artifact download",
                max_wait=max_wait,
            )
        except Exception as exc:
            raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc
