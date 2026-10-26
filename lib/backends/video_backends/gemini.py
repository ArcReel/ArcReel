"""GeminiVideoBackend — 从 GeminiClient 提取的视频生成逻辑。"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Buffer, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image

from arcreel_catalog_core import normalize_model_name
from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.backend_runtime import (
    ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
    ProviderJobIdPersistenceMixin,
    faststart_video_artifact,
    is_retryable_http_status,
    poll_with_retry,
    should_retry_poll,
    with_artifact_retry,
)
from lib.backends.gemini_shared import (
    GEMINI_DEFAULT_VIDEO_MODEL,
    VERTEX_SCOPES,
    RateLimiter,
    gemini_http_options,
    get_shared_rate_limiter,
    resolve_gemini_api_key,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.transport_layer import TransportLayer
from lib.backends.video_backends.metering import video_usage
from lib.backends.video_backends.name_family import UNRECOGNIZED_MODEL_CAPABILITIES, capabilities_by_names
from lib.billing.metering import Usage
from lib.config.system_config import resolve_vertex_credentials_path
from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_noninterruptible_sync, run_sync_transaction
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

#: 成片下载单次网络读写的 I/O 超时，与 ``download_video`` 的缺省一致：连接或读取停滞超过它即抛错，交给重试预算。
_DOWNLOAD_IO_TIMEOUT_SECONDS = 120.0
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
#: 未配置 base_url 时 google-genai 发往的 AI Studio 域名（SDK 缺省值），续跑回放据此比对提交域名。
_AISTUDIO_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/"

# 官方声明音轨可控的 Veo 型号（即 Vertex 目录）：`_create_task` 只在 backend_type == "vertex"
# 时下发 generate_audio，AI Studio 的请求没有这个字段。只决定能力声明，计费跟随实际请求。两家目录的 model 命名不重叠，一致性由
# tests/unit/lib/backends/video_backends/test_video_backend_capabilities.py 的音轨立场守卫锁定（新增型号漏登记会在那里暴露）。
_AUDIO_SWITCH_MODELS: frozenset[str] = frozenset(
    {
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
    }
)


def _is_veo_3_1(name: str) -> bool:
    """名字属于 Veo 3.1 系列（含 fast / lite 与日期、preview 变体）；中转命名空间与点号写法一并认。"""
    return normalize_model_name(name).startswith("veo-3-1-")


class GeminiVideoBackend(ProviderJobIdPersistenceMixin):
    """Gemini (Veo) 视频生成后端。"""

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        video_model: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = backend_type.strip().lower()
        self._credentials = None
        self._project_id = None
        self._api_key: str | None = None
        self._base_url: str | None = None
        self._transport_layer = transport_layer
        self._http_options: _types.HttpOptions | _types.HttpOptionsDict | None = None

        self._video_model = video_model or GEMINI_DEFAULT_VIDEO_MODEL

        if self._backend_type == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            self._project_id = creds_data.get("project_id")

            self._credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._client = _genai.Client(
                vertexai=True,
                project=self._project_id,
                location="global",
                credentials=self._credentials,
            )
        else:
            self._api_key = resolve_gemini_api_key(api_key)
            self._base_url = normalize_base_url(base_url)
            self._http_options = gemini_http_options(self._base_url, transport_layer)
            self._client = _genai.Client(api_key=self._api_key, http_options=self._http_options)

    async def _close_injected_http_clients(self) -> None:
        # SDK 的 close/aclose 跳过外部传入的 httpx 客户端
        options = self._http_options
        if self._transport_layer is None or not isinstance(options, self._types.HttpOptions):
            return
        if options.httpx_async_client is not None:
            await options.httpx_async_client.aclose()
        if options.httpx_client is not None:
            options.httpx_client.close()

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._video_model

    @staticmethod
    def metered_usage(
        model: str, request: VideoGenerationRequest, result: VideoGenerationResult | None = None
    ) -> Usage:
        """这次请求的计量（见 :mod:`lib.backends.video_backends.metering`）。

        有声与否跟随实际请求：Vertex 按下发的 ``generate_audio``（含用户添加、放宽了音轨的型号）；AI Studio
        发不出静音请求，``generate_audio=False`` 在提交前即被拒绝（见 ``generate``），能提交的都是有声请求。
        提交前预估的请求由调用方按执行时会发出的开关构造（音轨不可控的型号按有声，见 ``VideoRequestCostFacts``）。
        结算时以 ``result.generate_audio`` 为准（见 ``_collect_video``）。
        """
        return video_usage(
            request,
            result,
            seconds=request.duration_seconds,
            resolution=request.resolution,
            audio=request.generate_audio,
        )

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按模型名纯计算 caps —— 不构造 SDK client（无需 api_key）。

        Veo 官方文档（docs/api-docs/providers/gemini-aistudio.md）把 Image-to-video 与 Reference images
        列为并列模式。Veo 3.1 系列的首帧/尾帧/参考图三维一致，按名字族认定；其余名字取 name_family 的
        统一回落。instance property 委托至此，保持 backend 为单一真相源。

        音轨形态按 model_id 分派：Veo 成片恒带音轨，只有 Vertex 的请求带 ``generate_audio`` 开关
        （见 ``_create_task``），AI Studio 的不带、开关无处可下发。registry 名 ``gemini`` 同时服务
        gemini-vertex 与 gemini-aistudio，静态查询只拿得到 model_id，故判据落在 model_id 上——两家
        目录的 model 命名不重叠，未登记的 Veo model 保守按「有音轨、无开关」声明。
        """
        audio_track = VideoAudioMode.CONTROLLABLE if model in _AUDIO_SWITCH_MODELS else VideoAudioMode.ALWAYS_ON

        def match(name: str) -> VideoCapabilities | None:
            if not _is_veo_3_1(name):
                return None
            return VideoCapabilities(last_frame=True, max_reference_images=3, audio_track=audio_track)

        return capabilities_by_names(
            model, match, fallback=replace(UNRECOGNIZED_MODEL_CAPABILITIES, audio_track=audio_track)
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._video_model)

    @property
    def _request_base_url(self) -> str | None:
        """AI Studio 请求实际发往的域名（未配置即 SDK 缺省域名）；Vertex 域名不随用户配置变化，为 None。"""
        if self._backend_type == "vertex":
            return None
        return self._base_url or _AISTUDIO_DEFAULT_BASE_URL

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """生成视频。任务创建和轮询阶段分离重试，避免瞬态错误导致重建任务。"""
        # 请求体没有参考音频字段；能力被用户覆盖放宽时也发不出，付费前拒绝而不是静默丢弃。
        if request.reference_audio_files:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._video_model)
        # AI Studio 请求体没有音轨开关，同理拒绝静音请求；续跑（resume_video）的任务已提交，不在此列。
        if not request.generate_audio and self._backend_type != "vertex":
            raise VideoCapabilityError("video_audio_switch_not_supported", provider=self.name, model=self._video_model)
        operation = await self._create_task(request)
        op_name = getattr(operation, "name", None)
        if not op_name:
            # fail-fast：缺 operation.name 意味着 submit 成功但无法持久化 provider_job_id，
            # 一旦进程中断，孤儿处理会走 [restart_lost] 回退到重新提交路径——这正是要避免的
            # 重复扣费场景。直接抛错让 worker finally 标 failed，比静默继续 poll 安全。
            raise RuntimeError("Gemini 提交成功但未返回 operation.name，无法持久化 provider_job_id")
        # 一并写回本次实际请求的域名：续跑据此回放，否则在途改了 base_url 会按新域名去查旧 operation
        await self._persist_provider_job_id(request, op_name, provider=PROVIDER_GEMINI, endpoint=self._request_base_url)
        done_operation = await self._poll_until_done(operation, request)
        return await self._collect_video(done_operation, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Gemini operation：用 name 重建 GenerateVideosOperation 走 poll + 下载。

        Operation 是 ABC，不可实例化；具体 LRO 子类 GenerateVideosOperation 通过
        Pydantic v2 ``model_validate`` 接收 dict 构造（pyright 对 Pydantic 多继承的
        字段推断不全，用 model_validate 绕开）。

        过期判定只覆盖按 job_id 查询 operation 的阶段：远端 GC 了 LRO 时，初次
        ``operations.get`` 与 mid-poll 的查询都会收到 NOT_FOUND，归类为 ResumeExpiredError
        让 worker finally 走 ``[resume_expired]``。operation 完成之后的取件失败（产物 URL 404 等）
        说明远端任务已成功、只是成片没取到，按取件失败原样上抛，不归过期。

        operation 只在提交它的域名上可查：AI Studio 续跑且提交域名与当下配置不同时，按同一份凭据
        另建绑定提交域名的实例接续（轮询与取件都走它），用完关闭其连接池。
        """
        submitted = normalize_base_url(request.submitted_base_url)
        if submitted and self._backend_type != "vertex" and submitted != self._request_base_url:
            replay = GeminiVideoBackend(
                backend_type=self._backend_type,
                api_key=self._api_key,
                rate_limiter=self._rate_limiter,
                video_model=self._video_model,
                base_url=submitted,
                transport_layer=self._transport_layer,
            )
            try:
                return await replay.resume_video(job_id, request)
            finally:
                await replay._client.aio.aclose()
                replay._client.close()
                await replay._close_injected_http_clients()
        op = self._types.GenerateVideosOperation.model_validate({"name": job_id, "done": False})
        try:
            refreshed = await self._client.aio.operations.get(op)
            done_operation = await self._poll_until_done(refreshed, request)
        except Exception as exc:
            if _is_gemini_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_GEMINI) from exc
            raise
        return await self._collect_video(done_operation, request)

    @with_retry_async()
    async def _create_task(self, request: VideoGenerationRequest) -> Any:
        """创建 Gemini 视频生成任务（带重试保护）。"""
        # 1. 限流
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._video_model)

        # 2. duration 透传（由模型 supported_durations 校验，不在 backend 做桶映射）
        duration_str = str(request.duration_seconds)

        # 3. 构建配置
        # 反向提示词不走参数通道——caller 在 request.prompt 末尾文本化注入
        # （lib.prompts.prompt_utils.render_storyboard_video_prompt 渲染的 storyboard/video 模版），跨 backend 一致。
        config_params: dict = {
            "aspect_ratio": request.aspect_ratio,
            "duration_seconds": duration_str,
        }
        if request.resolution is not None:
            config_params["resolution"] = request.resolution
        if self._backend_type == "vertex":
            config_params["generate_audio"] = request.generate_audio

        # end_image → last_frame（帧插值）
        if request.end_image is not None:
            config_params["last_frame"] = await asyncio.to_thread(self._prepare_image_param, request.end_image)

        # reference_images → reference_images（参考图列表，type=ASSET）
        if request.reference_images:
            prepared_refs = await asyncio.gather(
                *[asyncio.to_thread(self._prepare_image_param, img) for img in request.reference_images]
            )
            config_params["reference_images"] = [
                self._types.VideoGenerationReferenceImage(
                    image=prepared,
                    reference_type=self._types.VideoGenerationReferenceType.ASSET,
                )
                for prepared in prepared_refs
            ]

        config = self._types.GenerateVideosConfig(**config_params)

        # 4. 准备 source（prompt + 可选起始帧）
        image_param = (
            await asyncio.to_thread(self._prepare_image_param, request.start_image) if request.start_image else None
        )
        source = self._types.GenerateVideosSource(prompt=request.prompt, image=image_param)

        # 5. 调用 API
        logger.info(
            "调用 %s 视频 SDK payload=%s",
            self.name,
            format_kwargs_for_log(
                {
                    "model": self._video_model,
                    "prompt": request.prompt,
                    "config": config_params,
                    "has_start_image": request.start_image is not None,
                    "reference_image_count": len(request.reference_images) if request.reference_images else 0,
                }
            ),
        )
        operation = await self._client.aio.models.generate_videos(model=self._video_model, source=source, config=config)
        op_name = getattr(operation, "name", "unknown")
        logger.info("视频生成已提交, operation=%s", op_name)
        return operation

    async def _poll_until_done(self, operation: Any, request: VideoGenerationRequest) -> Any:
        """轮询任务状态直到完成并返回完成态 operation，瞬态错误仅重试当次轮询请求。"""
        op_name = getattr(operation, "name", "unknown")
        logger.info("开始轮询 operation=%s ...", op_name)

        if not operation.done:
            operation = await poll_with_retry(
                # SDK 通过 operation.name 查询状态并返回新对象，闭包捕获初始 operation 即可
                poll_fn=lambda: self._client.aio.operations.get(operation),
                is_done=lambda op: op.done,
                is_failed=lambda op: None,  # Gemini 在轮询完成后检查失败
                max_wait=request.poll_timeout_seconds,
                label="Gemini",
                retry_if=_should_retry_gemini_poll,
                on_progress=lambda op, elapsed: logger.info(
                    "视频生成中... 已等待 %.0f 秒 (operation=%s)", elapsed, op_name
                ),
            )

        logger.info("视频生成完成 (operation=%s)", op_name)
        return operation

    async def _collect_video(self, operation: Any, request: VideoGenerationRequest) -> VideoGenerationResult:
        """校验完成态 operation 的产出，取件落盘并组装结果。"""
        op_name = getattr(operation, "name", "unknown")
        if not operation.response or not operation.response.generated_videos:
            error_detail = getattr(operation, "error", None)
            metadata = getattr(operation, "metadata", None)
            logger.error(
                "视频生成返回空结果: operation=%s, error=%s, metadata=%s",
                op_name,
                error_detail,
                metadata,
            )
            if error_detail:
                raise RuntimeError(f"视频生成失败: {error_detail}")
            raise RuntimeError("视频生成失败: API 返回空结果")

        # 提取并下载视频
        generated_video = operation.response.generated_videos[0]
        video_ref = generated_video.video
        video_uri = video_ref.uri if video_ref else None

        await run_noninterruptible_sync(request.output_path.parent.mkdir, parents=True, exist_ok=True)
        await self._download_video_with_retry(video_ref, request.output_path)
        await faststart_video_artifact(request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._video_model,
            duration_seconds=request.duration_seconds,
            video_uri=video_uri,
            generate_audio=request.generate_audio if self._backend_type == "vertex" else True,
        )

    # ------------------------------------------------------------------
    # 内部辅助方法（从 GeminiClient 提取）
    # ------------------------------------------------------------------

    def _prepare_image_param(self, image: str | Path | Image.Image | None):
        """准备图片参数用于 API 调用 — 提取自 GeminiClient。"""
        if image is None:
            return None

        mime_type_png = "image/png"

        if isinstance(image, (str, Path)):
            with open(image, "rb") as f:
                image_bytes = f.read()
            suffix = Path(image).suffix.lower()
            mime_types = {
                ".png": mime_type_png,
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            mime_type = mime_types.get(suffix, mime_type_png)
            return self._types.Image(image_bytes=image_bytes, mime_type=mime_type)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        return self._types.Image(image_bytes=image_bytes, mime_type=mime_type_png)

    async def _download_video_with_retry(self, video_ref, output_path: Path) -> None:
        """下载视频，重试走共用的产物下载预算。

        SDK 取件抛的不是 ``HTTPStatusError``，故退到按 ``retryable_errors`` 判定。
        """
        if self._backend_type == "vertex" and video_ref and getattr(video_ref, "video_bytes", None):
            # 已有字节只需落盘，不进入网络取件的超时与重试预算。
            await run_noninterruptible_sync(
                _write_atomically, output_path, lambda target: target.write_bytes(video_ref.video_bytes)
            )
            return
        # 线程里写 .part 再替换成片：取消须等这次写盘结束、结果确定后再传播，否则调用方清理后线程才落盘。
        # 外层 asyncio.timeout 结束不了线程，线程按同一份预算的墙钟期限自行收尾。
        deadline = time.monotonic() + ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS
        await with_artifact_retry(
            lambda: run_sync_transaction(self._download_video, video_ref, output_path, deadline=deadline),
            label="Gemini",
            retry_if=None,
            max_wait=ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS,
        )

    def _download_video(self, video_ref, output_path: Path, *, deadline: float) -> None:
        """下载视频到本地文件 — 提取自 GeminiClient。

        三条落盘路径都经 ``_write_atomically``：先写同目录 ``.part``，成功后原子改名，
        失败时 output_path 上不会出现被当作成品的残片（已有的旧成片也保持原样）。
        两条网络路径都设 I/O 超时并逐块比对 ``deadline``（``time.monotonic()`` 时刻），
        到期抛 ``TimeoutError``，线程不会比取件预算活得更久。
        """
        if self._backend_type == "vertex":
            if video_ref and hasattr(video_ref, "video_bytes") and video_ref.video_bytes:
                video_bytes = video_ref.video_bytes
                _write_atomically(output_path, lambda target: target.write_bytes(video_bytes))
            elif video_ref and hasattr(video_ref, "uri") and video_ref.uri:
                uri = video_ref.uri
                _write_atomically(output_path, lambda target: _fetch_uri(uri, target, deadline))
            else:
                raise RuntimeError("视频生成成功但无法获取视频数据")
        else:
            # AI Studio 模式：files.download 带 destination 时分块直写，不把整段视频读进内存。
            def write(target: Path) -> None:
                timeout_ms = int(_io_timeout(deadline) * 1000)
                with open(target, "wb") as handle:
                    self._client.files.download(
                        file=video_ref,
                        destination=_DeadlineWriter(handle, deadline),
                        config={"http_options": {"timeout": timeout_ms}},
                    )

            _write_atomically(output_path, write)


class _DeadlineWriter(io.RawIOBase):
    """写入前比对墙钟期限的文件包装，期限已过即抛 ``TimeoutError``。

    I/O 超时只约束单次读取停滞，持续慢速出数据的下载由逐块比对期限结束。
    继承 ``RawIOBase`` 以满足 SDK ``destination`` 的 ``IOBase`` 类型。
    """

    def __init__(self, handle: BinaryIO, deadline: float) -> None:
        super().__init__()
        self._handle = handle
        self._deadline = deadline
        self.written = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Buffer, /) -> int:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("Gemini 成片下载超出取件预算")
        written = self._handle.write(b)
        self.written += written
        return written


def _io_timeout(deadline: float) -> float:
    """单次网络读写的超时：不超过 I/O 超时上限，也不越过期限；期限已过则直接抛 ``TimeoutError``。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Gemini 成片下载超出取件预算")
    return min(_DOWNLOAD_IO_TIMEOUT_SECONDS, remaining)


def _fetch_uri(uri: str, target: Path, deadline: float) -> None:
    """带 I/O 超时与期限检查，流式下载 ``uri`` 到 ``target``。

    HTTP 错误由 ``urlopen`` 抛 ``HTTPError``；正文短于 Content-Length 时抛
    ``ContentTooShortError``，截断文件不会被当成成片。
    """
    with urllib.request.urlopen(uri, timeout=_io_timeout(deadline)) as response, open(target, "wb") as handle:
        sink = _DeadlineWriter(handle, deadline)
        shutil.copyfileobj(response, sink, _DOWNLOAD_CHUNK_BYTES)
        expected = response.headers.get("Content-Length")
        if expected is not None and sink.written < int(expected):
            raise urllib.error.ContentTooShortError(
                f"retrieval incomplete: got only {sink.written} out of {expected} bytes",
                (str(target), response.headers),
            )


def _write_atomically(output_path: Path, write: Callable[[Path], object]) -> None:
    """让 ``write`` 把内容写进同目录 ``<name>.part``，成功后 ``os.replace`` 改名为 output_path。

    各写入方都以 "wb" 直开目标路径，中途失败会留下截断文件；写 ``.part``
    可保证失败时 output_path 不被覆盖，``BaseException`` 时清掉残片。
    """
    partial_path = output_path.with_name(f"{output_path.name}.part")
    try:
        write(partial_path)
        os.replace(partial_path, output_path)
    except BaseException:
        partial_path.unlink(missing_ok=True)  # hard-delete: temp -- 原子写的 .part 文件
        raise


def _should_retry_gemini_poll(exc: Exception) -> bool:
    """Gemini 轮询重试谓词：SDK ``APIError`` 按 HTTP 状态码判定，其余异常按幂等轮询口径。

    ``str(APIError)`` 带整份响应体，operation 名里的数字串可能含 "500" / "429"，按子串兜底会把
    404 当成瞬态错误重试到预算耗尽，续跑便认不出 operation 已被回收。
    """
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.APIError):
        return is_retryable_http_status(exc.code)
    return should_retry_poll(exc)


def _is_gemini_not_found(exc: BaseException) -> bool:
    """识别 Gemini operations.get 「operation 不存在 / 已过期」响应。

    只认 SDK ``APIError`` 携带的 HTTP 状态码 404：异常文本里的 "not found" / "expired"
    可能来自与 operation 存续无关的环节（传输层报错、上游失败原因），按子串判定会把它们
    误归过期。INVALID_ARGUMENT（400）同样不归过期：Gemini 用它表达入参不合法（如非法
    operation 格式），归过期会把客户端 bug 当成幽灵任务静默吞掉。
    """
    from google.genai import errors as genai_errors

    return isinstance(exc, genai_errors.APIError) and exc.code == 404
