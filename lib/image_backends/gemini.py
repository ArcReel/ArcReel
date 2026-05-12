"""GeminiImageBackend — 从 GeminiClient 提取的图片生成逻辑。"""

from __future__ import annotations

import json as json_module
import logging
from pathlib import Path

from PIL import Image

from lib.config.url_utils import normalize_base_url
from lib.gemini_shared import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter, with_retry_async
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_GEMINI
from lib.system_config import resolve_vertex_credentials_path

logger = logging.getLogger(__name__)

# 跳过名称推断的文件名模式
SKIP_NAME_PATTERNS = ("scene_", "storyboard_", "output_")

# 默认图片模型
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"


class GeminiImageBackend:
    """Gemini 图片生成后端，支持 AI Studio 和 Vertex AI。"""

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: str | None = None,
        rate_limiter: RateLimiter | None = None,
        image_model: str | None = None,
        base_url: str | None = None,
        credentials_path: str | None = None,
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
            if credentials_path:
                credentials_file = Path(credentials_path)
            else:
                credentials_file = resolve_vertex_credentials_path(Path(__file__).parent.parent.parent)

            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file) as f:
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
            # spec §5.4：不再读 env fallback；缺失即 raise。
            if not api_key:
                raise ValueError("请到系统配置页填写 Gemini API Key")

            effective_base_url = normalize_base_url(base_url)
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self._client = _genai.Client(api_key=api_key, http_options=http_options)

        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._image_model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @with_retry_async(max_attempts=5, backoff_seconds=(2, 4, 8, 16, 32))
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """异步生成图片。"""
        # 1. 限流
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._image_model)

        # 2. 构建 contents（参考图 + prompt）
        contents = self._build_contents_with_labeled_refs(request.prompt, request.reference_images)

        image_config_kwargs: dict = {"aspect_ratio": request.aspect_ratio}
        if request.image_size is not None:
            image_config_kwargs["image_size"] = request.image_size

        config = self._types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=self._types.ImageConfig(**image_config_kwargs),
        )

        # 4. 调用异步 API
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

        # 5. 解析响应并保存
        self._process_image_response(response, request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GEMINI,
            model=self._image_model,
        )

    @staticmethod
    def _load_image_detached(image_path: str | Path) -> Image.Image:
        """从路径加载图片并与底层文件句柄解绑。"""
        with Image.open(image_path) as img:
            return img.copy()

    @staticmethod
    def _extract_name_from_path(image_path: str | Path) -> str | None:
        """从图片路径推断名称。跳过 scene_/storyboard_/output_ 前缀的文件。"""
        path = Path(image_path)
        filename = path.stem
        for pattern in SKIP_NAME_PATTERNS:
            if filename.startswith(pattern):
                return None
        return filename

    def _build_contents_with_labeled_refs(
        self,
        prompt: str,
        reference_images: list[ReferenceImage] | None = None,
    ) -> list:
        """
        构建带名称标签的 contents 列表。

        格式：[标签1, 图片1, 标签2, 图片2, ..., prompt]
        """
        contents: list = []

        if reference_images:
            labeled_refs: list[str] = []
            for ref in reference_images:
                # 确定标签
                label = ref.label.strip() if ref.label else ""
                name = label or self._extract_name_from_path(ref.path)

                if name:
                    labeled_refs.append(name)
                    contents.append(name)

                # 加载图片
                loaded_img = self._load_image_detached(ref.path)
                contents.append(loaded_img)

            if labeled_refs:
                logger.debug("参考图片标签: %s", ", ".join(labeled_refs))

        # prompt 放最后
        contents.append(prompt)
        return contents

    @staticmethod
    def _process_image_response(response, output_path: Path) -> Image.Image:
        """解析图片生成响应并保存到文件。"""
        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(output_path)
                return image
        raise RuntimeError("API 未返回图片")
