"""内置图片调用通道的预估用量：每条内置通道都能按请求事实推出用量，并能按目录价目表计价。"""

from __future__ import annotations

import pytest

from lib.backends.backend_assembly.specs import PROVIDER_SPEC_REGISTRY
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_usage import estimate_builtin_image_usage
from lib.billing.catalog_pricing import price_builtin_call
from lib.billing.metering import Priced
from lib.catalog import query as catalog

_IMAGE_PROVIDERS = sorted(provider for provider, media_type in PROVIDER_SPEC_REGISTRY if media_type == "image")


@pytest.mark.parametrize("provider_id", _IMAGE_PROVIDERS)
def test_every_builtin_image_channel_estimates_text_to_image(provider_id: str) -> None:
    model = catalog.default_model(provider_id, "image")
    assert model is not None
    # 通道不再替请求改档位，所以取目录里确实声明的档位（没声明分辨率的模型用 1K）。
    resolutions = catalog.get_model(provider_id, model, media_type="image").resolutions
    image_size = "1K" if not resolutions or "1K" in resolutions else resolutions[0]
    usage = estimate_builtin_image_usage(
        provider_id, ImageRequestFacts(model=model, aspect_ratio="9:16", image_size=image_size, reference_count=0)
    )
    cost = price_builtin_call(
        provider=provider_id, model=model, media_type="image", requests=(usage,), provider_multiplier=1.0, estimate=True
    )
    assert isinstance(cost, Priced), (provider_id, model, usage, cost)


def test_reference_surcharge_beyond_free_allowance() -> None:
    """Agnes 前 3 张参考图免费，之后每张 $0.003（价目表 free_allowance）。"""
    usage = estimate_builtin_image_usage(
        "agnes",
        ImageRequestFacts(model="agnes-image-2.5-flash", aspect_ratio="9:16", image_size="1K", reference_count=5),
    )
    cost = price_builtin_call(
        provider="agnes",
        model="agnes-image-2.5-flash",
        media_type="image",
        requests=(usage,),
        provider_multiplier=1.0,
        estimate=True,
    )
    assert cost == Priced(pytest.approx(0.01 + 2 * 0.003), "USD")


def test_unknown_reference_count_cannot_be_estimated_when_references_are_priced() -> None:
    usage = estimate_builtin_image_usage(
        "agnes",
        ImageRequestFacts(model="agnes-image-2.5-flash", aspect_ratio="9:16", image_size="1K", reference_count=None),
    )
    cost = price_builtin_call(
        provider="agnes",
        model="agnes-image-2.5-flash",
        media_type="image",
        requests=(usage,),
        provider_multiplier=1.0,
        estimate=True,
    )
    assert not isinstance(cost, Priced)


def test_provider_without_image_channel_raises() -> None:
    with pytest.raises(ValueError, match="no builtin ProviderSpec"):
        estimate_builtin_image_usage("anthropic", ImageRequestFacts(model="x", aspect_ratio="9:16"))
