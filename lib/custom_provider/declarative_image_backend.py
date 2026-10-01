"""声明式图片定义的调用通道：在媒体无关运行时上组装图片的请求与产物语义。

与视频通道共用提交、轮询、状态映射、二次取件与产物下载；图片一侧只多出自己的模板变量、
能力声明与产物字段：产物可以是地址，也可以是响应体里内联的 base64。

图片没有续跑协议：服务重启时在途的图片任务由重启恢复记为重启丢失，所以这里不落供应商任务 id；
产物没能取回时也接不回原任务，只能重新生成。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from arcreel_market_core.endpoint_definition import (
    AssetData,
    JsonPathEvaluationError,
    TemplateRenderError,
    build_context,
    definition_media_type,
    encode_inputs,
)
from arcreel_market_core.job_contract import ProviderJobStatus, ProviderResponseStage
from arcreel_market_core.video_backend_contract import IMAGE_MIME_TYPES
from lib.backends.artifact_download_guard import IMAGE_ARTIFACT_MAX_BYTES, artifact_http_client
from lib.backends.backend_runtime import ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS, notify_provider_response_to
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.custom_provider.declarative_runtime import (
    DeclarativeRuntime,
    DeclarativeRuntimeError,
    ProviderRuntimeState,
    extract_runtime_state,
    extract_text,
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
class ImageRuntimeState:
    """图片定义的判读结果：媒体无关状态加产物地址与内联的 base64 产物。"""

    body: object
    status: ProviderJobStatus
    provider_status: str | None
    image_url: str | None
    #: 按 ``image_b64`` 取到的原文，未解码。两者都取到时以 ``image_url`` 为准。
    image_b64: str | None
    error: str | None
    result_id: str | None


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


def _image_state(state: ProviderRuntimeState, extract: Mapping[str, Any]) -> ImageRuntimeState:
    try:
        return ImageRuntimeState(
            body=state.body,
            status=state.status,
            provider_status=state.provider_status,
            image_url=extract_text(extract.get("image_url"), state.body),
            image_b64=extract_text(extract.get("image_b64"), state.body),
            error=state.error,
            result_id=state.result_id,
        )
    except JsonPathEvaluationError as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=exc.message) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise DeclarativeRuntimeError("declarative_response_extract_failed", detail=str(exc)) from exc


def extract_image_state(
    body: object,
    extract: Mapping[str, Any],
    *,
    status_map: Mapping[str, str] | None = None,
    status: ProviderJobStatus | None = None,
) -> ImageRuntimeState:
    """按图片定义的一节 ``extract`` 读一份响应体。运行时与验证响应共用的唯一判读实现。"""
    state = extract_runtime_state(body, extract, status_map=status_map, status=status)
    return _image_state(state, extract)


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
        self._runtime = DeclarativeRuntime(
            api_key=api_key,
            base_url=base_url,
            definition=definition,
            provider=provider,
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
        context = self._request_context(request, references)
        on_response = self._response_recorder(request)
        async with artifact_http_client(timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            job_id = await self._runtime.submit(client, context, on_response=on_response)
            try:
                return await self._collect(client, job_id, request, context=context, on_response=on_response)
            except DeclarativeRuntimeError as exc:
                # 运行时把二次取件与下载耗尽记为可「重试下载」的码，那条恢复路径要靠续跑接回原任务；
                # 图片不落任务 id、没有续跑，只能重新生成。
                if exc.code != "artifact_download_failed":
                    raise
                raise DeclarativeRuntimeError(IMAGE_SAVE_FAILED_CODE, detail=str(exc)) from exc

    def _request_context(self, request: ImageGenerationRequest, references: list[ReferenceImage]) -> dict[str, object]:
        declarations = self._definition.get("inputs") or {}
        try:
            assets = {"reference_images": [Path(ref.path) for ref in references]}
            loaded: dict[str, AssetData | list[AssetData] | None] = {
                "reference_images": self._assets(assets["reference_images"]),
            }
            encoded = encode_inputs(declarations, loaded)
            missing = [name for name, value in encoded.items() if declarations[name].get("required") and not value]
            if missing:
                raise DeclarativeRuntimeError(
                    "declarative_template_render_failed",
                    detail=f"required inputs are missing: {', '.join(sorted(missing))}",
                )
            return build_context(
                {
                    "api_key": self._runtime.api_key,
                    "base_url": self._runtime.base_url,
                    "model": self._model,
                    "prompt": request.prompt,
                    "aspect_ratio": request.aspect_ratio,
                    "resolution": request.image_size,
                    "seed": request.seed,
                },
                encoded,
                self._definition.get("defaults"),
                media_type=definition_media_type(self._definition),
            )
        except TemplateRenderError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=exc.message) from exc
        except OSError as exc:
            raise DeclarativeRuntimeError("declarative_template_render_failed", detail=str(exc)) from exc

    async def _collect(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        request: ImageGenerationRequest,
        *,
        context: Mapping[str, object],
        on_response: Callable[[ProviderResponseStage, object], Awaitable[None]],
    ) -> ImageGenerationResult:
        outcome = await self._runtime.poll_for_result(
            client,
            job_id,
            context=context,
            poll_timeout_seconds=IMAGE_POLL_TIMEOUT_SECONDS,
            is_resume=False,
            on_response=on_response,
        )
        poll_extract = self._definition["poll"]["extract"]
        poll_state = _image_state(outcome.poll_state, poll_extract)
        final_state = poll_state
        if outcome.final_stage == "result":
            final_state = _image_state(outcome.final_state, self._definition["result"]["extract"])
        if not final_state.image_url:
            await _write_inline_image(final_state, request.output_path)
            return self._result(request, image_uri=None)
        await self._runtime.download_artifact(
            client,
            final_state.image_url,
            request.output_path,
            context,
            max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
            trusted_origins=outcome.trusted_origins,
            max_bytes=IMAGE_ARTIFACT_MAX_BYTES,
            label=f"{self._provider} artifact download",
        )
        return self._result(request, image_uri=final_state.image_url)

    def _result(self, request: ImageGenerationRequest, *, image_uri: str | None) -> ImageGenerationResult:
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            image_uri=image_uri,
            seed=request.seed,
        )

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

    @staticmethod
    def _response_recorder(
        request: ImageGenerationRequest,
    ) -> Callable[[ProviderResponseStage, object], Awaitable[None]]:
        async def record(stage: ProviderResponseStage, body: object) -> None:
            await notify_provider_response_to(request.on_provider_response, stage, body)

        return record


async def _write_inline_image(state: ImageRuntimeState, output_path: Path) -> None:
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
