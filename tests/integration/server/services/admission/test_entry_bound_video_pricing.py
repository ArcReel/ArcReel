"""Opaque API model IDs bound to catalog entries share official-entry estimates, quotes and settlement metering; outgoing requests still use original API IDs.

Exercise production configuration: real DB custom providers/credentials, ``ConfigResolver`` request facts/audio pricing parameters, ``assemble_backend`` (via ``create_custom_backend`` for custom providers), and DB-reading ``quote_video_request``. Never replace request facts or issue network requests."""

from __future__ import annotations

from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest, VideoGenerationResult
from lib.backends.backend_assembly.assembler import assemble_backend
from lib.backends.video_backends.registry import backend_metered_usage
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.custom_provider import make_provider_id
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.video_request_facts import (
    ExecutionVideoIdentity,
    VideoRequestCostFacts,
    VideoRequestFacts,
    evaluate_video_request_facts,
)
from server.services.admission.cost_estimation import quote_video_request

OPAQUE = "ep-20261103-opaque"
SEEDANCE_2 = "doubao-seedance-2-0-260128"
SEEDANCE_2_ENTRY = "bytedance/doubao-seedance-2.0"


@pytest.fixture(autouse=True)
def _reset_user_added_models():
    yield
    catalog.install_user_added_models(())


async def _custom_provider(db_factory, endpoint: str, models: list[dict]) -> str:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="中转",
            discovery_format="openai",
            base_url="https://relay.example.test",
            api_key="TEST-relay-key-0001",
            models=[{"display_name": m["model_id"], "endpoint": endpoint, **m} for m in models],
        )
        await session.commit()
        return make_provider_id(provider.id)


async def _facts(resolver: ConfigResolver, provider_id: str, model_id: str) -> VideoRequestFacts:
    project = {"generation_mode": "storyboard", "video_provider_i2v": f"{provider_id}/{model_id}"}
    facts = await evaluate_video_request_facts(
        project,
        route="storyboard",
        generation_type="i2v",
        identity=ExecutionVideoIdentity(provider_id, model_id),
        resolver=resolver,
    )
    assert isinstance(facts, VideoRequestFacts), facts
    return facts


def _sent_request(facts: VideoRequestFacts, **fields: object) -> VideoGenerationRequest:
    base: dict[str, object] = {
        "prompt": "p",
        "output_path": Path("out.mp4"),
        "aspect_ratio": "16:9",
        "duration_seconds": 5,
        "resolution": facts.resolution,
        "start_image": Path("storyboard.png"),
        "generate_audio": facts.requested_generate_audio,
    }
    base.update(fields)
    return VideoGenerationRequest(**base)


async def _quote_and_usage(db_factory, provider_id: str, model_id: str):
    """Derive model quotes, execution estimates and settlement metering entirely through production configuration."""
    resolver = ConfigResolver(db_factory)
    facts = await _facts(resolver, provider_id, model_id)
    quote = await quote_video_request(
        VideoRequestCostFacts(facts, 5, aspect_ratio="16:9", has_first_frame=True), db_factory
    )
    backend = await assemble_backend(provider_id=provider_id, media_type="video", model_id=model_id, resolver=resolver)
    request = _sent_request(facts)
    result = VideoGenerationResult(
        video_path=Path("out.mp4"), provider=provider_id, model=model_id, duration_seconds=5, usage_tokens=108_000
    )
    return quote, backend, backend_metered_usage(backend, request), backend_metered_usage(backend, request, result)


async def test_custom_opaque_seedance_id_is_quoted_and_metered_like_the_official_model(db_factory):
    """Custom opaque IDs bound to Seedance 2.0 use 2.0 pixel-table token estimates, report no service tier, and quote identically to official IDs."""
    provider_id = await _custom_provider(
        db_factory,
        "ark-seedance-2",
        [
            {"model_id": OPAQUE, "is_default": True, "entry_binding": SEEDANCE_2_ENTRY},
            {"model_id": SEEDANCE_2},
        ],
    )

    opaque_quote, backend, opaque_estimate, opaque_settled = await _quote_and_usage(db_factory, provider_id, OPAQUE)
    official_quote, _, official_estimate, official_settled = await _quote_and_usage(db_factory, provider_id, SEEDANCE_2)

    assert backend.model == OPAQUE
    # 5 × 1280 × 720 × 24 / 1024
    assert opaque_estimate.quantities["video_tokens"] == pytest.approx(108_000)
    assert "service_tier" not in opaque_estimate.dimensions
    assert opaque_estimate == official_estimate
    assert opaque_settled == official_settled
    assert opaque_quote.unpriced_reason is None
    assert (opaque_quote.amount, opaque_quote.currency) == (official_quote.amount, official_quote.currency)


