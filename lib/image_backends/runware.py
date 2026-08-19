"""RunwareImageBackend — Runware 聚合推理平台图像生成后端（imageInference 原生协议）。

走 Runware 的单一端点 `POST https://api.runware.ai/v1`，请求体为 JSON 数组（每元素一个
task）。T2I 直接 model + positivePrompt + width/height；I2I 先把参考图 mediaStorage 上传
得 mediaUUID，再以 seedImage + strength 下发。同步返回 data[].imageURL，无需轮询。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

import httpx

from lib.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, parse_aspect_ratio, resolution_to_short_edge
from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
    image_to_base64_data_uri,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_RUNWARE
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.runware_shared import resolve_runware_api_key, runware_base_url, runware_headers
from lib.video_backends.base import should_retry_download, should_retry_submit, submit_post

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "google:nano-banana@2-lite"

# Nano Banana（google:*）只支持固定比例档位（执行期白名单，见 Runware imageInference
# 报错 allowedValues）。比例键为约简后的 "宽:高"。
_NANO_BANANA_DIMENSIONS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "3:2": (1264, 848),
    "2:3": (848, 1264),
    "4:3": (1200, 896),
    "3:4": (896, 1200),
    "4:5": (928, 1152),
    "5:4": (1152, 928),
    "9:16": (768, 1376),
    "16:9": (1376, 768),
    "7:3": (1584, 672),  # Runware 原生档位 21:9
    "4:1": (2048, 512),
    "1:4": (512, 2048),
    "8:1": (3072, 384),
    "1:8": (384, 3072),
}

# GPT Image 等任意尺寸模型：宽高均被 16 整除（gpt-image 要求）、总像素预算 8294400。
_GPT_IMAGE_ROUND_TO = 16
_GPT_IMAGE_MAX_TOTAL_PIXELS = 8294400
_DEFAULT_SHORT = 1440

# I2I 转换强度（官方示例 0.9 = 强转换，保留基本构图）。
_I2I_STRENGTH = 0.9

# 仅允许进日志的标量字段白名单；prompt 仅记长度。
_SAFE_LOG_KEYS = ("model", "width", "height", "numberResults", "strength")


def _extract_first_image_url(payload: dict) -> str | None:
    """从 Runware 响应 data[].imageURL 取首个非空字符串；无则 None。"""
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                url = item.get("imageURL")
                if isinstance(url, str) and url:
                    return url
    return None


def _extract_seed(payload: dict) -> int | None:
    """从响应 data[] 取首个整型 seed；无则 None。"""
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("seed"), int):
                return item["seed"]
    return None


def _safe_body_for_log(tasks: list[dict]) -> list[dict]:
    """安全日志视图：白名单标量 + prompt 仅长度。"""
    view: list[dict] = []
    for task in tasks:
        entry = {key: task[key] for key in _SAFE_LOG_KEYS if key in task}
        prompt = task.get("positivePrompt")
        if isinstance(prompt, str):
            entry["prompt_len"] = len(prompt)
        view.append(entry)
    return view


class RunwareImageBackend:
    """Runware 图像后端（单步同步 imageInference 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_runware_api_key(api_key)
        self._base_url = runware_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_RUNWARE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        width, height = self._resolve_dimensions(request)

        seed_image_uuid: str | None = None
        if request.reference_images:
            seed_image_uuid = await self._upload_reference_image(request.reference_images[0])

        data = await self._submit(request.prompt, width, height, seed_image_uuid, request.seed)
        image_uri = await self._persist_image(data, request.output_path)
        logger.info("Runware 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_RUNWARE,
            model=self._model,
            image_uri=image_uri,
            seed=_extract_seed(data),
        )

    @with_retry_async(retry_if=should_retry_submit)
    async def _submit(
        self,
        prompt: str,
        width: int,
        height: int,
        seed_image_uuid: str | None,
        seed: int | None,
    ) -> dict:
        """imageInference POST（非幂等「建图 + 计费」），返回解析后的响应体。

        重试范围严格限定在本方法内、不含下载——下载失败不会触发整流程重试导致重复建图与
        重复计费。submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败避免重复计费。
        """
        task: dict = {
            "taskType": "imageInference",
            "taskUUID": str(uuid.uuid4()),
            "model": self._model,
            "positivePrompt": prompt,
            "width": width,
            "height": height,
            "numberResults": 1,
        }
        if seed_image_uuid:
            task["seedImage"] = seed_image_uuid
            task["strength"] = _I2I_STRENGTH
        if seed is not None:
            task["seed"] = seed

        body = [task]
        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(body)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(self._base_url, json=body, headers=runware_headers(self._api_key)),
                provider=PROVIDER_RUNWARE,
            )
            return resp.json()

    async def _upload_reference_image(self, ref) -> str:
        """把本地参考图 mediaStorage 上传，返回 mediaUUID（I2I 的 seedImage 输入）。"""
        path = Path(ref.path) if getattr(ref, "path", None) else None
        if path is None or not path.is_file():
            raise ImageCapabilityError(
                "image_reference_images_unreadable",
                model=self._model,
                names=path.name if path and path.name else "#1",
            )
        try:
            data_uri = await asyncio.to_thread(image_to_base64_data_uri, path)
        except OSError as exc:
            logger.warning("Runware 参考图读取失败: %s (%s)", path, exc)
            raise ImageCapabilityError("image_reference_images_unreadable", model=self._model, names=path.name) from exc

        task = {
            "taskType": "mediaStorage",
            "taskUUID": str(uuid.uuid4()),
            "operation": "upload",
            "media": data_uri,
        }
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await submit_post(
                lambda: client.post(self._base_url, json=[task], headers=runware_headers(self._api_key)),
                provider=PROVIDER_RUNWARE,
            )
            data = resp.json()

        data_items = data.get("data")
        if isinstance(data_items, list):
            for item in data_items:
                if isinstance(item, dict) and isinstance(item.get("mediaUUID"), str):
                    return item["mediaUUID"]
        raise RuntimeError("Runware mediaStorage 上传响应缺少 mediaUUID")

    def _resolve_dimensions(self, request: ImageGenerationRequest) -> tuple[int, int]:
        """按模型算出 (宽, 高)。

        Nano Banana（google:*）只支持固定比例档位，按约简比例查表；比例不在档位表时回退 1:1。
        GPT Image 等任意尺寸模型走 aspect_size（16 整除 + 总像素预算）。
        """
        if self._model.startswith("google:"):
            aw, ah = parse_aspect_ratio(request.aspect_ratio)
            key = f"{aw}:{ah}"
            if key in _NANO_BANANA_DIMENSIONS:
                return _NANO_BANANA_DIMENSIONS[key]
            logger.warning("Runware nano-banana 无 %s 档位，回退 1:1", key)
            return _NANO_BANANA_DIMENSIONS["1:1"]
        short = resolution_to_short_edge(
            request.image_size or None, tier_map=IMAGE_TIER_SHORT_EDGE, default_short=_DEFAULT_SHORT
        )
        return aspect_size(
            request.aspect_ratio,
            short,
            round_to=_GPT_IMAGE_ROUND_TO,
            max_total_pixels=_GPT_IMAGE_MAX_TOTAL_PIXELS,
        )

    async def _persist_image(self, data: dict, output_path: Path) -> str | None:
        """把 imageInference 响应落地为本地文件，返回远端 URL。"""
        url = _extract_first_image_url(data)
        if not url:
            data_items = data.get("data")
            logger.error(
                "Runware 图像响应缺少 imageURL: keys=%s data_count=%s",
                sorted(str(key) for key in data),
                len(data_items) if isinstance(data_items, list) else None,
            )
            raise RuntimeError("Runware 图像响应缺少 imageURL")
        await self._download_result(url, output_path)
        return url

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_result(self, url: str, output_path: Path) -> None:
        """下载已签发的结果图 URL（幂等 GET），独立的下载重试范围。"""
        await download_image_to_path(url, output_path)
