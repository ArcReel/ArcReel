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

# 缺省尺寸：qwen 用像素 宽*高，wan 用档位预算（换算成像素后下传）
_DEFAULT_QWEN_SIZE = "2048*2048"
_DEFAULT_WAN_BUDGET = "2K"

# 标准档总像素预算（非 pro / 非文生图上限）= 2048×2048；超出须 wan2.7-image-pro 文生图（4K=4096×4096）
_STANDARD_PIXEL_BUDGET = 2048 * 2048

# aspect_ratio → 像素 宽*高。值取 qwen-image-2.0 系列官方推荐档（千问-文生图.md），
# 总像素均 ≤ 2048×2048 且比例在 [1:8, 8:1] 内，故 wan2.7-image 像素方式同样适用、复用此表。
_SIZE_BY_RATIO: dict[str, str] = {
    "16:9": "2688*1536",
    "9:16": "1536*2688",
    "1:1": "2048*2048",
    "4:3": "2368*1728",
    "3:4": "1728*2368",
}

# wan 系档位 → 各比例的显式像素。wan 的「方式一档位」在文生图下会强制输出正方形、丢掉比例，
# 故 backend 永不下传档位词，一律换算成「方式二像素值」。2K 复用上方官方推荐档；1K/4K 按 2K
# 等比 ×0.5 / ×2 并对齐 16 的倍数，保持官方推荐档的精确比例。4K（总像素 4096×4096）仅
# wan2.7-image-pro 文生图可用，门控见 _resolve_size。
_WAN_PIXELS_BY_BUDGET: dict[str, dict[str, str]] = {
    "1K": {"16:9": "1344*768", "9:16": "768*1344", "1:1": "1024*1024", "4:3": "1184*864", "3:4": "864*1184"},
    "2K": _SIZE_BY_RATIO,
    "4K": {"16:9": "5376*3072", "9:16": "3072*5376", "1:1": "4096*4096", "4:3": "4736*3456", "3:4": "3456*4736"},
}


def _has_pixel_sep(size: str) -> bool:
    """size 是否为显式像素值（含 宽*高 分隔符），区别于 1K/2K/4K 档位词。"""
    s = size.lower()
    return "*" in s or "x" in s or "×" in s


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
    def _exceeds_standard_budget(size: str) -> bool:
        """size 是否超出标准档总像素预算（2048×2048）。

        docs 口径：超出 2048×2048 的输出仅 wan2.7-image-pro 文生图支持（4K 档=4096×4096）。
        档位 "1K"/"2K" 在预算内、"4K" 超预算；像素值按"总像素 > 预算"判定，避免只认 "4K"
        字面而让 "4096*4096" / "3000*3000" 等数字写法绕过门控（这是按比例算总像素，
        故 "4096*512" 这类窄幅合法尺寸不会被误拒）。
        """
        normalized = size.strip().upper()
        if normalized in ("1K", "2K"):
            return False
        if normalized == "4K":
            return True
        for sep in ("*", "X", "×"):
            if sep in normalized:
                parts = normalized.split(sep, 1)
                try:
                    return int(parts[0]) * int(parts[1]) > _STANDARD_PIXEL_BUDGET
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
        # wan 系：超 2048×2048 预算的输出（4K 档或大像素值）仅 wan2.7-image-pro 文生图支持，
        # 非 pro 不支持、pro 的 I2I 不支持 —— 先门控（档位词与像素值统一判定）
        budget = explicit or _DEFAULT_WAN_BUDGET
        if self._exceeds_standard_budget(budget) and ("pro" not in self._model.lower() or has_refs):
            raise ImageCapabilityError("image_dashscope_4k_t2i_only", model=self._model)
        # 显式像素值（caller 已定比例）原样 honor
        if _has_pixel_sep(explicit):
            return explicit
        # 档位词 / 空 → 一律按 aspect_ratio 换算成显式像素，绝不下传档位词，
        # 否则 wan 文生图会被强制输出正方形、丢掉项目的 16:9 / 9:16 比例
        tier = explicit.upper() if explicit.upper() in _WAN_PIXELS_BY_BUDGET else _DEFAULT_WAN_BUDGET
        table = _WAN_PIXELS_BY_BUDGET[tier]
        return table.get(request.aspect_ratio, table["1:1"])

    def _build_content(self, request: ImageGenerationRequest, has_refs: bool) -> list[dict]:
        content: list[dict] = []
        if has_refs:
            # fail-loud：任一声明的参考图缺失（含目录/空串解析出的 "."）或读取失败（权限/并发删除
            # → OSError）即中止生成并报错列出文件名，让用户感知到有图未被使用，而非静默丢弃、用子集
            # 生成出错误结果还照常计费。
            data_uris: list[str] = []
            unreadable: list[str] = []
            for ref in request.reference_images:
                path = Path(ref.path) if ref.path else None
                if path is None or not path.is_file():
                    unreadable.append(path.name if path else "(空路径)")
                    continue
                try:
                    data_uris.append(image_to_data_uri(path))
                except OSError as exc:
                    logger.warning("DashScope 参考图读取失败: %s (%s)", path, exc)
                    unreadable.append(path.name)
            if unreadable:
                raise ImageCapabilityError(
                    "image_reference_images_unreadable", model=self._model, names="、".join(unreadable)
                )
            if len(data_uris) > self._ref_limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(data_uris),
                    self._model,
                    self._ref_limit,
                )
                data_uris = data_uris[: self._ref_limit]
            content.extend({"image": uri} for uri in data_uris)
        content.append({"text": request.prompt})
        return content
