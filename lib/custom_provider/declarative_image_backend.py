"""声明式图片定义的调用通道：在 :class:`DeclarativeJobEngine` 上组装图片的请求与产物语义。

与视频通道共用提交、轮询、状态映射、二次取件与产物下载；图片一侧只多出自己的模板变量、
能力声明与产物字段。图片没有续跑协议：服务重启时在途的图片任务由重启恢复记为重启丢失，
所以这里不落供应商任务 id。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from arcreel_market_core.video_backend_contract import ProviderJobStatus
from lib.backends.artifact_download_guard import IMAGE_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS
from lib.backends.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.custom_provider.declarative_backend import (
    DeclarativeJobEngine,
    DeclarativeRuntimeError,
    JobCall,
    JobState,
    extract_job_status,
    extract_text,
    response_extract_guard,
)

_HTTP_TIMEOUT_SECONDS = 60

#: 一次图片任务等到终态的墙钟上限。
#:
#: 不读全局的 ``video_poll_timeout_seconds``：那个设置项说的是视频，把它的含义扩到图片上，用户调它
#: 的时候就不知道自己在调几件事。图片这一维没有可配项，取与 ComfyUI 图片端点相同的定值。
IMAGE_POLL_TIMEOUT_SECONDS = 1800

#: 图片定义 ``capabilities`` 节的字段 → 端点的图片能力。
_IMAGE_CAPABILITY_BY_FIELD: Mapping[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
}


def image_capabilities_from_definition(definition: Mapping[str, Any]) -> frozenset[ImageCapability]:
    """图片定义显式声明的图片能力。端点投影与 backend 共读这一份，两处不会给出不同的能力。"""
    declared: Mapping[str, Any] = definition.get("capabilities") or {}
    return frozenset(
        capability for name, capability in _IMAGE_CAPABILITY_BY_FIELD.items() if declared.get(name) is True
    )


@dataclass(frozen=True)
class ImageJobState(JobState):
    """图片定义的判读结果：在 :class:`JobState` 之上加产物地址。"""

    image_url: str | None


def extract_image_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ImageJobState:
    """按图片定义的一节 ``extract`` 读一份响应体。运行时与验证响应共用的唯一判读实现。"""
    with response_extract_guard():
        job_status, provider_status = extract_job_status(body, extract, status_map=status_map, status=status)
        return ImageJobState(
            body=body,
            status=job_status,
            provider_status=provider_status,
            image_url=extract_text(extract.get("image_url"), body),
            error=extract_text(extract.get("error"), body),
            result_id=extract_text(extract.get("result_id"), body),
        )


class DeclarativeImageBackend:
    """声明式图片定义的调用通道，实现 ``lib.backends.image_backends.base.ImageBackend`` 协议。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        definition: Mapping[str, Any],
        provider: str,
    ) -> None:
        self._engine = DeclarativeJobEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            definition=definition,
            provider=provider,
            read_state=extract_image_state,
            log_label="声明式图片请求",
        )
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
    def capabilities(self) -> set[ImageCapability]:
        return set(image_capabilities_from_definition(self._definition))

    @property
    def max_reference_images(self) -> int:
        return 0

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        context = self._engine.request_context(
            {
                "prompt": request.prompt,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.image_size,
                "seed": request.seed,
            },
            {},
            require_declared_inputs=True,
        )
        call = JobCall(
            poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
            on_provider_response=request.on_provider_response,
            label=None,
        )
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._engine.submit(client, context, call)
            outcome = await self._engine.poll(client, job_id, call, context=context, is_resume=False)
            final = outcome.result_state or outcome.poll_state
            if not final.image_url:
                raise DeclarativeRuntimeError(
                    "declarative_response_extract_failed",
                    detail=final.error or "provider reported success but no image URL matched the definition",
                )
            await self._engine.download(
                client,
                final.image_url,
                request.output_path,
                context,
                max_bytes=IMAGE_ARTIFACT_MAX_BYTES,
                max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
                trusted_origins=outcome.trusted_origins,
            )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            image_uri=final.image_url,
            seed=request.seed,
        )
