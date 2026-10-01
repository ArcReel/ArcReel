"""声明式 JSON 提交/轮询视频调用通道。

媒体无关的提交、轮询、状态映射、二次取件与错误分类在 ``declarative_runtime``；本模块只装配
视频请求上下文、视频产物字段、计费时长与最终 ``VideoGenerationResult``。
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.endpoint_definition import (
    AssetData,
    JsonPathEvaluationError,
    TemplateRenderError,
    build_context,
    encode_inputs,
    extract_value,
)
from arcreel_market_core.video_backend_contract import (
    IMAGE_MIME_TYPES,
    ProviderJobStatus,
    ProviderResponseStage,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.artifact_download_guard import VIDEO_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import ProviderJobIdPersistenceMixin, notify_provider_response
from lib.custom_provider.declarative_runtime import (
    DeclarativeRuntime,
    DeclarativeRuntimeError,
    ProviderRuntimeState,
    extract_text,
    normalize_declarative_base_url,
    request_urls,
    text_or_none,
)
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS

_HTTP_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)

__all__ = [
    "DeclarativeRuntimeError",
    "DeclarativeVideoBackend",
    "ProviderState",
    "extract_duration",
    "extract_provider_state",
    "normalize_declarative_base_url",
    "request_urls",
    "text_or_none",
]


@dataclass(frozen=True)
class ProviderState:
    """一次供应商响应按定义读出的视频结果。"""

    body: object
    status: ProviderJobStatus
    video_url: str | None
    error: str | None
    result_id: str | None
    duration_seconds: int | None


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


def _video_state(state: ProviderRuntimeState, extract: Mapping[str, Any]) -> ProviderState:
    try:
        return ProviderState(
            body=state.body,
            status=state.status,
            video_url=extract_text(extract.get("video_url"), state.body),
            error=state.error,
            result_id=state.result_id,
            duration_seconds=extract_duration((extract.get("usage") or {}).get("duration_seconds"), state.body),
        )
    except JsonPathEvaluationError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc


def extract_provider_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ProviderState:
    """按一节 ``extract`` 读一份响应体，保留视频 backend 的公开兼容入口。"""
    from lib.custom_provider.declarative_runtime import extract_runtime_state

    state = extract_runtime_state(body, extract, status_map=status_map, status=status)
    return _video_state(state, extract)


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
        self._model = model
        self._definition = definition
        self._provider = provider
        self._runtime = DeclarativeRuntime(
            api_key=api_key,
            base_url=base_url,
            definition=definition,
            provider=provider,
            logger_=logger,
            log_label="声明式视频请求",
        )

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
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._runtime.submit(client, context, on_response=self._response_recorder(request))
            await self._persist_provider_job_id(
                request,
                job_id,
                provider=self._provider,
                endpoint=self._runtime.base_url,
            )
            return await self._poll_download(client, job_id, request, context=context, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        context = self._request_context(request)
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            return await self._poll_download(client, job_id, request, context=context, is_resume=True)

    def _request_context(
        self, request: VideoGenerationRequest, *, require_declared_inputs: bool = False
    ) -> dict[str, object]:
        """构造视频模板上下文。

        ``require_declared_inputs`` 只在提交路径为真：续跑的请求本就不带素材。
        """
        declarations = self._definition.get("inputs") or {}
        try:
            assets: dict[str, AssetData | list[AssetData] | None] = {
                "start_image": self._asset(request.start_image),
                "end_image": self._asset(request.end_image),
                "reference_images": self._assets(request.reference_images),
                "reference_audio_files": self._assets(request.reference_audio_files),
            }
            encoded = encode_inputs(declarations, assets)
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
                    "api_key": self._runtime.api_key,
                    # 续跑回放提交时的域名（提交路径恒 None）：域名是连接维度，不是协议维度。
                    "base_url": request.submitted_base_url or self._runtime.base_url,
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

    async def _poll_download(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        request: VideoGenerationRequest,
        *,
        context: Mapping[str, object],
        is_resume: bool,
    ) -> VideoGenerationResult:
        outcome = await self._runtime.poll_for_result(
            client,
            job_id,
            context=context,
            poll_timeout_seconds=request.poll_timeout_seconds,
            is_resume=is_resume,
            on_response=self._response_recorder(request),
        )
        poll_extract = self._definition["poll"]["extract"]
        poll_state = _video_state(outcome.poll_state, poll_extract)
        final_state = poll_state
        if outcome.final_stage == "result":
            final_state = _video_state(outcome.final_state, self._definition["result"]["extract"])
        video_url = final_state.video_url
        duration = final_state.duration_seconds or poll_state.duration_seconds
        if not video_url:
            raise DeclarativeRuntimeError(
                "declarative_response_extract_failed",
                detail=final_state.error or "provider reported success but no video URL matched the definition",
            )
        await self._runtime.download_artifact(
            client,
            video_url,
            request.output_path,
            context,
            max_wait=request.poll_timeout_seconds,
            trusted_origins=outcome.trusted_origins,
            max_bytes=VIDEO_ARTIFACT_MAX_BYTES,
            label=f"{self._provider} artifact download",
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

    @staticmethod
    def _response_recorder(
        request: VideoGenerationRequest,
    ) -> Callable[[ProviderResponseStage, object], Awaitable[None]]:
        async def record(stage: ProviderResponseStage, body: object) -> None:
            await notify_provider_response(request, stage, body)

        return record
