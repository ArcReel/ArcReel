"""声明式图片定义的调用通道：在 :class:`DeclarativeJobEngine` 上组装图片的请求与产物语义。

与视频通道共用提交、轮询、状态映射、二次取件与产物下载；图片一侧只多出自己的模板变量、
能力声明与产物字段：产物可以是地址，也可以是响应体里内联的 base64。

图片没有续跑协议：服务重启时在途的图片任务由重启恢复记为重启丢失，所以这里不落供应商任务 id；
产物没能取回时也接不回原任务，只能重新生成。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

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

#: 供应商已出图、产物却没能取回或落盘时的失败码，恢复方式是重新生成。
IMAGE_SAVE_FAILED_CODE = "declarative_image_save_failed"

#: 图片定义 ``capabilities`` 节的字段 → 端点的图片能力。
_IMAGE_CAPABILITY_BY_FIELD: Mapping[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
    "image_to_image": ImageCapability.IMAGE_TO_IMAGE,
}


def image_capabilities_from_definition(definition: Mapping[str, Any]) -> frozenset[ImageCapability]:
    """图片定义显式声明的图片能力。端点投影与 backend 共读这一份，两处不会给出不同的能力。"""
    declared: Mapping[str, Any] = definition.get("capabilities") or {}
    return frozenset(
        capability for name, capability in _IMAGE_CAPABILITY_BY_FIELD.items() if declared.get(name) is True
    )


@dataclass(frozen=True)
class ImageJobState(JobState):
    """图片定义的判读结果：在 :class:`JobState` 之上加产物地址与内联的 base64 产物。"""

    image_url: str | None
    #: 按 ``image_b64`` 取到的原文，未解码。两者都取到时以 ``image_url`` 为准。
    image_b64: str | None


_DATA_URI_PREFIX = re.compile(r"^data:image/[\w.+-]+;base64,", re.IGNORECASE)


def decode_image_b64(text: str) -> bytes:
    """把 ``image_b64`` 取到的原文解成图片字节，裸 base64 与 ``data:image/...;base64,`` 都认。

    Raises:
        ValueError: 不是合法的 base64，解出来为空，或超出图片产物的体积上限。
    """
    payload = "".join(_DATA_URI_PREFIX.sub("", text.strip(), count=1).split())
    # 先按编码长度估算体积再解码：超限的串不值得花内存解出来。
    if len(payload) // 4 * 3 > IMAGE_ARTIFACT_MAX_BYTES:
        raise ValueError(f"inline image exceeds {IMAGE_ARTIFACT_MAX_BYTES} bytes")
    try:
        image = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=True)
    except binascii.Error as exc:
        raise ValueError("image_b64 is not valid base64") from exc
    if not image:
        raise ValueError("image_b64 decoded to an empty image")
    return image


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
            image_b64=extract_text(extract.get("image_b64"), body),
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
        """定义声明的参考图上限；未声明时为 ``0``。校验器保证声明了图生图就有正数上限。"""
        value = (self._definition.get("capabilities") or {}).get("max_reference_images")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # 编排层已按 max_reference_images 裁剪并提示；这里的截断只是兜底，超出的图没有落点。上限为 0
        # 时按协议不裁剪。
        references = request.reference_images
        if self.max_reference_images:
            references = references[: self.max_reference_images]
        context = self._engine.request_context(
            {
                "prompt": request.prompt,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.image_size,
                "seed": request.seed,
            },
            {"reference_images": [Path(ref.path) for ref in references]},
            require_declared_inputs=True,
        )
        call = JobCall(
            poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
            on_provider_response=request.on_provider_response,
            label=None,
        )
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._engine.submit(client, context, call)
            try:
                return await self._collect(client, job_id, call, context, request)
            except DeclarativeRuntimeError as exc:
                # 引擎把二次取件与下载耗尽记为可「重试下载」的码，那条恢复路径要靠续跑接回原任务；
                # 图片不落任务 id、没有续跑，只能重新生成。
                if exc.code != "artifact_download_failed":
                    raise
                raise DeclarativeRuntimeError(IMAGE_SAVE_FAILED_CODE, detail=str(exc)) from exc

    async def _collect(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        call: JobCall,
        context: Mapping[str, object],
        request: ImageGenerationRequest,
    ) -> ImageGenerationResult:
        outcome = await self._engine.poll(client, job_id, call, context=context, is_resume=False)
        final = outcome.result_state or outcome.poll_state
        if not final.image_url:
            await _write_inline_image(final, request.output_path)
            return self._result(request, image_uri=None)
        await self._engine.download(
            client,
            final.image_url,
            request.output_path,
            context,
            max_bytes=IMAGE_ARTIFACT_MAX_BYTES,
            max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
            trusted_origins=outcome.trusted_origins,
        )
        return self._result(request, image_uri=final.image_url)

    def _result(self, request: ImageGenerationRequest, *, image_uri: str | None) -> ImageGenerationResult:
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            image_uri=image_uri,
            seed=request.seed,
        )


async def _write_inline_image(state: ImageJobState, output_path: Path) -> None:
    """URL 没取到时落盘 base64 产物；两者都没有或解不出图片即判取件失败。"""
    if not state.image_b64:
        raise DeclarativeRuntimeError(
            "declarative_response_extract_failed",
            detail=state.error or "provider reported success but no image matched the definition",
        )
    encoded = state.image_b64

    def decode_and_save() -> None:
        image = decode_image_b64(encoded)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image)

    try:
        # 解码大图是 CPU 密集操作，放到线程里免得卡住事件循环。
        await asyncio.to_thread(decode_and_save)
    except ValueError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc
    except OSError as exc:
        raise DeclarativeRuntimeError(IMAGE_SAVE_FAILED_CODE, detail=str(exc)) from exc
