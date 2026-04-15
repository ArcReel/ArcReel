"""BailianImageBackend — 百炼图像生成后端（异步任务模式）。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from lib.bailian_shared import BAILIAN_RETRYABLE_ERRORS, DEFAULT_DASHSCOPE_BASE_URL, upload_file_and_get_url
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.providers import PROVIDER_BAILIAN
from lib.retry import with_retry_async
from lib.video_backends.base import poll_with_retry

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "wan2.7-image-pro"

# aspect_ratio 到百炼 size 参数的映射
_SIZE_MAP: dict[str, str] = {
    "9:16": "720*1280",
    "16:9": "1280*720",
    "1:1": "1024*1024",
    "3:4": "768*1152",
    "4:3": "1152*768",
}


class BailianImageBackend:
    """百炼图像生成后端，基于 DashScope 异步任务接口。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        if not api_key:
            raise ValueError("百炼图像后端需要 api_key")
        self._api_key = api_key
        self._model = model or DEFAULT_MODEL
        self._base_url = base_url or DEFAULT_DASHSCOPE_BASE_URL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_BAILIAN

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图像（异步任务模式）。"""
        task_id = await self._submit_task(request)
        logger.info("百炼图像任务已提交: task_id=%s", task_id)

        # 轮询任务状态
        result_data = await poll_with_retry(
            poll_fn=lambda: self._query_task(task_id),
            is_done=lambda r: (
                r.get(
                    "output",
                ).get("task_status")
                == "SUCCEEDED"
            ),
            is_failed=lambda r: _extract_error(r),
            poll_interval=3.0,
            max_wait=300.0,
            retryable_errors=BAILIAN_RETRYABLE_ERRORS,
            label="百炼图像",
        )

        # 下载图像
        image_url = _extract_image_url(result_data)
        await _download_image(image_url, request.output_path)
        logger.info("百炼图像下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_BAILIAN,
            model=self._model,
            image_uri=image_url,
        )

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8), retryable_errors=BAILIAN_RETRYABLE_ERRORS)
    async def _submit_task(self, request: ImageGenerationRequest) -> str:
        """提交图像生成任务。"""
        url = f"{self._base_url}/api/v1/services/aigc/image-generation/generation"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }

        # 构建消息内容
        content = [{"text": request.prompt}]

        # 处理参考图（I2I）
        if request.reference_images:
            for ref in request.reference_images[:3]:  # 最多 3 张
                ref_path = Path(ref.path)
                if ref_path.exists():
                    # 上传本地文件到 OSS
                    oss_url = await upload_file_and_get_url(self._api_key, self._model, ref_path, self._base_url)
                    content.append({"image": oss_url})
                    logger.info("参考图已上传: %s -> %s", ref_path.name, oss_url)

        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": {
                "size": _SIZE_MAP.get(request.aspect_ratio, "1024*1024"),
                "n": 1,
                "watermark": False,
            },
        }

        async with httpx.AsyncClient() as client:
            # 如果使用 oss:// 资源，需要添加解析头
            if any("oss://" in str(c.get("image", "")) for c in content if isinstance(c, dict)):
                headers["X-DashScope-OssResourceResolve"] = "enable"

            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()

        task_id = data["output"]["task_id"]
        return task_id

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8), retryable_errors=BAILIAN_RETRYABLE_ERRORS)
    async def _query_task(self, task_id: str) -> dict:
        """查询任务状态。"""
        url = f"{self._base_url}/api/v1/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()


def _extract_error(result_data: dict) -> str | None:
    """从任务结果中提取错误信息。"""
    status = result_data.get("output", {}).get("task_status")
    if status == "FAILED":
        message = result_data.get("output", {}).get("message", "未知错误")
        return f"百炼图像生成失败: {message}"
    return None


def _extract_image_url(result_data: dict) -> str:
    """从任务结果中提取图像 URL。"""
    choices = result_data.get("output", {}).get("choices", [])
    if not choices:
        raise RuntimeError("百炼图像任务无有效结果")
    content = choices[0].get("message", {}).get("content", [])
    for item in content:
        if item.get("type") == "image":
            return item["image"]
    raise RuntimeError("百炼图像任务结果中未找到图像 URL")


async def _download_image(url: str, output_path: Path, *, timeout: int = 60) -> None:
    """从 URL 下载图像到本地文件。"""
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=timeout)
        response.raise_for_status()
        await asyncio.to_thread(output_path.write_bytes, response.content)
