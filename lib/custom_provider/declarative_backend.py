"""声明式 JSON 提交/轮询视频调用通道。

住在 ``lib.custom_provider`` 而非 ``lib.video_backends``：本 backend 的输入是自定义调用端点
的定义格式，读定义要用模板引擎与响应提取，而分层契约（``pyproject.toml``
``[tool.importlinter]``）不允许 backend 层反向依赖 ``lib.custom_provider``。方向与
``endpoints.py`` 装配各家 backend 一致——上层消费下层，下层不知道声明式定义的存在。
"""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from lib.custom_provider.endpoint_definition import (
    AssetData,
    TemplateRenderError,
    build_context,
    encode_inputs,
    extract_value,
    map_status,
    render_request,
)
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.retry import retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    notify_provider_response,
    poll_with_retry,
    should_retry_poll,
    should_retry_submit,
    stream_to_file,
    submit_post,
    with_artifact_retry,
)

_HTTP_TIMEOUT_SECONDS = 60


class DeclarativeRuntimeError(RuntimeError):
    """声明式定义执行失败，携带可持久化、本地化的稳定错误码。"""

    def __init__(self, code: str, *, detail: str) -> None:
        self.code = code
        self.params = {"detail": detail}
        super().__init__(detail)


@dataclass(frozen=True)
class _ProviderState:
    body: object
    status: ProviderJobStatus
    video_url: str | None
    error: str | None
    result_id: str | None
    duration_seconds: int | None


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
        self._base_url = base_url.rstrip("/")
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
        context = self._request_context(request)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._submit(client, context, request)
            await self._persist_provider_job_id(request, job_id, provider=self._provider)
            return await self._poll_download(client, job_id, request, context=context, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = self._request_context(request)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            return await self._poll_download(client, job_id, request, context=context, is_resume=True)

    def _request_context(self, request: VideoGenerationRequest) -> dict[str, object]:
        assets: dict[str, AssetData | list[AssetData] | None] = {
            "start_image": self._asset(request.start_image),
            "end_image": self._asset(request.end_image),
            "reference_images": self._assets(request.reference_images),
            "reference_audio_files": self._assets(request.reference_audio_files),
        }
        declarations = self._definition.get("inputs") or {}
        try:
            encoded = encode_inputs(declarations, assets)
            return build_context(
                {
                    "api_key": self._api_key,
                    "base_url": self._base_url,
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
            )
        except (OSError, TemplateRenderError) as exc:
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
        except (KeyError, TypeError, ValueError, TemplateRenderError) as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    async def _send(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        response = await self._send_without_status(client, section, context)
        response.raise_for_status()
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
                    await self._record_response(response, request)
                return response

            return await submit_post(
                post,
                provider=self._provider,
            )

        response = await retry_async(operation, retry_if=should_retry_submit)
        body = await self._response_body(response, request)
        try:
            job_id = extract_value(section["extract"]["task_id"], body)
            error = self._extract_text(section["extract"].get("error"), body)
        except (TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
        if not isinstance(job_id, str) or not job_id.strip():
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=error or "submit response did not contain a provider task id",
            )
        return job_id.strip()

    async def _send_without_status(
        self,
        client: httpx.AsyncClient,
        section: Mapping[str, Any],
        context: Mapping[str, object],
    ) -> httpx.Response:
        rendered = self._render(section, context)
        return await client.request(
            rendered.method,
            rendered.url,
            headers=rendered.headers,
            json=rendered.body if rendered.body is not None else None,
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

        async def poll_once() -> _ProviderState:
            try:
                response = await self._send(client, self._definition["poll"], poll_context)
            except httpx.HTTPStatusError as exc:
                await self._record_response(exc.response, request)
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=job_id, provider=self._provider) from exc
                raise
            return self._extract_state(
                await self._response_body(response, request), self._definition["poll"]["extract"]
            )

        final = await poll_with_retry(
            poll_fn=poll_once,
            is_done=lambda state: state.status is ProviderJobStatus.SUCCEEDED,
            is_failed=lambda state: state.error if state.status is ProviderJobStatus.FAILED else None,
            max_wait=request.poll_timeout_seconds,
            retry_if=should_retry_poll,
            label=self._provider,
        )
        video_url = final.video_url
        duration = final.duration_seconds
        if "result" in self._definition:
            result_context = {**poll_context, "result_id": final.result_id}
            response = await self._send(client, self._definition["result"], result_context)
            result_state = self._extract_state(
                await self._response_body(response, request),
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
        await self._download(client, video_url, request.output_path, context, request.poll_timeout_seconds)
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
    ) -> _ProviderState:
        try:
            failure = extract_value(extract["failure"], body) if "failure" in extract else None
            mapped = status or map_status(
                extract_value(extract.get("status"), body), self._definition.get("status_map")
            )
            if failure is not None:
                mapped = ProviderJobStatus.FAILED
            duration = self._duration((extract.get("usage") or {}).get("duration_seconds"), body)
            return _ProviderState(
                body=body,
                status=mapped,
                video_url=self._extract_text(extract.get("video_url"), body),
                error=self._extract_text(extract.get("error"), body),
                result_id=self._extract_text(extract.get("result_id"), body),
                duration_seconds=duration,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc

    @staticmethod
    def _extract_text(spec: object | None, body: object) -> str | None:
        if spec is None:
            return None
        value = extract_value(spec, body)
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _duration(spec: object | None, body: object) -> int | None:
        if spec is None:
            return None
        raw = extract_value(spec, body)
        try:
            value = int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return value if 0 < value <= MAX_BILLED_DURATION_SECONDS else None

    @staticmethod
    async def _response_body(response: httpx.Response, request: VideoGenerationRequest) -> object:
        try:
            body = response.json()
        except ValueError as exc:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed", detail="provider response was not valid JSON"
            ) from exc
        await notify_provider_response(request, body)
        return body

    @staticmethod
    async def _record_response(response: httpx.Response, request: VideoGenerationRequest) -> None:
        """留痕失败响应；非 JSON 体按 #2126 存截断字符串，不因此让任务多失败一种方式。"""
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        await notify_provider_response(request, body)

    async def _download(
        self,
        client: httpx.AsyncClient,
        url: str,
        output_path: Path,
        context: Mapping[str, object],
        max_wait: float,
    ) -> None:
        # 同源才按 auth 节渲染凭证；异源（对象存储 / CDN 签名 URL）裸请求，附带凭证会破坏
        # 签名、并把 query 凭证写进第三方访问日志。
        rendered = (
            self._render({"method": "GET", "url": url}, context)
            if self._origin(url) == self._origin(self._base_url)
            else None
        )

        async def download_once() -> None:
            await stream_to_file(
                client,
                rendered.url if rendered is not None else url,
                output_path,
                headers=rendered.headers if rendered is not None else None,
            )

        try:
            await with_artifact_retry(
                download_once,
                label=f"{self._provider} artifact download",
                max_wait=max_wait,
            )
        except Exception as exc:
            raise DeclarativeRuntimeError("artifact_download_failed", detail=str(exc)) from exc

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None)
        return parts.scheme.lower(), (parts.hostname or "").lower(), port
