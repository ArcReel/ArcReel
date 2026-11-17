"""Gemini Interactions API 的视频调用通道（Gemini Omni）。

与 Veo 的 ``generate_videos`` 长任务是两套协议：提交 ``interactions.create(background=True)`` 拿到 interaction ID，
按 ID 轮询 ``interactions.get`` 到终态，成片以 base64 内联在最后一个 ``model_output`` 步骤里。AI Studio 与 Vertex 共用
同一组 SDK 调用，差别只在客户端的凭证与域名（见 :class:`GeminiVideoBackend` 的构造）。官方文档入口见
``docs/api-docs/providers/gemini-aistudio.md`` 与 ``gemini-vertex.md``。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, ClassVar

import httpx

from arcreel_catalog_core import CatalogEntry
from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import (
    faststart_video_artifact,
    is_retryable_http_status,
    poll_with_retry,
    should_retry_poll,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.video_backends.gemini import GeminiVideoBackend, write_atomically
from lib.backends.video_backends.metering import video_usage
from lib.billing.metering import Usage
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)

#: 仍在生成的状态；其余状态都是终态，``completed`` 之外的终态按失败处理。
_RUNNING_STATUSES = frozenset({"in_progress", "queued"})

#: 每秒成片的视频 token 数，按分辨率档位（Gemini API 定价页）；没发分辨率时按服务端缺省 720p 估算。
_VIDEO_TOKENS_PER_SECOND = {"360p": 1931, "720p": 5792, "1080p": 8688, "4k": 17376}
_DEFAULT_RESOLUTION = "720p"

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class GeminiInteractionsVideoBackend(GeminiVideoBackend):
    """经 Interactions API 调用 Gemini Omni 生成视频（AI Studio 凭证）。

    成片恒有声：请求里没有音轨开关，只能用提示词描述想要的声音。输入图片不带角色标记，按 ``video_config.task``
    与提示词区分首尾帧和参考图，所以首尾帧与参考图不能在同一个请求里混用，也发不出单独的尾帧。
    """

    audio_track: ClassVar[VideoAudioMode] = VideoAudioMode.ALWAYS_ON

    @classmethod
    def video_capabilities_for_model(cls, model: str, entry: CatalogEntry | None = None) -> VideoCapabilities:
        """通道实现文生视频、首帧、尾帧与参考图四条路径；参考图张数是模型事实，由条目给出。"""
        del model, entry
        return VideoCapabilities(last_frame=True, max_reference_images=None, audio_track=cls.audio_track)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._video_model)

    @staticmethod
    def estimate_video_tokens(*, resolution: str | None, seconds: int) -> int | None:
        """按分辨率档位的每秒 token 数换算成片的视频 token；不认识的档位算不出，为 ``None``。"""
        rate = _VIDEO_TOKENS_PER_SECOND.get((resolution or _DEFAULT_RESOLUTION).lower())
        return None if rate is None else rate * seconds

    @staticmethod
    def metered_usage(
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None = None,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`），与型号名和条目无关。

        视频 token 结算取供应商回报的输出视频 token（``result.usage_tokens``），预估按分辨率档位的每秒 token 数换算。
        成片恒有声，音轨维度固定为有声。
        """
        del model, entry
        tokens = (
            result.usage_tokens
            if result is not None
            else GeminiInteractionsVideoBackend.estimate_video_tokens(
                resolution=request.resolution, seconds=request.duration_seconds
            )
        )
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=True,
            video_tokens=tokens,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """提交 interaction 后轮询到终态并取件；提交失败的重试由 SDK 按状态码完成。"""
        self._reject_unsendable(request)
        interaction = await self._create_interaction(request)
        interaction_id = getattr(interaction, "id", None)
        if not interaction_id:
            # 没有 ID 就无法持久化 provider_job_id，进程中断后会走重新提交，造成重复扣费。
            raise RuntimeError("Gemini Interactions 提交成功但未返回 interaction ID，无法持久化 provider_job_id")
        await self._persist_provider_job_id(
            request, interaction_id, provider=PROVIDER_GEMINI, endpoint=self._request_base_url
        )
        done = await self._poll_interaction(interaction, request)
        return await self._collect_interaction(done, request)

    async def _resume_on_current_client(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """按 interaction ID 轮询到终态并取件，不重新提交。

        查询 interaction 收到 404 时归为过期（服务端按保留期删除了记录）；interaction 完成后的取件失败照原样上抛。
        """
        try:
            interaction = await self._get_interaction(job_id)
            done = await self._poll_interaction(interaction, request)
        except Exception as exc:
            if _interactions_status_code(exc) == 404:
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_GEMINI) from exc
            raise
        return await self._collect_interaction(done, request)

    def _reject_unsendable(self, request: VideoGenerationRequest) -> None:
        """请求体表达不了的输入在付费前拒绝，不静默丢弃（能力被用户覆盖放宽时也会走到这里）。"""
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._video_model)
        if not request.generate_audio:
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._video_model)
        if request.reference_images and (request.start_image or request.end_image):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._video_model)
        if request.end_image and not request.start_image:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._video_model)

    async def _create_interaction(self, request: VideoGenerationRequest) -> Any:
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._video_model)

        if request.reference_images:
            images, task = list(request.reference_images), "reference_to_video"
        elif request.start_image:
            images = [request.start_image] + ([request.end_image] if request.end_image else [])
            task = "image_to_video"
        else:
            images, task = [], "text_to_video"
        parts: list[dict[str, Any]] = list(await asyncio.gather(*[asyncio.to_thread(_image_part, i) for i in images]))
        # 反向提示词由调用方文本化注入 prompt，跨通道一致；文本放在图片之后。
        parts.append({"type": "text", "text": request.prompt})

        response_format: dict[str, Any] = {
            "type": "video",
            "aspect_ratio": request.aspect_ratio,
            "duration": f"{request.duration_seconds}s",
        }
        if request.resolution is not None:
            response_format["resolution"] = request.resolution

        logger.info(
            "调用 %s Interactions 视频 payload=%s",
            self.name,
            format_kwargs_for_log(
                {
                    "model": self._video_model,
                    "prompt": request.prompt,
                    "task": task,
                    "response_format": response_format,
                    "image_count": len(images),
                }
            ),
        )
        await self._refresh_vertex_token()
        interaction = await self._client.aio.interactions.create(
            model=self._video_model,
            input=parts,
            response_format=response_format,
            generation_config={"video_config": {"task": task}},
            background=True,
        )
        logger.info("Interactions 视频已提交, interaction=%s", getattr(interaction, "id", "unknown"))
        return interaction

    async def _refresh_vertex_token(self) -> None:
        """Vertex 的访问令牌过期时在线程里刷新。

        Interactions 的异步客户端取令牌是同步调用，令牌过期时会在事件循环线程上发起 OAuth 刷新请求；
        先在线程里刷新好，SDK 取到的就是有效令牌。AI Studio 用 API Key，没有令牌。
        """
        credentials = self._credentials
        if credentials is None or credentials.valid:
            return
        from google.auth.transport.requests import Request

        await asyncio.to_thread(credentials.refresh, Request())

    async def _get_interaction(self, interaction_id: str) -> Any:
        await self._refresh_vertex_token()
        return await self._client.aio.interactions.get(interaction_id)

    async def _poll_interaction(self, interaction: Any, request: VideoGenerationRequest) -> Any:
        """轮询到终态；``completed`` 之外的终态抛 ``RuntimeError``，瞬态错误只重试当次查询。"""
        interaction_id = interaction.id
        if interaction.status in _RUNNING_STATUSES:
            interaction = await poll_with_retry(
                poll_fn=lambda: self._get_interaction(interaction_id),
                is_done=lambda it: it.status not in _RUNNING_STATUSES,
                is_failed=_failure_message,
                max_wait=request.poll_timeout_seconds,
                label="Gemini Interactions",
                retry_if=_should_retry_interactions_poll,
                on_progress=lambda _it, elapsed: logger.info(
                    "视频生成中... 已等待 %.0f 秒 (interaction=%s)", elapsed, interaction_id
                ),
            )
        failure = _failure_message(interaction)
        if failure is not None:
            raise RuntimeError(failure)
        return interaction

    async def _collect_interaction(self, interaction: Any, request: VideoGenerationRequest) -> VideoGenerationResult:
        video = interaction.output_video
        data = getattr(video, "data", None)
        if not data:
            logger.error("Interactions 视频完成但没有成片数据: interaction=%s", interaction.id)
            raise RuntimeError("视频生成失败: API 未返回视频数据")

        await run_noninterruptible_sync(request.output_path.parent.mkdir, parents=True, exist_ok=True)
        # 字节已在内存里：解码与写盘不可中断，落盘经 .part 再改名。
        await run_noninterruptible_sync(
            write_atomically, request.output_path, lambda target: target.write_bytes(base64.b64decode(data))
        )
        await faststart_video_artifact(request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            usage_tokens=_output_video_tokens(interaction),
            generate_audio=True,
        )


