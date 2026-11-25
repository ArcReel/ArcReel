"""GeminiImageBackend — 从 GeminiClient 提取的图片生成逻辑。"""

from __future__ import annotations

import asyncio
import json as json_module
import logging
from pathlib import Path
from typing import ClassVar

from PIL import Image

from lib.backends.backend_runtime import read_reference_images
from lib.backends.gemini_shared import (
    GEMINI_DEFAULT_IMAGE_MODEL,
    VERTEX_SCOPES,
    RateLimiter,
    gemini_http_options,
    get_shared_rate_limiter,
    resolve_gemini_api_key,
    with_retry_async,
)
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageOutputMissingError,
    ImageRequestFacts,
    per_image_usage,
)
from lib.backends.providers import PROVIDER_GEMINI
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.config.system_config import resolve_vertex_credentials_path
from lib.config.url_utils import normalize_base_url
from lib.infra.async_thread import run_noninterruptible_sync
from lib.infra.image_utils import convert_image_bytes_to_png
from lib.infra.logging_utils import format_kwargs_for_log

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = GEMINI_DEFAULT_IMAGE_MODEL
_GEMINI_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"})


class GeminiImageBackend:
    """Gemini 图片生成后端，支持 AI Studio 和 Vertex AI。"""

    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES
    CHANNEL_REFERENCE_LIMIT: ClassVar[int | None] = None

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        image_model: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
        transport_layer: TransportLayer | None = None,
    ):
        from google import genai as _genai
        from google.genai import types as _types

        self._types = _types
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._backend_type = backend_type.strip().lower()
        self._image_model = image_model or DEFAULT_IMAGE_MODEL

        if self._backend_type == "vertex":
            from google.oauth2 import service_account

            credentials_file: Path | None = None
            credentials_file = Path(credentials_path) if credentials_path else resolve_vertex_credentials_path()

            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            project_id = creds_data.get("project_id")

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._client = _genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
        else:
            api_key = resolve_gemini_api_key(api_key)
            effective_base_url = normalize_base_url(base_url)
            self._client = _genai.Client(
                api_key=api_key, http_options=gemini_http_options(effective_base_url, transport_layer)
            )

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._image_model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return set(self.CHANNEL_CAPABILITIES)

    async def reference_image_limit(self) -> int | None:
        return self.CHANNEL_REFERENCE_LIMIT

    @staticmethod
    def _usage(image_size: str | None) -> Usage:
        """上报输出张数，分辨率取请求里的 ``image_size``（没发就不报）。

        参考图按输入 token 计费，不报张数，以免与输入 token 重叠；输入 token 与思考 token 目前不在价目表里，也不报。
        """
        return per_image_usage(
            output_images=1,
            input_images=None,
            dimensions={"resolution": image_size} if image_size is not None else None,
        )

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        return GeminiImageBackend._usage(facts.image_size)

    @with_retry_async(max_attempts=5, backoff_seconds=(2, 4, 8, 16, 32))
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """异步生成图片。"""
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._image_model)

        contents = await asyncio.to_thread(self._reference_parts, request)
        contents.append(request.prompt)

        image_config_kwargs: dict = {"aspect_ratio": request.aspect_ratio}
        if request.image_size is not None:
            image_config_kwargs["image_size"] = request.image_size

        config = self._types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=self._types.ImageConfig(**image_config_kwargs),
        )

        logger.info(
            "调用 %s 图片 SDK payload=%s",
            self.name,
            format_kwargs_for_log(
                {"model": self._image_model, "contents": contents, "image_config": image_config_kwargs}
            ),
        )
        response = await self._client.aio.models.generate_content(
            model=self._image_model, contents=contents, config=config
        )

        await run_noninterruptible_sync(self._process_image_response, response, request.output_path, self._image_model)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._image_model,
            usage=self._usage(request.image_size),
        )

    def _reference_parts(self, request: ImageGenerationRequest) -> list:
        images = read_reference_images((ref.path for ref in request.reference_images), self.model)
        parts = []
        for image in images:
            content, mime = image.content, image.mime
            if mime not in _GEMINI_IMAGE_MIME_TYPES:
                content, mime = convert_image_bytes_to_png(content), "image/png"
            parts.append(self._types.Part.from_bytes(data=content, mime_type=mime))
        return parts

    @staticmethod
    def _process_image_response(response, output_path: Path, model: str) -> Image.Image:
        """解析图片生成响应并保存到文件。"""
        for part in response.parts or []:
            if part.inline_data is not None:
                image = part.as_image()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(output_path)
                return image
        feedback = getattr(response, "prompt_feedback", None)
        candidate = (getattr(response, "candidates", None) or [None])[0]
        logger.warning(
            "Gemini 未返回图片 model=%s block_reason=%s finish_reason=%s finish_message=%s text=%.200s",
            model,
            getattr(feedback, "block_reason", None),
            getattr(candidate, "finish_reason", None),
            getattr(candidate, "finish_message", None),
            (getattr(response, "text", None) if response.parts else None) or "",
        )
        raise ImageOutputMissingError(model=model)
