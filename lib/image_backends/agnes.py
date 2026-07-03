"""AgnesImageBackend — Agnes 图像生成后端（单步同步，OpenAI 兼容）。

走 apihub 网关上的 OpenAI 兼容 ``/images/generations`` 同步端点：单次 POST 直接返回图片
URL 或 base64，立即落地为本地资产。T2I 与 I2I 共用同一端点，I2I 把参考图随请求体下发。
尺寸按 ADR 0011 aspect_size 精确算出并显式下发 ``size``（不依赖上游默认横屏尺寸）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

from lib.agnes_shared import agnes_base_url, agnes_headers, resolve_agnes_api_key
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
from lib.providers import PROVIDER_AGNES
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-image-2.1-flash"

_IMAGE_ENDPOINT = "/images/generations"

# 尺寸约束：长宽被 8 整除、长边收口 2048（保守值，覆盖常见 flash 图像档）。缺 image_size
# 时按 2K 短边兜底，经长边收口后竖屏得 1152x2048。
_ROUND_TO = 8
_MAX_LONG_EDGE = 2048
_DEFAULT_SHORT = 1440

# 仅允许进日志的标量字段白名单；prompt 仅记长度、image 仅计数，base64/URL 一律不入日志。
_SAFE_LOG_KEYS = ("model", "size", "n")


def _extract_first_str(payload: object, key: str) -> str | None:
    """从 OpenAI 兼容响应 ``data[].<key>`` 取首个非空字符串（url / b64_json 共用）；无则 None。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _safe_body_for_log(body: dict) -> dict:
    """生成安全日志视图：白名单标量 + prompt 仅长度 + image 仅计数。"""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    images = body.get("image")
    if isinstance(images, list) and images:
        view["image"] = f"<{len(images)} ref>"
    return view


class AgnesImageBackend:
    """Agnes 图像后端（单步同步 OpenAI 兼容 images/generations 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_agnes_api_key(api_key)
        self._base_url = agnes_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # 编排层不带重试：把非幂等的「建图 + 计费」submit 与幂等的结果下载隔离到各自的
        # 重试范围（_submit / _download_result），避免下载失败回退到重跑生成 POST 造成重复计费。
        width, height = self._resolve_dimensions(request)

        # 不发 response_format：上游 litellm 网关对该参数报 UnsupportedParamsError；
        # 响应默认同时带 url 与 b64_json，由 _persist_image 优先取 url。
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
            "size": f"{width}x{height}",
        }
        if request.reference_images:
            # I2I 参考图随同一请求体下发 data-URI 列表（image 字段）。读盘 + base64 编码
            # （可能数 MB）offload 到线程，避免阻塞事件循环。
            payload["image"] = await asyncio.to_thread(self._build_reference_images, request)

        data = await self._submit(payload)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("Agnes 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            image_uri=image_uri,
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(self, payload: dict) -> dict:
        """单步图像生成 POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含下载——下载失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费；
        >=400 落 body 日志 + 抛 HTTPStatusError，交 should_retry_submit 按状态码分流。
        """
        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(
                    f"{self._base_url}{_IMAGE_ENDPOINT}",
                    json=payload,
                    headers=agnes_headers(self._api_key),
                ),
                provider=PROVIDER_AGNES,
            )
            return resp.json()

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """按「比例优先、清晰度其次」算出 (宽, 高)。

        比例永远来自 aspect_ratio；image_size（档位词 / 自定义 宽*高 / None）只决定清晰度短边，
        自定义值剥离其自带比例（取 min）。结果被 8 整除、长边受 2048 收口。
        """
        short = resolution_to_short_edge(
            request.image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
        )
        return aspect_size(request.aspect_ratio, short, round_to=_ROUND_TO, max_long_edge=_MAX_LONG_EDGE)

    def _build_reference_images(self, request: ImageGenerationRequest) -> list[str]:
        """构建 I2I 参考图列表（data-URI base64）。

        任一参考图缺失 / 读取失败即 fail-loud，报 image_reference_images_unreadable，让用户
        感知有图未被使用而非静默丢弃后照常计费。
        """
        uris: list[str] = []
        for idx, ref in enumerate(request.reference_images, start=1):
            # 空路径无文件名可显示，用序号标识，避免中文占位漏进非中文报错模板。
            path = Path(ref.path) if ref.path else None
            if path is None or not path.is_file():
                raise ImageCapabilityError(
                    "image_reference_images_unreadable",
                    model=self._model,
                    names=path.name if path and path.name else f"#{idx}",
                )
            try:
                uris.append(image_to_base64_data_uri(path))
            except OSError as exc:
                logger.warning("Agnes 参考图读取失败: %s (%s)", path, exc)
                raise ImageCapabilityError(
                    "image_reference_images_unreadable", model=self._model, names=path.name
                ) from exc
        return uris

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """把 images/generations 响应落地为本地文件，返回远端 URL（base64 路径返回 None）。

        优先 URL（立即下载）；URL 缺失或下载失败时降级到同响应内 base64 解码写盘，
        避免一次已计费的成功生成因下载环节失败而无法落盘；两者皆空即报错。
        """
        url = _extract_first_str(data, "url")
        b64 = _extract_first_str(data, "b64_json")
        if url:
            try:
                await self._download_result(url, output_path)
                return url
            except Exception:
                if not b64:
                    raise
                logger.warning("Agnes 结果 URL 下载失败，改用响应内 b64_json 落盘")

        if b64:
            await _write_base64_image(b64, output_path)
            return None

        # 完整响应体可能含 prompt / 签名 URL 等敏感字段，与请求日志脱敏策略一致：只记键名与
        # data 条数，不落整 body；异常消息同样不嵌 body——避免 "503"/"timeout" 子串被默认
        # _should_retry 误判为可重试（仓库已确立按状态码而非字符串判重试）。
        data_items = data.get("data")
        logger.error(
            "Agnes 图像响应缺少 url/b64_json: keys=%s data_count=%s",
            sorted(str(key) for key in data),
            len(data_items) if isinstance(data_items, list) else None,
        )
        raise RuntimeError("Agnes 图像响应缺少 url/b64_json")

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_result(self, url: str, output_path: Path) -> None:
        """下载已签发的结果图 URL（幂等 GET），独立的下载重试范围。

        瞬态失败在本层重试，绝不回退到重跑非幂等的生成 POST；4xx 快速失败。
        """
        await download_image_to_path(url, output_path)


async def _write_base64_image(b64: str, output_path: Path) -> None:
    """解码 base64 图片并写盘（解码 + 写盘 offload 到线程）。

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
