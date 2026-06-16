"""KlingVideoBackend — 可灵 Kling 视频生成后端（JWT 直连 / Bearer 中转双模式，异步轮询）。

走可灵原生视频端点：submit ``POST /v1/videos/{text2video|image2video}`` 取 ``data.task_id`` →
轮询 ``GET /v1/videos/{subpath}/{task_id}`` 至 ``task_status=succeed`` 取
``task_result.videos[0].url`` → 下载本地。复用 base.py 的 submit/poll/download helpers，
自包含异步状态机、不依赖 DashScope async 机制。

双模式（对齐 ``GeminiVideoBackend`` 的 ``backend_type`` 先例）：
- ``auth_mode="jwt"``（内置 provider）：接 access_key + secret_key，走 ``KlingJWTManager``，
  每次 HTTP 调用前检查过期、距过期 <60s 按需重签——异步渲染可能超单 token 寿命。
- ``auth_mode="bearer"``（自定义 endpoint）：接静态 api_key + base_url，旁路 JWT 管理器。

本片只接默认视频模型 ``kling-v2-5-turbo``（std/pro，5s/10s，文生 + 图生视频含首尾帧）；
其余模型（v3/v3-omni/v2-6/o1）与能力门控由后续片接入。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.config.url_utils import normalize_base_url
from lib.kling_shared import (
    KLING_BASE_URL,
    KlingJWTManager,
    extract_kling_task_id,
    extract_kling_video_url,
    image_to_base64,
    is_kling_task_terminal,
    kling_bearer_headers,
    kling_task_failure_reason,
    kling_task_status,
    resolve_kling_api_key,
    resolve_kling_jwt_credentials,
)
from lib.providers import PROVIDER_KLING
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    persist_provider_job_id,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kling-v2-5-turbo"

_TEXT2VIDEO = "text2video"
_IMAGE2VIDEO = "image2video"
_RESUMABLE_SUBPATHS = frozenset({_TEXT2VIDEO, _IMAGE2VIDEO})

_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0
_KLING_VIDEO_POLL_INTERVAL_SECONDS = 10.0


def _encode_job_id(subpath: str, task_id: str) -> str:
    """把生成类型子路径编进持久化 job_id（``subpath:task_id``）。

    可灵查询端点按生成类型分路径（``GET /v1/videos/{text2video|image2video}/{id}``），
    且重启 resume 时请求已无 ``start_image`` 可推断子路径——必须把子路径随 task_id 一起
    持久化，否则 image2video 任务 resume 会误查 text2video 端点取不到任务。
    """
    return f"{subpath}:{task_id}"


def _decode_job_id(job_id: str) -> tuple[str, str]:
    """从持久化 job_id 复原 ``(子路径, task_id)``；无已知前缀（异常/旧数据）回落 text2video。"""
    prefix, sep, rest = job_id.partition(":")
    if sep and prefix in _RESUMABLE_SUBPATHS:
        return prefix, rest
    return _TEXT2VIDEO, job_id


class KlingVideoBackend:
    """可灵 Kling 视频后端（异步轮询，JWT / Bearer 双模式）。"""

    def __init__(
        self,
        *,
        auth_mode: str = "jwt",
        access_key: str | None = None,
        secret_key: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._auth_mode = auth_mode
        self._model = model or DEFAULT_MODEL
        self._base_url = (normalize_base_url(base_url) or KLING_BASE_URL).rstrip("/")
        self._http_timeout = http_timeout

        if auth_mode == "jwt":
            ak, sk = resolve_kling_jwt_credentials(access_key, secret_key)
            self._jwt: KlingJWTManager | None = KlingJWTManager(ak, sk)
            self._static_api_key: str | None = None
        elif auth_mode == "bearer":
            self._jwt = None
            self._static_api_key = resolve_kling_api_key(api_key)
        else:
            raise ValueError(f"未知 Kling auth_mode: {auth_mode}")

        # turbo：文生 + 图生视频（首尾帧 pro），无参考图/音频。
        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    @property
    def name(self) -> str:
        return PROVIDER_KLING

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        # turbo 支持首尾帧（pro），不建模参考图（多图主体留 v3-omni/o1）。
        return VideoCapabilities(first_frame=True, last_frame=True)

    # ── auth ────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """鉴权头：jwt 模式每次调用触发过期检查 + 按需重签；bearer 模式用静态 key。"""
        if self._jwt is not None:
            return self._jwt.auth_headers()
        assert self._static_api_key is not None
        return kling_bearer_headers(self._static_api_key)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> tuple[str, dict]:
        """返回 (子路径, 请求体)。无首帧 → text2video；有首帧 → image2video（含可选尾帧）。"""
        mode = "pro" if (request.service_tier or "").lower() == "pro" else "std"
        payload: dict = {
            "model_name": self._model,
            "prompt": request.prompt,
            "mode": mode,
            "duration": str(request.duration_seconds),
            "aspect_ratio": request.aspect_ratio,
        }

        has_start = isinstance(request.start_image, (str, Path)) and str(request.start_image)
        if not has_start:
            return _TEXT2VIDEO, payload

        payload["image"] = self._encode_frame(Path(request.start_image))  # type: ignore[arg-type]
        if request.end_image is not None:
            payload["image_tail"] = self._encode_frame(Path(request.end_image))
        return _IMAGE2VIDEO, payload

    def _encode_frame(self, path: Path) -> str:
        # fail-loud：声明了帧图却缺失/不可读即中止，不静默退化（会产出错误结果且照常计费）。
        if not path.is_file():
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name)
        try:
            return image_to_base64(path)
        except OSError as exc:
            raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=path.name) from exc

    @staticmethod
    def _safe_log_view(subpath: str, payload: dict) -> dict:
        """预脱敏标量视图，直接喂 logger（避开 format_kwargs_for_log sink）。

        base64 帧图 / prompt 一律不展开：仅记是否存在 + prompt 长度。
        """
        prompt = payload.get("prompt")
        return {
            "endpoint": subpath,
            "model_name": payload.get("model_name"),
            "mode": payload.get("mode"),
            "duration": payload.get("duration"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "has_image": "image" in payload,
            "has_image_tail": "image_tail" in payload,
            "prompt_len": len(prompt) if isinstance(prompt, str) else 0,
        }

    # ── generate / resume ───────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        subpath, payload = self._build_payload(request)
        logger.info("调用 Kling 视频 API payload=%s", self._safe_log_view(subpath, payload))
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, subpath, payload)
            logger.info("Kling 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            if request.task_id is not None:
                # 持久化「子路径:task_id」而非裸 task_id：resume 据此复原查询端点（见 _encode_job_id）。
                await persist_provider_job_id(
                    request.task_id, _encode_job_id(subpath, task_id), provider=PROVIDER_KLING
                )
            return await self._poll_and_build(client, subpath, task_id, request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Kling task：仅轮询 + 取 url + 下载，不重新提交（ADR 0007）。

        查询子路径从持久化 job_id 复原（submit 时编入）——可灵查询端点按生成类型分路径，
        而 resume 请求已无 ``start_image`` 可推断，故不能再从 request 取（见 _encode_job_id）。
        """
        subpath, task_id = _decode_job_id(job_id)
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, subpath, task_id, request)

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, subpath: str, payload: dict) -> str:
        # 非幂等「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败，
        # 避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}/videos/{subpath}",
                json=payload,
                headers=self._headers(),
            ),
            provider=PROVIDER_KLING,
        )
        return extract_kling_task_id(resp.json())

    async def _poll_query(self, client: httpx.AsyncClient, subpath: str, task_id: str) -> dict:
        resp = await client.get(
            f"{self._base_url}/videos/{subpath}/{task_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        subpath: str,
        task_id: str,
        request: VideoGenerationRequest,
    ) -> VideoGenerationResult:
        final = await poll_with_retry(
            poll_fn=lambda: self._poll_query(client, subpath, task_id),
            is_done=is_kling_task_terminal,
            is_failed=kling_task_failure_reason,
            poll_interval=_KLING_VIDEO_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="Kling",
            on_progress=lambda v, elapsed: logger.info(
                "Kling 视频生成中... status=%s elapsed=%ds",
                kling_task_status(v),
                int(elapsed),
            ),
        )

        download_url = extract_kling_video_url(final)
        await self._download_with_retry(download_url, request.output_path)
        logger.info("Kling 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_KLING,
            model=self._model,
            duration_seconds=request.duration_seconds,
            video_uri=download_url,
            task_id=task_id,
            # turbo 无音频能力：恒报无声，下游计费取无声价。
            generate_audio=False,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(download_url: str, output_path: Path) -> None:
        await download_video(download_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
