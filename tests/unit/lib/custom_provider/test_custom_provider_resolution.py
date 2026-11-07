"""Test CustomProviderModel.endpoint and resolver fail-loud behavior."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.mark.asyncio
async def test_resolution_column_accepts_none_and_string(db_session: AsyncSession):
    provider = CustomProvider(
        display_name="X",
        discovery_format="openai",
        base_url="https://api.x.ai",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    m_without = CustomProviderModel(
        provider_id=provider.id,
        model_id="m1",
        display_name="M1",
        endpoint="openai-images",
        is_default=False,
        is_enabled=True,
        resolution=None,
    )
    m_with = CustomProviderModel(
        provider_id=provider.id,
        model_id="m2",
        display_name="M2",
        endpoint="newapi-video",
        is_default=False,
        is_enabled=True,
        resolution="1080p",
    )
    db_session.add_all([m_without, m_with])
    await db_session.flush()

    assert m_without.resolution is None
    assert m_with.resolution == "1080p"


@pytest.mark.asyncio
async def test_endpoint_field_stores_openai_chat(db_session: AsyncSession):
    """Text models use the openai-chat endpoint."""
    provider = CustomProvider(
        display_name="TextProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="gpt-4o",
        display_name="GPT-4o",
        endpoint="openai-chat",
        is_default=True,
        is_enabled=True,
    )
    db_session.add(model)
    await db_session.flush()

    assert model.endpoint == "openai-chat"


@pytest.mark.asyncio
async def test_endpoint_field_stores_gemini_image(db_session: AsyncSession):
    """Google image models use the gemini-image endpoint."""
    provider = CustomProvider(
        display_name="GoogleProv",
        discovery_format="google",
        base_url="https://generativelanguage.googleapis.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="imagen-3.0-generate-002",
        display_name="Imagen 3",
        endpoint="gemini-image",
        is_default=False,
        is_enabled=True,
    )
    db_session.add(model)
    await db_session.flush()

    assert model.endpoint == "gemini-image"


@pytest.mark.asyncio
async def test_video_capabilities_endpoint_mismatch_raises(db_session: AsyncSession):
    """endpoint=openai-chat used as video_backend becomes a dangling reference and errors at task-type bucket resolution.

    Match execution semantics: a reference with mismatched media_type is no longer fulfillable. Ask users to reselect instead of silently substituting
    another provider model (``docs/adr/0054``)."""
    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError

    provider = CustomProvider(
        display_name="MismatchProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="gpt-4o",
        display_name="GPT-4o",
        endpoint="openai-chat",  # text endpoint, not video
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    from lib.custom_provider import make_provider_id

    provider_id_str = make_provider_id(provider.id)
    # project.json video_backend references this text-only model.
    project = {"video_backend": f"{provider_id_str}/gpt-4o"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)

    from lib.config.service import ConfigService

    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_id", "expected_max_refs", "generation_mode"),
    [
        # Explicit int: use endpoint cap directly, preserving behavior.
        ("openai-video", "vid-model", 1, "storyboard"),
        ("newapi-video", "vid-model", 0, "storyboard"),
        # Undeclared (None): query the backend pure caps function by model_id without backend construction.
        ("ark-seedance-2", "doubao-seedance-2-0", 9, "storyboard"),
        # Explicit entry rejection of references → 0 (HappyHorse 1.0 i2v); unmatched entries follow only channel limits (Ark unlimited; model unlisted).
        ("dashscope-always-on-video", "happyhorse-1.0-i2v", 0, "storyboard"),
        ("ark-seedance-2", "doubao-seedance-1-0", None, "storyboard"),
        ("vidu-video-q3-turbo", "viduq3-turbo", 7, "storyboard"),
        # MiniMax declarations: S2V-01 single-face reference → 1; Hailuo first-frame without references → 0. S2V-01 has no first-frame capability,
        # so resolves only in the r2v bucket for reference-to-video projects. Endpoint declarations set the limit; model name is not S2V-01,
        # which is delisted in the catalog and would otherwise produce a delisting error.
        ("minimax-s2v-01", "relay-s2v", 1, "reference_video"),
        ("minimax-hailuo-v1", "MiniMax-Hailuo-2.3", 0, "storyboard"),
    ],
)
async def test_custom_video_max_reference_images_from_endpoint(
    db_session: AsyncSession, endpoint: str, model_id: str, expected_max_refs: int | None, generation_mode: str
):
    """Custom video max_reference_images evaluates channel declarations (explicit ENDPOINT_REGISTRY int or backend pure caps
    function) and name-matched entries, taking the smaller limit; no declaration on either side means unlisted (docs/adr/0104).

    Performance regression guard: every path asserts no `CustomProviderRepository.get_provider` call.
    Old fallthrough performed another provider-row query and backend construction; pure-function resolution must avoid both."""
    from unittest.mock import patch

    from lib.config.resolver import ConfigResolver
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id=model_id,
        display_name="Vid Model",
        endpoint=endpoint,
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/{model_id}", "generation_mode": generation_mode}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with patch.object(CustomProviderRepository, "get_provider") as mock_get_provider:
        caps = await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    mock_get_provider.assert_not_called()
    assert caps["source"] == "custom"
    assert caps["max_reference_images"] == expected_max_refs


@pytest.mark.asyncio
async def test_custom_video_caps_resolved_without_api_key(db_session: AsyncSession):
    """ark-seedance-2 with endpoint cap=None still resolves limit (=9) with an empty api_key and does **not raise**.

    The old path constructed ArkVideoBackend and raised ValueError for empty keys, unlike lazily resolved Vidu;
    client-independent caps functions now allow both symmetrically."""
    from lib.config.resolver import ConfigResolver
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance-2",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    caps = await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert caps["max_reference_images"] == 9


@pytest.mark.asyncio
async def test_custom_video_max_refs_missing_caps_fn_raises(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """endpoint cap=None with missing video_caps_for_model (misconfiguration) becomes a dangling reference at task-type bucket resolution.

    EndpointSpec is a frozen dataclass: dataclasses.replace creates a malformed spec, temporarily substituted with setitem
    (module-load invariants apply only at import, not runtime monkeypatches)."""
    import dataclasses

    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    bad_spec = dataclasses.replace(
        ENDPOINT_REGISTRY["ark-seedance-2"], video_max_reference_images=None, video_caps_for_model=None
    )
    monkeypatch.setitem(ENDPOINT_REGISTRY, "ark-seedance-2", bad_spec)

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance-2",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"
    # Keep the specific cause in the exception chain for logs, outside user-facing error copy.
    assert "declares neither video_max_reference_images" in str(excinfo.value.__cause__)


@pytest.mark.asyncio
async def test_custom_video_max_refs_negative_caps_raises(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """endpoint cap=None with a negative caps-function result raises ValueError instead of silently forwarding invalid values."""
    import dataclasses

    from arcreel_market_core.video_backend_contract import VideoCapabilities
    from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    def _negative_caps(model: str, entry: object = None) -> VideoCapabilities:
        return VideoCapabilities(max_reference_images=-1)

    bad_spec = dataclasses.replace(ENDPOINT_REGISTRY["ark-seedance-2"], video_caps_for_model=_negative_caps)
    monkeypatch.setitem(ENDPOINT_REGISTRY, "ark-seedance-2", bad_spec)

    provider = CustomProvider(
        display_name="VideoProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="doubao-seedance-2-0",
        display_name="Vid Model",
        endpoint="ark-seedance-2",
        is_default=True,
        is_enabled=True,
        capability_overrides={"durations": [5, 10]},
    )
    db_session.add(model)
    await db_session.flush()

    provider_id_str = make_provider_id(provider.id)
    project = {"video_backend": f"{provider_id_str}/doubao-seedance-2-0"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(VideoBucketCapabilityError) as excinfo:
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
    assert excinfo.value.code == "video_capability_reference_unavailable"
    assert "invalid backend max_reference_images" in str(excinfo.value.__cause__)


async def _seed_image_provider(db_session: AsyncSession, *, endpoint: str, model_id: str) -> str:
    from lib.custom_provider import make_provider_id

    provider = CustomProvider(
        display_name="ImageProv",
        discovery_format="openai",
        base_url="https://api.example.com",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()
    db_session.add(
        CustomProviderModel(
            provider_id=provider.id,
            model_id=model_id,
            display_name=model_id,
            endpoint=endpoint,
            is_default=True,
            is_enabled=True,
        )
    )
    await db_session.flush()
    return make_provider_id(provider.id)


@pytest.mark.asyncio
async def test_image_bucket_gate_rejects_model_lacking_the_bucket(db_session: AsyncSession):
    """An i2i bucket referencing a t2i-only custom model errors during resolution, without silent substitution or deferral until execution."""
    from lib.config.resolver import ConfigResolver, ImageBucketCapabilityError

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-generations", model_id="t2i-m")
    project = {"image_provider_i2i": f"{provider_id_str}/t2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(ImageBucketCapabilityError) as excinfo:
        await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert excinfo.value.code == "image_capability_missing_i2i"
    assert excinfo.value.params == {"provider": provider_id_str, "model": "t2i-m"}


@pytest.mark.asyncio
async def test_image_bucket_gate_passes_model_declaring_the_bucket(db_session: AsyncSession):
    from lib.config.resolver import ConfigResolver

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-edits", model_id="i2i-m")
    project = {"image_provider_i2i": f"{provider_id_str}/i2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    resolved = await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert (resolved.provider_id, resolved.model_id) == (provider_id_str, "i2i-m")


@pytest.mark.asyncio
async def test_image_bucket_gate_leaves_disabled_reference_to_the_default_fallback(db_session: AsyncSession):
    """Disabled referenced models do not error at the resolution gate; leave them to bucket-filtered default fallback."""
    from lib.config.resolver import ConfigResolver

    provider_id_str = await _seed_image_provider(db_session, endpoint="openai-images-generations", model_id="t2i-m")
    model = (await db_session.execute(select(CustomProviderModel))).scalar_one()
    model.is_enabled = False
    await db_session.flush()
    project = {"image_provider_i2i": f"{provider_id_str}/t2i-m"}

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    resolved = await resolver.resolve_image_backend(project, None, generation_type="i2i")
    assert (resolved.provider_id, resolved.model_id) == (provider_id_str, "t2i-m")
