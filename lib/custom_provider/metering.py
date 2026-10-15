"""自定义供应商调用按用量契约上报的计量（docs/adr/0100）。

自定义供应商的调用通道大多复用内置调用通道的实现，计量就取那条通道的声明：视频取 ``metered_usage``，图片的
预估取 ``estimate_usage``，结算时图片通道自己在结果里带 ``usage``。声明式视频端点按定义上报。其余通道（ComfyUI、
声明式图片、OpenAI Chat 出图）没有声明，按通用口径上报：视频报发出的时长、分辨率与是否有声，图片报一张输出与
参考图张数；图片通道有声明时也补上这两项张数。计量项的单价由对应条目的价目表决定，价目表不按它计价的计数类
计量项不计费。

结算与预估走同一组函数，两边口径一致。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.backends.image_backends.base import ImageRequestFacts, per_image_usage
from lib.backends.video_backends.metering import video_usage
from lib.billing.catalog_pricing import price_custom_call
from lib.billing.metering import Cost, Usage
from lib.custom_provider import is_custom_provider
from lib.db.repositories.custom_provider_repo import CustomModelPricing, CustomProviderRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from lib.custom_provider.endpoints import EndpointSpec

logger = logging.getLogger(__name__)

VideoMetering = Callable[[str, VideoGenerationRequest, VideoGenerationResult | None], Usage]


def generic_video_usage(request: VideoGenerationRequest, result: VideoGenerationResult | None = None) -> Usage:
    """没有声明计量的视频通道按通用口径上报：发出的时长、分辨率档位与是否有声。"""
    return video_usage(
        request,
        result,
        seconds=request.duration_seconds,
        resolution=request.resolution,
        audio=request.generate_audio,
    )


def generic_image_usage(reference_count: int | None) -> Usage:
    """没有声明计量的图片通道按通用口径上报：一张输出与参考图张数（不知道几张时不报）。"""
    return per_image_usage(output_images=1, input_images=reference_count)


def with_image_counts(usage: Usage | None, reference_count: int | None) -> Usage:
    """在调用通道上报的图片用量上补齐通用口径的张数：通道已报的计量项原样保留。

    同一个调用通道上挂的模型可能按 token 计价（如 gpt-image-2），也可能按张计价（如中转站经 OpenAI Images
    协议转发的 Seedream）。补上张数后按张计价的条目查得到用量；按 token 计价的条目里没有张数这类计数计量项，
    不计费。
    """
    counts = generic_image_usage(reference_count)
    if usage is None:
        return counts
    return Usage(
        quantities={**counts.quantities, **usage.quantities},
        dimensions=dict(usage.dimensions),
        text=usage.text,
    )


def video_metering_for(metering_backend: object | None, definition: object | None) -> VideoMetering | None:
    """端点的视频计量函数：内置调用通道类上的 ``metered_usage``，或声明式定义；都没有时为 ``None``。"""
    if metering_backend is not None:
        metered = getattr(metering_backend, "metered_usage", None)
        if callable(metered):
            return cast("VideoMetering", metered)
    if isinstance(definition, Mapping) and definition.get("kind") != COMFYUI_KIND:
        from lib.custom_provider.declarative_backend import declarative_video_usage

        frozen = definition

        def _declarative(model: str, request: VideoGenerationRequest, result: VideoGenerationResult | None) -> Usage:
            del model
            return declarative_video_usage(frozen, request, result)

        return _declarative
    return None


def custom_video_usage(
    metering: VideoMetering | None,
    model: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
) -> Usage:
    """一次自定义视频请求的计量：调用通道认得这个模型时取它的声明，认不得（抛 ``ValueError``）或没有声明时取通用口径。"""
    if metering is not None:
        try:
            return metering(model, request, result)
        except (ValueError, KeyError):
            logger.debug("调用通道不认得模型 %s 的计量，改按通用口径上报", model, exc_info=True)
    return generic_video_usage(request, result)


def custom_image_estimate(metering_backend: object | None, facts: ImageRequestFacts) -> Usage:
    """一次自定义图片请求的预估用量：调用通道有 ``estimate_usage`` 时取它并补齐张数（见 :func:`with_image_counts`），
    否则取通用口径。"""
    estimate = getattr(metering_backend, "estimate_usage", None) if metering_backend is not None else None
    usage: Usage | None = None
    if callable(estimate):
        try:
            estimated = estimate(facts)
        except (ValueError, KeyError):
            logger.debug("调用通道不认得模型 %s 的预估用量，改按通用口径", facts.model, exc_info=True)
        else:
            usage = estimated if isinstance(estimated, Usage) else None
    return with_image_counts(usage, facts.reference_count)


@dataclass(frozen=True, slots=True)
class CustomPriceSource:
    """自定义供应商模型报价要读库的两项：模型行的计价依据（倍率、条目绑定），调用端点的计量声明。

    ``endpoint_spec`` 为 ``None`` 表示模型行或端点解析不出，计量按通用口径。
    """

    pricing: CustomModelPricing
    endpoint_spec: EndpointSpec | None = None

    def video_usage(self, model: str, request: VideoGenerationRequest) -> Usage:
        """一次视频请求的预估用量，与结算同一组计量声明。"""
        spec = self.endpoint_spec
        metering = video_metering_for(spec.metering_backend, spec.definition) if spec is not None else None
        return custom_video_usage(metering, model, request)

    def image_usage(self, facts: ImageRequestFacts) -> Usage:
        """一次图片请求的预估用量。"""
        return custom_image_estimate(self.endpoint_spec.metering_backend if self.endpoint_spec else None, facts)

    def price(
        self, *, model: str, media_type: str, requests: Sequence[Usage], provider_multiplier: float, estimate: bool
    ) -> Cost:
        """按所对应条目的价目表与两级倍率计价，见 :func:`lib.billing.catalog_pricing.price_custom_call`。"""
        return price_custom_call(
            model=model,
            media_type=media_type,
            entry_binding=self.pricing.entry_binding,
            requests=requests,
            provider_multiplier=provider_multiplier,
            model_multiplier=self.pricing.multiplier,
            estimate=estimate,
        )


async def load_custom_price_source(session: AsyncSession, provider_id: str, model_id: str) -> CustomPriceSource | None:
    """读一个自定义供应商模型的报价来源，与结算同一组读取；不是自定义供应商时为 ``None``。"""
    if not is_custom_provider(provider_id):
        return None
    from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
    from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository

    pricing = await CustomProviderRepository(session).resolve_pricing(provider_id, model_id)
    spec: EndpointSpec | None = None
    if pricing.endpoint:
        try:
            spec = await resolve_endpoint_spec(pricing.endpoint, CustomEndpointRepository(session).get)
        except ValueError:
            logger.debug("自定义模型 %s/%s 的端点解析不出，计量按通用口径", provider_id, model_id, exc_info=True)
    return CustomPriceSource(pricing, spec)
