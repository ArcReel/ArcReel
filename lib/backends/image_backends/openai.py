"""OpenAIImageBackend — OpenAI 图片生成后端。"""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from typing import ClassVar, Literal

import openai

from arcreel_market_core.aspect_size import IMAGE_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from arcreel_market_core.video_backend_contract import IMAGE_MIME_TYPES
from lib.backends.image_backends.base import (
    BOTH_IMAGE_CAPABILITIES,
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
    per_image_usage,
    save_image_from_response_item,
)
from lib.backends.openai_shared import (
    OPENAI_IMAGE_QUALITY_MAP as _QUALITY_MAP,
)
from lib.backends.openai_shared import (
    OPENAI_RETRYABLE_ERRORS,
    create_openai_client,
)
from lib.backends.providers import PROVIDER_OPENAI
from lib.backends.transport_layer import TransportLayer
from lib.billing.metering import Usage
from lib.infra.logging_utils import format_kwargs_for_log
from lib.infra.retry import with_retry_async

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-2"
_MAX_REFERENCE_IMAGES = 16
ImageBackendMode = Literal["both", "generations_only", "edits_only"]

# gpt-image-2 / gpt-image-2-2026-04-21：size 接受任意 WxH，宽高均被 16 整除，比例 1:3~3:1；
# ≤2560x1440 稳定，~3840x2160 实验性，最大 3840x2160（4K）。
_GPT_IMAGE_MAX_LONG_EDGE = 3840
_GPT_IMAGE_STABLE_LONG_EDGE = 2560
_GPT_IMAGE_MAX_RATIO = 3.0

# 档位 → quality 大小写不敏感（与 resolution_to_short_edge 同口径）：用户自定义档位词可能写
# "2k"/"4K "，不归一会让 size 解析成功但 quality 静默丢失。自定义 WxH（无档位）→ None。
_QUALITY_MAP_CI = {k.lower(): v for k, v in _QUALITY_MAP.items()}


def _quality_for(image_size: str | None) -> str | None:
    return _QUALITY_MAP_CI.get(image_size.strip().lower()) if image_size else None


def _resolve_openai_params(
    image_size: str | None,
    aspect_ratio: str,
) -> dict[str, str]:
    """按「比例优先、清晰度其次」算出 {size, quality}。

    比例永远来自 aspect_ratio；image_size（档位 / 自定义 WxH / None）只决定清晰度短边，
    自定义 WxH 剥离其自带比例（取 min 当短边）。image_size=None 时按默认 720P 短边兜底，
    仍下传精确比例 size——不再回退 SDK 默认（否则中转网关会用自家默认比例，丢掉项目比例）。
    """
    short = resolution_to_short_edge(image_size, tier_map=IMAGE_TIER_SHORT_EDGE)
    w, h = aspect_size(
        aspect_ratio,
        short,
        round_to=16,
        max_long_edge=_GPT_IMAGE_MAX_LONG_EDGE,
        max_ratio=_GPT_IMAGE_MAX_RATIO,
    )
    if max(w, h) > _GPT_IMAGE_STABLE_LONG_EDGE:
        logger.warning(
            "OpenAI image: 尺寸 %dx%d 长边超过稳定区 %d，进入实验性高分辨率区间",
            w,
            h,
            _GPT_IMAGE_STABLE_LONG_EDGE,
        )
    params: dict[str, str] = {"size": f"{w}x{h}"}
    quality = _quality_for(image_size)
    if quality:
        params["quality"] = quality
    return params


# gpt-image-2 输出 token 估算：官方「Calculating costs」计算器的公式，各质量档的基数取自同一处。
# https://developers.openai.com/api/docs/guides/image-generation#calculating-costs
_GPT_IMAGE_2_QUALITY_BASE = {"low": 16, "medium": 48, "high": 96}


def _is_gpt_image_2(model: str) -> bool:
    """``gpt-image-2`` 与它的日期快照（如 ``gpt-image-2-2026-04-21``），不含 2.5 等后续型号。"""
    return model == "gpt-image-2" or model.startswith("gpt-image-2-")


