"""内置图片调用通道的预估用量：每条内置通道都能按请求事实推出用量，并能按目录价目表计价。"""

from __future__ import annotations

import pytest

from arcreel_catalog_core import ScaledOutputTokenBase
from lib.backends.backend_assembly.specs import PROVIDER_SPEC_REGISTRY
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_usage import estimate_builtin_image_usage
from lib.billing.catalog_pricing import price_builtin_call
from lib.billing.metering import Priced
from lib.catalog import query as catalog
from tests.catalog_support import install_catalog, with_entry_facts

_IMAGE_PROVIDERS = sorted(provider for provider, media_type in PROVIDER_SPEC_REGISTRY if media_type == "image")


@pytest.mark.parametrize("provider_id", _IMAGE_PROVIDERS)
def test_every_builtin_image_channel_estimates_text_to_image(provider_id: str) -> None:
    model = next(
        key for key, info in catalog.list_models(provider_id).items() if info.media_type == "image" and not info.hidden
    )
    assert model is not None
    # 通道原样下发档位，所以取目录里确实声明的档位（没声明分辨率的模型用 1K）。
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


def test_output_tokens_are_estimated_from_the_bases_on_the_model_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """内置 OpenAI 的输出 token 估算读供应商模型所引条目上的质量档基数；条目没收录时不估算。"""
    facts = ImageRequestFacts(model="gpt-image-2", aspect_ratio="1:1", image_size="1K", reference_count=0)
    snapshot = catalog.factory_snapshot()

    install_catalog(monkeypatch, with_entry_facts(snapshot, "openai", "gpt-image-2", output_token_bases={}))
    assert "image_output_tokens" not in estimate_builtin_image_usage("openai", facts).quantities

    bases = {"low": 16, "medium": 48, "high": 96}
    install_catalog(monkeypatch, with_entry_facts(snapshot, "openai", "gpt-image-2", output_token_bases=bases))
    # 1K → medium、1:1 → 1024x1024：官方计算器 1756 个输出 token
    assert estimate_builtin_image_usage("openai", facts).quantities["image_output_tokens"] == 1756


def test_output_tokens_are_estimated_from_scaled_bases_on_the_model_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """带系数基数走同一条估算路径；官方计算器 1024x1024、medium（GPT Image 2.6）为 1449。"""
    facts = ImageRequestFacts(model="gpt-image-2", aspect_ratio="1:1", image_size="1K", reference_count=0)
    scaled = {"medium": ScaledOutputTokenBase(base=24, multiplier=3.3)}
    install_catalog(
        monkeypatch,
        with_entry_facts(
            catalog.factory_snapshot(), "openai", "gpt-image-2", output_token_bases={}, output_token_scaled_bases=scaled
        ),
    )

    assert estimate_builtin_image_usage("openai", facts).quantities["image_output_tokens"] == 1449


@pytest.mark.parametrize(
    ("provider", "model", "count"),
    [("grok", "grok-imagine-image", 8), ("vidu", "viduq2", 9), ("minimax", "image-01", 3)],
)
def test_estimate_keeps_reference_counts_above_the_channel_limit(provider: str, model: str, count: int) -> None:
    usage = estimate_builtin_image_usage(
        provider, ImageRequestFacts(model=model, aspect_ratio="1:1", reference_count=count)
    )
    assert usage.quantities["input_images"] == count