class VertexInteractionsVideoBackend(GeminiInteractionsVideoBackend):
    """以 Vertex 服务账号经 Interactions API 调用 Gemini Omni；成片同样恒有声。"""

    default_backend_type = "vertex"


def _image_part(path: Path) -> dict[str, Any]:
    image_bytes = Path(path).read_bytes()
    return {
        "type": "image",
        "data": base64.b64encode(image_bytes).decode("ascii"),
        "mime_type": _IMAGE_MIME_TYPES.get(Path(path).suffix.lower(), "image/png"),
    }


def _failure_message(interaction: Any) -> str | None:
    status = interaction.status
    if status in _RUNNING_STATUSES or status == "completed":
        return None
    errors = [f"{getattr(e, 'code', '')}: {getattr(e, 'message', '')}" for e in (interaction.errors or [])]
    detail = "; ".join(errors) if errors else "无错误详情"
    return f"视频生成失败: interaction {interaction.id} 状态为 {status}（{detail}）"


def _output_video_tokens(interaction: Any) -> int | None:
    """供应商回报的输出视频 token；没有按模态拆分时取输出 token 总数。"""
    usage = interaction.usage
    if usage is None:
        return None
    for item in usage.output_tokens_by_modality or ():
        if item.modality == "video" and item.tokens is not None:
            return item.tokens
    return usage.total_output_tokens


def _interactions_status_code(exc: BaseException) -> int | None:
    """Interactions SDK 错误携带的 HTTP 状态码。

    这组错误不是 ``google.genai.errors.APIError``：状态码在 ``status_code`` 上（Veo 路径是 ``code``），
    错误类也不在公开模块里，按属性读取。
    """
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _should_retry_interactions_poll(exc: Exception) -> bool:
    """有状态码的按状态码判定（404 不重试，续跑据此认出过期），其余按幂等轮询口径。

    SDK 把 httpx 的传输错误包成自己的连接错误类、原错误放在 ``__cause__``，按原错误判定。
    """
    status = _interactions_status_code(exc)
    if status is not None:
        return is_retryable_http_status(status)
    cause = exc.__cause__
    if isinstance(cause, httpx.HTTPError):
        return should_retry_poll(cause)
    return should_retry_poll(exc)