def estimate_gpt_image_2_output_tokens(size: str, quality: str) -> int | None:
    """按尺寸与质量估 gpt-image-2 一张图的输出 token；质量不是 low / medium / high 或尺寸不是宽x高时返回 ``None``。

    照搬官方计算器：短边方向的块数 = 质量基数 ÷ 宽高比（四舍五入，恰好 .5 取偶），块数 = 基数 × 短边块数，
    token = ⌈块数 × (2,000,000 + 宽 × 高) ÷ 4,000,000⌉。
    """
    base = _GPT_IMAGE_2_QUALITY_BASE.get(quality)
    width_text, sep, height_text = size.partition("x")
    if base is None or not sep or not width_text.isdigit() or not height_text.isdigit():
        return None
    width, height = int(width_text), int(height_text)
    if width <= 0 or height <= 0:
        return None
    scaled = base / (max(width, height) / min(width, height))
    floor = math.floor(scaled)
    short_blocks = floor + floor % 2 if scaled - floor == 0.5 else math.floor(scaled + 0.5)
    return math.ceil(base * short_blocks * (2_000_000 + width * height) / 4_000_000)


def _token_usage(
    *,
    text_input: int | None,
    image_input: int | None,
    image_output: int | None,
    text_output: int | None,
    dimensions: dict[str, object],
) -> Usage:
    """按 token 计价的用量：提示词文字、参考图（图片输入 token）与输出图片各一项，没有的项不报。

    Images API 不走缓存（官方：缓存价只适用于 Responses API），不报缓存读。
    """
    quantities = {
        meter: value
        for meter, value in (
            ("uncached_input_tokens", text_input),
            ("uncached_image_input_tokens", image_input),
            ("image_output_tokens", image_output),
            ("output_tokens", text_output),
        )
        if value is not None
    }
    return Usage(quantities=quantities, dimensions=dimensions)


def _dimensions(params: dict[str, str]) -> dict[str, object]:
    """请求里的 ``size`` 与 ``quality`` 作为选择维度；没发 ``quality`` 时不报。"""
    dims: dict[str, object] = {"resolution": params["size"]}
    if "quality" in params:
        dims["quality"] = params["quality"]
    return dims


