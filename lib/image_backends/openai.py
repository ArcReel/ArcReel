"""OpenAIImageBackend — OpenAI 图片生成后端。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    save_image_from_response_item,
)
from lib.openai_shared import (
    OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP,
)
from lib.openai_shared import (
    OPENAI_IMAGE_SIZE_MAP as _SIZE_MAP,
)
from lib.openai_shared import (
    OPENAI_RETRYABLE_ERRORS,
    create_openai_client,
)
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-2"
_MAX_REFERENCE_IMAGES = 16


def _resolve_openai_params(
    image_size: str | None,
    aspect_ratio: str,
) -> dict[str, str]:
    """根据 image_size 返回 {size, quality} 子集。

    - None → 空 dict（全不传，走 SDK 默认）
    - 标准 token → 查 _SIZE_MAP 得 size，_QUALITY_MAP 得 quality
    - 未知 token（例如 "1024x1024"）→ warning 后作为 size 透传，不传 quality
    """
    if image_size is None:
        return {}

    mapped_size = _SIZE_MAP.get((image_size, aspect_ratio))
    if mapped_size is not None:
        params: dict[str, str] = {"size": mapped_size}
        quality = _QUALITY_MAP.get(image_size)
        if quality:
            params["quality"] = quality
        return params

    logger.warning(
        "OpenAI image: 未知 image_size=%r (aspect=%r)，原样作为 size 透传",
        image_size,
        aspect_ratio,
    )
    return {"size": image_size}


class OpenAIImageBackend:
    """OpenAI 图片生成后端，支持 T2I 和 I2I。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if request.reference_images:
            return await self._generate_edit(request)
        return await self._generate_create(request)

    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        kwargs = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
        }
        kwargs.update(_resolve_openai_params(request.image_size, request.aspect_ratio))
        response = await self._client.images.generate(**kwargs)
        return await self._save_and_return(response, request)

    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images
        if len(refs) > _MAX_REFERENCE_IMAGES:
            logger.warning("参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
            refs = refs[:_MAX_REFERENCE_IMAGES]

        def _open_refs() -> tuple[ExitStack, list]:
            """在 ExitStack 内打开所有参考图，保证部分 open 失败时已打开句柄被释放。"""
            stack = ExitStack()
            try:
                files = []
                for ref in refs:
                    ref_path = Path(ref.path)
                    try:
                        files.append(stack.enter_context(open(ref_path, "rb")))
                    except FileNotFoundError:
                        logger.warning("参考图不存在，跳过: %s", ref_path)
                # 把已打开的句柄所有权移交给调用者
                return stack.pop_all(), files
            except BaseException:
                stack.close()
                raise

        stack, image_files = await asyncio.to_thread(_open_refs)
        try:
            if not image_files:
                logger.warning("所有参考图均无效，回退到 T2I")
                return await self._generate_create(request)
            response = await self._client.images.edit(
                model=self._model,
                image=image_files,
                prompt=request.prompt,
            )
        finally:
            stack.close()
        return await self._save_and_return(response, request)

    async def _save_and_return(self, response, request: ImageGenerationRequest) -> ImageGenerationResult:
        await save_image_from_response_item(response.data[0], request.output_path)
        logger.info("OpenAI 图片生成完成: %s", request.output_path)
        quality = _QUALITY_MAP.get(request.image_size) if request.image_size else None

        img_in = img_out = txt_in = txt_out = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                in_details = getattr(usage, "input_tokens_details", None)
                # 必须拿到 input 拆分（image_tokens / text_tokens 至少一项有值），否则保留 None
                # 让 cost_calculator 走静态 fallback，避免部分字段缺失场景下漏算 input 费用
                in_image = getattr(in_details, "image_tokens", None) if in_details is not None else None
                in_text = getattr(in_details, "text_tokens", None) if in_details is not None else None
                if in_image is not None or in_text is not None:
                    img_in = in_image
                    txt_in = in_text
                    out_details = getattr(usage, "output_tokens_details", None)
                    if out_details is not None:
                        img_out = getattr(out_details, "image_tokens", None)
                        txt_out = getattr(out_details, "text_tokens", None)
                    if img_out is None:
                        # 部分模型只在顶层暴露 output_tokens（GPT Image 输出基本为 image token）
                        img_out = getattr(usage, "output_tokens", None)
                    # 输入拆分到手但输出完全拿不到 → 数据残缺，撤回让上层走静态 fallback
                    if img_out is None and txt_out is None:
                        img_in = txt_in = None
            except Exception:
                logger.warning("OpenAI image usage 解析失败", exc_info=True)
                img_in = img_out = txt_in = txt_out = None

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            quality=quality,
            image_input_tokens=img_in,
            image_output_tokens=img_out,
            text_input_tokens=txt_in,
            text_output_tokens=txt_out,
        )
