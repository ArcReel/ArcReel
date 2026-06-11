"""按 ``kind`` 派发的计费策略：每种定价形状一个纯函数，``calculate_pricing`` 统一入口。

策略只读 ``Pricing`` 声明 + ``PricingParams`` 维度，无 HTTP/DB 依赖，可独立单测。
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.pricing.types import (
    CHARACTERS_PER_PRICING_UNIT,
    PerCharacter,
    PerImageByResolution,
    PerImageFlat,
    PerImageOpenAIToken,
    PerSecondMatrix,
    PerToken,
    PerTokenVideo,
    Pricing,
    ViduDelegate,
)
from lib.vidu_shared import calculate_vidu_cost


@dataclass(frozen=True)
class PricingParams:
    """承载一次计费所需的全部维度；各 kind 策略按需取用。"""

    call_type: str
    model: str | None = None
    resolution: str | None = None
    aspect_ratio: str | None = None
    duration_seconds: int | None = None
    generate_audio: bool = True
    usage_tokens: int | None = None
    service_tier: str = "default"
    input_tokens: int | None = None
    output_tokens: int | None = None
    quality: str | None = None
    size: str | None = None
    image_input_tokens: int | None = None
    image_output_tokens: int | None = None
    text_input_tokens: int | None = None
    text_output_tokens: int | None = None
    n: int = 1


def _per_token(pricing: PerToken, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    rates = pricing.rates.get(model, pricing.rates.get(pricing.default_model, {"input": 0.0, "output": 0.0}))
    amount = ((params.input_tokens or 0) * rates["input"] + (params.output_tokens or 0) * rates["output"]) / 1_000_000
    return amount, pricing.currency


def _per_image_flat(pricing: PerImageFlat, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    per_image = pricing.rates.get(model, pricing.rates[pricing.default_model])
    return per_image * params.n, pricing.currency


def _per_image_by_resolution(pricing: PerImageByResolution, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    model_costs = pricing.rates.get(model, pricing.rates[pricing.default_model])
    # 用 is None 而非 or：模型可显式声明 0.0 免费档，falsy 的 0.0 不应被当作缺失而回落默认费率。
    own_1k = model_costs.get("1K")
    default_cost = own_1k if own_1k is not None else pricing.rates[pricing.default_model].get("1K", 0.0)
    resolution = params.resolution or "1K"
    return model_costs.get(resolution.upper(), default_cost) * params.n, pricing.currency


def _per_image_openai_token(pricing: PerImageOpenAIToken, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    has_usage = any(
        t is not None
        for t in (
            params.image_input_tokens,
            params.image_output_tokens,
            params.text_input_tokens,
            params.text_output_tokens,
        )
    )
    if has_usage:
        rates = pricing.token_rates.get(model, pricing.token_rates[pricing.default_model])
        amount = (
            (params.image_input_tokens or 0) * rates["image_in"]
            + (params.image_output_tokens or 0) * rates["image_out"]
            + (params.text_input_tokens or 0) * rates["text_in"]
            + (params.text_output_tokens or 0) * rates["text_out"]
        ) / 1_000_000
        return amount, pricing.currency

    # 主路径按 token 计费已覆盖绝大多数；此处为 SDK 不返回 usage 的兜底。不再用静态
    # (resolution, aspect_ratio) → size 表反查（已废弃），size 缺失即落默认档，接受兜底
    # 丧失按尺寸区分成本——避免为兜底引入额外尺寸真相源（见 docs/adr/0011）。
    quality = params.quality or "medium"
    size = params.size or "1024x1024"
    model_costs = pricing.fallback_rates.get(model, pricing.fallback_rates[pricing.default_model])
    per_image = model_costs.get(
        (quality, size),
        model_costs.get((quality, "1024x1024"), model_costs.get(("medium", "1024x1024"), 0.034)),
    )
    return per_image * params.n, pricing.currency


def _per_second_matrix(pricing: PerSecondMatrix, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    model_costs = pricing.rates.get(model, pricing.rates[pricing.default_model])
    # 真实 0 秒（如参考模式全零时长聚合）保持 0；缺省（None）才按 8 秒兜底。
    # 「无时长视为 8 秒」的默认由 calculate_cost 对单次实时调用施加，不在此处。
    duration = params.duration_seconds if params.duration_seconds is not None else 8
    if pricing.dimensions == "resolution_audio":
        resolution = (params.resolution or "1080p").lower()
        # 同上：0.0 免费档不应被 or 当作缺失而回落默认模型费率。
        own_1080p = model_costs.get(("1080p", True))
        fallback = (
            own_1080p if own_1080p is not None else pricing.rates[pricing.default_model].get(("1080p", True), 0.0)
        )
        per_second = model_costs.get((resolution, params.generate_audio), fallback)
    elif pricing.dimensions == "resolution_only":
        resolution = (params.resolution or "720p").lower()
        per_second = model_costs.get((resolution, None), model_costs.get(("720p", None), 0.0))
    else:  # flat
        per_second = model_costs.get(("", None), 0.0)
    return duration * per_second, pricing.currency


def _per_token_video(pricing: PerTokenVideo, params: PricingParams) -> tuple[float, str]:
    model = params.model or pricing.default_model
    model_costs = pricing.rates.get(model, pricing.rates[pricing.default_model])
    key = (params.service_tier, params.generate_audio)
    price_per_million = model_costs.get(key, model_costs.get(("default", True), 16.00))
    amount = (params.usage_tokens or 0) / 1_000_000 * price_per_million
    return amount, pricing.currency


def _per_character(pricing: PerCharacter, params: PricingParams) -> tuple[float, str]:
    # 字符数复用通用计数字段 usage_tokens 承载（与 _per_token_video / _vidu 同模式，免新增 DB 列）。
    model = params.model or pricing.default_model
    rate = pricing.rates.get(model, pricing.rates.get(pricing.default_model, 0.0))
    amount = (params.usage_tokens or 0) / CHARACTERS_PER_PRICING_UNIT * rate
    return amount, pricing.currency


def _vidu(pricing: ViduDelegate, params: PricingParams) -> tuple[float, str]:
    return calculate_vidu_cost(
        call_type=params.call_type,
        usage_tokens=params.usage_tokens,
        model=params.model,
        resolution=params.resolution,
        duration_seconds=params.duration_seconds,
    )


def calculate_pricing(pricing: Pricing, params: PricingParams) -> tuple[float, str]:
    """按 ``pricing`` 的运行时类型派发到对应策略，返回 ``(金额, 币种)``。"""
    if isinstance(pricing, PerToken):
        return _per_token(pricing, params)
    if isinstance(pricing, PerImageFlat):
        return _per_image_flat(pricing, params)
    if isinstance(pricing, PerImageByResolution):
        return _per_image_by_resolution(pricing, params)
    if isinstance(pricing, PerImageOpenAIToken):
        return _per_image_openai_token(pricing, params)
    if isinstance(pricing, PerSecondMatrix):
        return _per_second_matrix(pricing, params)
    if isinstance(pricing, PerTokenVideo):
        return _per_token_video(pricing, params)
    if isinstance(pricing, PerCharacter):
        return _per_character(pricing, params)
    # 仅剩 ViduDelegate；若日后新增 kind 未在上方分派，此处类型收窄会让 _vidu 入参报错，
    # 起到穷尽性检查的作用。
    return _vidu(pricing, params)