class OpenAIImageBackend:
    """OpenAI 图片生成后端，按 mode 决定支持 T2I / I2I / 两者。"""

    #: 内置供应商按缺省的 ``both`` 构造；自定义端点按 mode 收窄，通道事实由端点声明。
    CHANNEL_CAPABILITIES: ClassVar[frozenset[ImageCapability]] = BOTH_IMAGE_CAPABILITIES

    _MODE_TO_CAPS: ClassVar[dict[str, set[ImageCapability]]] = {
        "both": {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE},
        "generations_only": {ImageCapability.TEXT_TO_IMAGE},
        "edits_only": {ImageCapability.IMAGE_TO_IMAGE},
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        transport_layer: TransportLayer | None = None,
        mode: ImageBackendMode = "both",
    ):
        self._client = create_openai_client(api_key=api_key, base_url=base_url, transport_layer=transport_layer)
        self._model = model or DEFAULT_MODEL
        self._capabilities = set(self._MODE_TO_CAPS[mode])

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def reference_image_limit(self) -> int:
        return _MAX_REFERENCE_IMAGES

    @staticmethod
    def estimate_usage(facts: ImageRequestFacts) -> Usage:
        """预估用量：输出 token 按官方计算器公式估（目前只有 gpt-image-2）。

        提示词文字 token 记 0：费用页与整批预估拿不到提示词，这部分会偏低。文字输出与缓存在 Images API 上
        不发生，记 0。参考图的图片输入 token 无法从请求事实估出：没有参考图时记 0，有参考图时不报。
        """
        params = _resolve_openai_params(facts.image_size, facts.aspect_ratio)
        quality = params.get("quality")
        output = (
            estimate_gpt_image_2_output_tokens(params["size"], quality)
            if quality is not None and _is_gpt_image_2(facts.model)
            else None
        )
        usage = _token_usage(
            text_input=0,
            image_input=0 if facts.reference_count == 0 else None,
            image_output=output,
            text_output=0,
            dimensions=_dimensions(params),
        )
        return Usage(
            quantities={**usage.quantities, "cache_read_tokens": 0, "image_cache_read_tokens": 0},
            dimensions=usage.dimensions,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        has_refs = bool(request.reference_images)
        if has_refs and ImageCapability.IMAGE_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_i2i", model=self._model)
        if not has_refs and ImageCapability.TEXT_TO_IMAGE not in self._capabilities:
            raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model=self._model)
        return await (self._generate_edit(request) if has_refs else self._generate_create(request))

    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        kwargs = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
        }
        params = _resolve_openai_params(request.image_size, request.aspect_ratio)
        kwargs.update(params)
        logger.info("调用 %s 图片 SDK (T2I) kwargs=%s", self.name, format_kwargs_for_log(kwargs))
        response = await self._client.images.generate(**kwargs)
        return await self._save_and_return(response, request, params)

    async def _generate_edit(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        refs = request.reference_images
        if len(refs) > _MAX_REFERENCE_IMAGES:
            logger.warning("参考图数量 %d 超过上限 %d，截断", len(refs), _MAX_REFERENCE_IMAGES)
            refs = refs[:_MAX_REFERENCE_IMAGES]

        def _read_refs() -> list[tuple[str, bytes, str]]:
            """在线程里读出参考图：交给 SDK 的若是文件句柄，multipart 会在事件循环上读文件。"""
            files = []
            for ref in refs:
                ref_path = Path(ref.path)
                try:
                    data = ref_path.read_bytes()
                except FileNotFoundError:
                    logger.warning("参考图不存在，跳过: %s", ref_path)
                    continue
                files.append((ref_path.name, data, IMAGE_MIME_TYPES.get(ref_path.suffix.lower(), "image/png")))
            return files

        image_files = await asyncio.to_thread(_read_refs)
        if not image_files:
            # 参考图全部打不开时报错，不降级为 T2I：请求要的是 I2I，没有有效素材属于错误
            raise ImageCapabilityError(
                "image_endpoint_mismatch_no_i2i",
                model=self._model,
                detail="all reference images failed to open",
            )
        # I2I 与 T2I 对称下传 size/quality：images.edit 不带 size 时比例由上游默认决定，项目 aspect_ratio 不生效
        edit_kwargs: dict = {
            "model": self._model,
            "image": image_files,
            "prompt": request.prompt,
        }
        params = _resolve_openai_params(request.image_size, request.aspect_ratio)
        edit_kwargs.update(params)
        logger.info(
            "调用 %s 图片 SDK (I2I) kwargs=%s",
            self.name,
            format_kwargs_for_log({**edit_kwargs, "image": f"<{len(image_files)} files>"}),
        )
        try:
            response = await self._client.images.edit(**edit_kwargs)
        except openai.NotFoundError as exc:
            # 火山方舟没有 /images/edits：Seedream 带参考图要走「火山方舟 (Seedream)」端点。早先按名字推断
            # 落在 OpenAI 图片端点上的存量配置会在这里稳定 404，改报一条指明换端点的原因，不改写用户配置。
            if "seedream" in self._model.lower():
                raise ImageCapabilityError("image_seedream_edits_unavailable", model=self._model) from exc
            raise
        return await self._save_and_return(response, request, params)

    async def _save_and_return(
        self, response, request: ImageGenerationRequest, params: dict[str, str]
    ) -> ImageGenerationResult:
        data = getattr(response, "data", None) or []
        if not data:
            # 空 data 通常是内容安全过滤命中或上游网关异常，给出清晰错误便于排查
            raise RuntimeError(
                f"OpenAI 图片生成响应 data 为空 (model={self._model})，可能触发内容安全过滤或上游服务异常"
            )
        await save_image_from_response_item(data[0], request.output_path)
        logger.info("OpenAI 图片生成完成: %s", request.output_path)

        img_in = img_out = txt_in = txt_out = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                in_details = getattr(usage, "input_tokens_details", None)
                # 必须拿到 input 拆分（image_tokens / text_tokens 至少一项有值），否则保留 None，
                # 避免部分字段缺失场景下漏算 input 费用
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
                    # 输入拆分到手但输出完全拿不到 → 数据残缺，撤回，按拿不到 usage 处理
                    if img_out is None and txt_out is None:
                        img_in = txt_in = None
            except Exception:
                logger.warning("OpenAI image usage 解析失败", exc_info=True)
                img_in = img_out = txt_in = txt_out = None

        # 有 usage 时按 token 上报（参考图已计入图片输入 token，不报张数）；拿不到 usage 时只报输出张数，
        # 价目表按 token 计价，这笔会记为未定价，不按估算值记账。
        usage = (
            _token_usage(
                text_input=txt_in,
                image_input=img_in,
                image_output=img_out,
                text_output=txt_out,
                dimensions=_dimensions(params),
            )
            if img_out is not None or txt_out is not None
            else per_image_usage(output_images=len(data), input_images=None, dimensions=_dimensions(params))
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            usage=usage,
            image_input_tokens=img_in,
            image_output_tokens=img_out,
            text_input_tokens=txt_in,
            text_output_tokens=txt_out,
        )
