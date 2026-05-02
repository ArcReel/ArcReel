"""图片生成服务层核心接口定义。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx

from lib.video_backends.base import IMAGE_MIME_TYPES


def image_to_base64_data_uri(image_path: Path) -> str:
    """将本地图片转为 base64 data URI。"""
    suffix = image_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


async def download_image_to_path(url: str, output_path: Path, *, timeout: int = 60) -> None:
    """从 URL 异步下载图片到本地文件。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        content = resp.content

    def _save() -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    await asyncio.to_thread(_save)


async def save_image_from_response_item(item, output_path: Path) -> None:
    """从 OpenAI 兼容 SDK 的 ``response.data[i]`` 提取图片并写入本地路径。

    优先 ``b64_json``；为空降级到 ``url`` 下载；两者皆空抛 ``ValueError``。
    """
    b64 = getattr(item, "b64_json", None)
    if b64:

        def _decode_and_save() -> None:
            # 解码 + 写盘统一 offload 到线程，避免在事件循环内做 CPU 密集 base64 解码
            image_bytes = base64.b64decode(b64)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)

        await asyncio.to_thread(_decode_and_save)
        return
    url = getattr(item, "url", None)
    if url:
        await download_image_to_path(url, output_path)
        return
    raise ValueError("图片生成响应既无 b64_json 也无 url")


class ImageCapability(StrEnum):
    """图片后端支持的能力枚举。"""

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"


@dataclass
class ReferenceImage:
    """参考图片。"""

    path: str
    label: str = ""


@dataclass
class ImageGenerationRequest:
    """通用图片生成请求。各 Backend 忽略不支持的字段。"""

    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str | None = None
    project_name: str | None = None
    seed: int | None = None


@dataclass
class ImageGenerationResult:
    """通用图片生成结果。"""

    image_path: Path
    provider: str
    model: str
    image_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    quality: str | None = None
    # OpenAI GPT Image 系列的 token 用量拆分（其它 backend 默认 None）
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None


class ImageBackend(Protocol):
    """图片生成后端协议。"""

    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[ImageCapability]: ...
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class ImageCapabilityError(RuntimeError):
    """图像后端能力不匹配（endpoint mismatch / generator gating 共用）。

    不携带本地化字符串，只带稳定 code + 上下文 params；
    路由层捕获后用 _t(code, **params) 渲染。
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)