async def test_custom_opaque_id_on_the_seedance_1_endpoint_keeps_the_conservative_metering(db_factory):
    """Unbound opaque IDs on 1.x endpoints (migration backfills pre-split classifications) have no video-token estimate, report request service tiers and quote unpriced."""
    provider_id = await _custom_provider(db_factory, "ark-seedance-1", [{"model_id": OPAQUE, "is_default": True}])

    quote, _, estimate, _ = await _quote_and_usage(db_factory, provider_id, OPAQUE)

    assert "video_tokens" not in estimate.quantities
    assert estimate.dimensions["service_tier"] == "standard"
    assert quote.amount is None


async def test_builtin_user_added_seedance_id_is_quoted_and_metered_like_the_official_model(db_factory):
    """User-added opaque IDs under built-in Ark bound to Seedance 2.0 share official-model quotes/settlement metering."""
    async with db_factory() as session:
        await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-inference-key-0001")
        await session.commit()
    catalog.install_user_added_models([catalog.UserAddedModel("ark", OPAQUE, "video", entry_binding=SEEDANCE_2_ENTRY)])

    opaque_quote, backend, opaque_estimate, opaque_settled = await _quote_and_usage(db_factory, "ark", OPAQUE)
    official_quote, _, official_estimate, official_settled = await _quote_and_usage(db_factory, "ark", SEEDANCE_2)

    assert backend.model == OPAQUE
    assert "service_tier" not in opaque_estimate.dimensions
    assert opaque_estimate == official_estimate
    assert opaque_settled == official_settled
    assert opaque_quote.unpriced_reason is None
    assert (opaque_quote.amount, opaque_quote.currency) == (official_quote.amount, official_quote.currency)


@pytest.mark.parametrize(("official", "generates_audio"), [("kling-v3", True), ("kling-v2-6", False)])
async def test_builtin_user_added_kling_id_takes_the_entry_audio_pricing_parameter(
    db_factory, official: str, generates_audio: bool
):
    """User-added opaque IDs under built-in Kling share default-tier audio pricing with official models when endpoint and entry bindings match.

    v3 audio is resolution-independent, so defaults price audio; v2-6 audio requires 1080P, so without tier context conservatively price silence."""
    catalog.install_user_added_models(
        [
            catalog.UserAddedModel(
                "kling",
                OPAQUE,
                "video",
                entry_binding=f"kuaishou/{official}",
                endpoint=catalog.provider_model_endpoint("kling", official, "video"),
            )
        ]
    )
    resolver = ConfigResolver(db_factory)

    opaque = await resolver.video_capabilities_for_model("kling", OPAQUE, generation_type="i2v")
    expected = await resolver.video_capabilities_for_model("kling", official, generation_type="i2v")

    assert opaque["generate_audio"] is expected["generate_audio"] is generates_audio


@pytest.mark.parametrize("model", [OPAQUE, "kling-v2-6"])
async def test_custom_kling_id_bound_to_v2_6_meters_720p_as_silent(db_factory, model: str):
    """Custom Kling opaque IDs bound to v2-6 narrow 720P requests to silence and report silent settlement metering, matching official IDs."""
    provider_id = await _custom_provider(
        db_factory,
        "kling-video-pro-audio",
        [{"model_id": model, "is_default": True, "entry_binding": "kuaishou/kling-v2-6" if model == OPAQUE else None}],
    )
    resolver = ConfigResolver(db_factory)
    facts = await _facts(resolver, provider_id, model)
    backend = await assemble_backend(provider_id=provider_id, media_type="video", model_id=model, resolver=resolver)
    request = _sent_request(
        facts, resolution="720p", generate_audio=True, video_capabilities=backend.video_capabilities_for_tier("std")
    )

    usage = backend_metered_usage(backend, request)

    assert backend.model == model
    assert usage is not None
    assert usage.dimensions["audio"] is False
    assert usage.dimensions["resolution"] == "720p"
