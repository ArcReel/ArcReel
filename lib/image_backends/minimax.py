"""MiniMaxImageBackend — MiniMax（海螺）image-01 图像生成后端（单步同步）。

走 OpenAI 兼容 base 上的原生 /image_generation 同步端点：单次 POST 直接返回图片 URL
（24h 有效）或 base64，立即落地为本地资产。T2I 与 I2I 共用同一请求体，I2I 经
subject_reference 单脸参考驱动多场景角色一致性立绘。尺寸用 width/height（512–2048、8 倍数），
按「比例优先、清晰度其次」从项目 aspect_ratio + image_size 精确算出。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from lib.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
    image_to_base64_data_uri,
)
from lib.logging_utils import format_kwargs_for_log
from lib.minimax_shared import (
    extract_image_base64,
    extract_image_url,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
    safe_body_for_log,
)
from lib.providers import PROVIDER_MINIMAX
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "image-01"

_IMAGE_ENDPOINT = "/image_generation"

# image-01 宽高均 ∈ [512, 2048] 且须被 8 整除；可表达的最极端比例为 2048/512 = 4:1。
_MIN_EDGE = 512
_MAX_EDGE = 2048
_ROUND_TO = 8
_MAX_RATIO = 4.0

# 缺 image_size 时的默认短边（2K 档），经 max_long_edge=2048 收口后竖屏得 1152*2048。
_DEFAULT_SHORT = 1440

# image-01 单脸参考：subject_reference 仅取首张参考图驱动角色一致性。
_SUBJECT_REF_LIMIT = 1


def _clamp_edge(value: int) -> int:
    """把单边像素夹进 [512, 2048]。两端点均为 8 的倍数，夹取后仍满足 8 整除约束。"""
    return max(_MIN_EDGE, min(_MAX_EDGE, value))


class MiniMaxImageBackend:
    """MiniMax 图像后端（单步同步 image_generation 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_minimax_api_key(api_key)
        self._base_url = minimax_text_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_MINIMAX

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        # image-01 文生图 + 图生图（subject_reference）同模型。
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # 编排层不带重试：把非幂等的「建图 + 计费」submit 与幂等的结果下载隔离到各自的
        # 重试范围（_submit / _download_result），避免下载失败回退到重跑生成 POST 造成重复计费。
        width, height = self._resolve_dimensions(request)

        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "width": width,
            "height": height,
            "response_format": "url",
            "n": 1,
            # ArcReel 剧本 prompt 已是 LLM 精炼描述，关闭智能改写保留原意。
            "prompt_optimizer": False,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.reference_images:
            # 参考图读盘 + base64 编码（可能数 MB）offload 到线程，避免阻塞事件循环；
            # 单次 to_thread 整体执行同步 helper，最小化线程调度开销。
            payload["subject_reference"] = await asyncio.to_thread(self._build_subject_reference, request)

        data = await self._submit(payload)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("MiniMax 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_MINIMAX,
            model=self._model,
            image_uri=image_uri,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """单步图像生成 POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含下载——下载失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 落 body 日志 + 抛 HTTPStatusError（保留 status_code 供咽喉层识别 413 降档），
        交 should_retry_submit 按状态码分流。
        """
        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=minimax_headers(self._api_key),
                ),
                provider=PROVIDER_MINIMAX,
            )
            return resp.json()

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """按「比例优先、清晰度其次」算出 (宽, 高)。

        比例永远来自 aspect_ratio；image_size（档位词 / 自定义 宽*高 / None）只决定清晰度短边，
        自定义值剥离其自带比例（取 min）。短边先夹到 ≥ _MIN_EDGE 再算尺寸——否则过小的短边会让
        aspect_size 产出 <512 的边，随后 _clamp_edge 对宽高独立夹取会破坏比例（如 16:9 退化成
        512x512 的 1:1）。结果被 8 整除、长边受 2048 收口，每边仍经 _clamp_edge 夹进 [512, 2048] 兜底。
        """
        short = max(
            _MIN_EDGE,
            resolution_to_short_edge(
                request.image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
            ),
        )
        w, h = aspect_size(
            request.aspect_ratio, short, round_to=_ROUND_TO, max_long_edge=_MAX_EDGE, max_ratio=_MAX_RATIO
        )
        return _clamp_edge(w), _clamp_edge(h)

    def _build_subject_reference(self, request: ImageGenerationRequest) -> list[dict]:
        """构建 subject_reference（单脸参考）。

        image-01 仅取一张人脸参考；多张时截断为首张并告警。首张缺失 / 读取失败即 fail-loud，
        报 image_reference_images_unreadable，让用户感知有图未被使用而非静默丢弃后照常计费。
        """
        refs = request.reference_images
        if len(refs) > _SUBJECT_REF_LIMIT:
            logger.warning(
                "MiniMax subject_reference 仅支持 %d 张参考图，截断 %d 张取首张",
                _SUBJECT_REF_LIMIT,
                len(refs),
            )
        ref = refs[0]
        # 空路径无文件名可显示，用序号 #1 标识，避免中文占位漏进非中文报错模板。
        path = Path(ref.path) if ref.path else None
        if path is None or not path.is_file():
            raise ImageCapabilityError(
                "image_reference_images_unreadable", model=self._model, names=path.name if path else "#1"
            )
        try:
            image_file = image_to_base64_data_uri(path)
        except OSError as exc:
            logger.warning("MiniMax 参考图读取失败: %s (%s)", path, exc)
            raise ImageCapabilityError("image_reference_images_unreadable", model=self._model, names=path.name) from exc
        return [{"type": "character", "image_file": image_file}]

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """把 image_generation 响应落地为本地文件，返回远端 URL（base64 路径返回 None）。

        先查 base_resp 业务错误（200 + 非零 status_code），再优先 URL（立即下载，24h 失效前落地），
        URL 缺失降级 base64 解码写盘；两者皆空即报错。
        """
        reason = minimax_failure_reason(data)
        if reason:
            raise RuntimeError(reason)

        url = extract_image_url(data)
        if url:
            await self._download_result(url, output_path)
            return url

        b64 = extract_image_base64(data)
        if b64:
            await _write_base64_image(b64, output_path)
            return None

        # 完整响应体记日志便于诊断，但不嵌进异常消息——避免 body 里的 "503"/"timeout" 等子串
        # 被默认 _should_retry 误判为可重试（仓库已确立按状态码而非字符串判重试）。
        logger.error("MiniMax 图像响应缺少 image_urls/image_base64: %s", data)
        raise RuntimeError("MiniMax 图像响应缺少 image_urls/image_base64")

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_result(self, url: str, output_path: Path) -> None:
        """下载已签发的结果图 URL（幂等 GET），独立的下载重试范围。

        瞬态失败在本层重试，绝不回退到重跑非幂等的生成 POST；4xx（URL 失效等确定性错误）
        快速失败。下载比生成更宽容（失败不浪费生成额度），故用 DOWNLOAD_* 重试配置。
        """
        await download_image_to_path(url, output_path)


async def _write_base64_image(b64: str, output_path: Path) -> None:
    """解码 base64 图片并写盘（解码 + 写盘 offload 到线程，避免事件循环内做 CPU 密集解码）。

    容忍少数中转返回 data URI（``data:image/...;base64,<payload>``）：剥前缀后再解码。
    """
    payload = b64
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    def _decode_and_save() -> None:
        image_bytes = base64.b64decode(payload)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)

    await asyncio.to_thread(_decode_and_save)
