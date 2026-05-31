"""DashScopeImageBackend — 阿里百炼 Qwen-Image / 万相图像生成后端（同步）。

走原生 multimodal-generation/generation 同步端点，T2I 与 I2I 共用同一请求体，
只差 content 是否含 image 元素。覆盖 qwen-image-2.0 融合系列、qwen-image-edit
编辑系列与 wan2.7-image 系列。schema 依据 docs/dashscope-docs/ 一手核实快照。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.dashscope_shared import (
    DASHSCOPE_RETRYABLE_ERRORS,
    dashscope_headers,
    dashscope_native_base_url,
    extract_image_url,
    image_to_data_uri,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    download_image_to_path,
)
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_DASHSCOPE
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-image-2.0"

_IMAGE_ENDPOINT = "/services/aigc/multimodal-generation/generation"

# 编辑系列仅图生图（无文生图能力）；子串覆盖 qwen-image-edit / -edit-plus / -edit-max
_I2I_ONLY_MARKERS = ("qwen-image-edit",)

# 参考图上限：qwen 系 1~3 张、wan 系 0~9 张（docs 确权）
_QWEN_REF_LIMIT = 3
_WAN_REF_LIMIT = 9

# 缺省尺寸：qwen 用像素 宽*高，wan 用档位
_DEFAULT_QWEN_SIZE = "2048*2048"
_DEFAULT_WAN_SIZE = "2K"

# aspect_ratio → 像素 宽*高。值取 qwen-image-2.0 系列官方推荐档（千问-文生图.md），
# 总像素均 ≤ 2048×2048 且比例在 [1:8, 8:1] 内，故 wan2.7-image 像素方式同样适用、复用此表。
_SIZE_BY_RATIO: dict[str, str] = {
    "16:9": "2688*1536",
    "9:16": "1536*2688",
    "1:1": "2048*2048",
    "4:3": "2368*1728",
    "3:4": "1728*2368",
}

# 编辑系列宽高均 ∈ [512, 2048]，单独一张 ≤2048 的授权档表。
_EDIT_SIZE_BY_RATIO: dict[str, str] = {
    "16:9": "2048*1152",
    "9:16": "1152*2048",
    "1:1": "2048*2048",
    "4:3": "2048*1536",
    "3:4": "1536*2048",
}


class DashScopeImageBackend:
    """阿里百炼图像后端（同步 multimodal 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = 120.0,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        mid = self._model.lower()
        self._is_wan = mid.startswith("wan")
        self._is_edit = "qwen-image-edit" in mid
        self._capabilities = self._resolve_caps(self._model)

    @staticmethod
    def _resolve_caps(model: str) -> set[ImageCapability]:
        mid = model.lower()
        if any(marker in mid for marker in _I2I_ONLY_MARKERS):
            return {ImageCapability.IMAGE_TO_IMAGE}
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    @staticmethod
    def _is_4k_size(size: str) -> bool:
        """档位 "4K" 或等价 4K 像素值（3840×2160 任意朝向）都算 4K，避免数字写法绕过门控。"""
        normalized = size.strip().upper()
        if normalized == "4K":
            return True
        for sep in ("*", "X", "×"):
            if sep in normalized:
                parts = normalized.split(sep, 1)
                try:
                    return {int(parts[0]), int(parts[1])} == {3840, 2160}
                except ValueError:
                    return False
        return False

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    @property
    def _ref_limit(self) -> int:
        return _WAN_REF_LIMIT if self._is_wan else _QWEN_REF_LIMIT

    @with_retry_async(retryable_errors=DASHSCOPE_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)

        size = self._resolve_size(request, has_refs)
        content = self._build_content(request, has_refs)

        parameters: dict = {
            "n": 1,
            "watermark": False,
            # ArcReel 剧本 prompt 已是 LLM 精炼描述，关闭智能改写保留原意
            "prompt_extend": False,
            "size": size,
        }
        if request.seed is not None:
            parameters["seed"] = request.seed

        payload = {
            "model": self._model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }

        logger.info(
            "调用 %s 图片 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.post(
                f"{self._base_url}{_IMAGE_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key),
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"DashScope 图像接口返回 {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        url = extract_image_url(data)
        await download_image_to_path(url, request.output_path)
        logger.info("DashScope 图片生成完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            image_uri=url,
        )

    def _resolve_size(self, request: ImageGenerationRequest, has_refs: bool) -> str:
        explicit = (request.image_size or "").strip()
        if not self._is_wan:
            # qwen 系：caller 显式指定优先；否则按 aspect_ratio 选授权像素档（编辑系列宽高 ≤2048）。
            # 不按 aspect 选档会让项目 aspect_ratio 静默失效、一律产出 1:1 方图。
            if explicit:
                return explicit
            table = _EDIT_SIZE_BY_RATIO if self._is_edit else _SIZE_BY_RATIO
            return table.get(request.aspect_ratio, _DEFAULT_QWEN_SIZE)
        # wan 系：未显式指定时同样按 aspect_ratio 选像素值（满足 wan2.7 总像素/比例约束）
        if not explicit:
            return _SIZE_BY_RATIO.get(request.aspect_ratio, _DEFAULT_WAN_SIZE)
        # 显式档位/像素值 honor。4K 仅 wan2.7-image-pro 文生图支持：非 pro 不支持、pro 的 I2I 不支持
        if self._is_4k_size(explicit) and ("pro" not in self._model.lower() or has_refs):
            raise ImageCapabilityError("image_dashscope_4k_t2i_only", model=self._model)
        return explicit

    def _build_content(self, request: ImageGenerationRequest, has_refs: bool) -> list[dict]:
        content: list[dict] = []
        if has_refs:
            # 用 is_file 而非 exists：空串 "" 解析为当前目录、exists() 返回 True，
            # 后续 read_bytes 会撞 IsADirectoryError。非空 + 常规文件才纳入。
            existing = [p for ref in request.reference_images if ref.path and (p := Path(ref.path)).is_file()]
            if not existing:
                # 模型支持 i2i，只是参考图缺失/不可读；用准确的错误码而非"模型不支持 i2i"
                raise ImageCapabilityError("image_reference_images_unreadable", model=self._model)
            if len(existing) > self._ref_limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(existing),
                    self._model,
                    self._ref_limit,
                )
                existing = existing[: self._ref_limit]
            content.extend({"image": image_to_data_uri(p)} for p in existing)
        content.append({"text": request.prompt})
        return content
