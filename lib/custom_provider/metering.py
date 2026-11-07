"""Custom-provider measurements under usage contracts (docs/adr/0100).

Most custom channels reuse built-ins and their declarations: video ``metered_usage``, image
``estimate_usage``, with images supplying settlement ``usage`` in results. Declarative videos use definitions. Others (ComfyUI,
declarative images, OpenAI Chat images) lack declarations and use generic metering: sent video duration/resolution/audio; one output
image and reference counts. Declared image channels also receive these counts. Matching entry price tables determine rates;
count measurements absent from tables are uncharged.

Settlement/estimation share functions and semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

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

    from arcreel_catalog_core import CatalogEntry
    from lib.custom_provider.endpoints import EndpointSpec

logger = logging.getLogger(__name__)


class VideoMetering(Protocol):
    """Video-channel declarations: ``(API 模型名, 请求, 结果, entry=模型对应的条目)``; :mod:`lib.backends.video_backends.metering`."""

    def __call__(
        self,
        model: str,
        request: VideoGenerationRequest,
        result: VideoGenerationResult | None,
        /,
        *,
        entry: CatalogEntry | None = None,
    ) -> Usage: ...


def generic_video_usage(request: VideoGenerationRequest, result: VideoGenerationResult | None = None) -> Usage:
    """Generic undeclared video metering: sent duration, resolution tiers, audio."""
    return video_usage(
        request,
        result,
        seconds=request.duration_seconds,
        resolution=request.resolution,
        audio=request.generate_audio,
    )


def generic_image_usage(reference_count: int | None) -> Usage:
    """Generic undeclared image metering: one output plus references, omitting unknown counts."""
    return per_image_usage(output_images=1, input_images=reference_count)


def with_image_counts(usage: Usage | None, reference_count: int | None) -> Usage:
    """Supplement generic image counts without changing channel-reported measurements.

    One channel may host token-priced gpt-image-2 or image-priced relayed Seedream over OpenAI Images.
    Counts supply image-priced tables; token-priced tables lack count measurements,
    leaving them uncharged.
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
    """Endpoint video functions: built-in ``metered_usage`` or declarative definitions; ``None`` without either."""
    if metering_backend is not None:
        metered = getattr(metering_backend, "metered_usage", None)
        if callable(metered):
            return cast("VideoMetering", metered)
    if isinstance(definition, Mapping) and definition.get("kind") != COMFYUI_KIND:
        from lib.custom_provider.declarative_backend import declarative_video_usage

        frozen = definition

        def _declarative(
            model: str,
            request: VideoGenerationRequest,
            result: VideoGenerationResult | None,
            /,
            *,
            entry: CatalogEntry | None = None,
        ) -> Usage:
            del model, entry
            return declarative_video_usage(frozen, request, result)

        return _declarative
    return None


def custom_video_usage(
    metering: VideoMetering | None,
    model: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
    *,
    entry: CatalogEntry | None = None,
) -> Usage:
    """Custom-video request measurements: channel declarations for recognized models; generic if unknown (``ValueError``) or undeclared.

    ``entry`` is corresponding entries, bindings first (:func:`lib.custom_provider.model_facts.model_entry`), identifying models
    consistently with capabilities/construction; requests still send API ``model`` names.
    """
    if metering is not None:
        try:
            return metering(model, request, result, entry=entry)
        except (ValueError, KeyError):
            logger.debug("调用通道不认得模型 %s 的计量，改按通用口径上报", model, exc_info=True)
    return generic_video_usage(request, result)


def custom_image_estimate(metering_backend: object | None, facts: ImageRequestFacts) -> Usage:
    """Custom-image estimates: use channel ``estimate_usage`` plus counts (:func:`with_image_counts`)
    when declared, otherwise generic measurements."""
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
    """Two DB-backed custom-model pricing inputs: model factors/bindings and endpoint metering declarations.

    ``endpoint_spec`` = ``None`` means unresolved model rows/endpoints, using generic metering.
    """

    pricing: CustomModelPricing
    endpoint_spec: EndpointSpec | None = None

    def video_usage(self, model: str, request: VideoGenerationRequest) -> Usage:
        """Video-request estimates through settlement-shared declarations.

        Missing request capabilities receive system + user overrides from execution ``CustomVideoBackend`` sources;
        unavailable system declarations leave capabilities absent. Channels identify models through corresponding entries,
        bindings first, identical to execution factory entries.
        """
        spec = self.endpoint_spec
        if spec is None:
            return custom_video_usage(None, model, request)
        from lib.custom_provider.model_facts import model_entry

        entry = model_entry(model, spec.media_type, self.pricing.entry_binding)
        if request.video_capabilities is None and self.pricing.endpoint:
            from lib.custom_provider.capabilities import synthesize_video_capabilities

            try:
                caps = synthesize_video_capabilities(
                    endpoint=self.pricing.endpoint,
                    model_id=model,
                    overrides=self.pricing.capability_overrides,
                    endpoint_spec=spec,
                    entry_binding=self.pricing.entry_binding,
                )
            except ValueError:
                logger.debug("自定义模型 %s 的视频能力判定不出，预估按不带能力计量", model, exc_info=True)
            else:
                request = replace(request, video_capabilities=caps)
        return custom_video_usage(
            video_metering_for(spec.metering_backend, spec.definition), model, request, entry=entry
        )

    def image_usage(self, facts: ImageRequestFacts) -> Usage:
        """Image-request estimates from matching entries, bindings first, identical to execution factory entries."""
        from lib.custom_provider.model_facts import model_entry

        facts = replace(facts, entry=model_entry(facts.model, "image", self.pricing.entry_binding))
        return custom_image_estimate(self.endpoint_spec.metering_backend if self.endpoint_spec else None, facts)

    def price(
        self, *, model: str, media_type: str, requests: Sequence[Usage], provider_multiplier: float, estimate: bool
    ) -> Cost:
        """Price matching entry tables × two multipliers; :func:`lib.billing.catalog_pricing.price_custom_call`."""
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
    """Read custom model pricing sources through settlement-shared reads; ``None`` for noncustom providers."""
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
