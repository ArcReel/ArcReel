"""ConfigResolver.resolve_resolution tests.

Use public behavior, not private assertions: project.model_settings → legacy video_model_settings
→ custom-provider default → None. Custom defaults use real in-memory DB and CustomProviderModel.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import (
    ConfigResolver,
    constrain_durations,
)
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.fixture
async def resolver(db_session: AsyncSession) -> ConfigResolver:
    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    return ConfigResolver(factory, _bound_session=db_session)


async def _add_custom_video_model(db_session: AsyncSession, model_id: str, resolution: str | None) -> str:
    """Create a custom video model with resolution, returning its registry provider_id string."""
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
        endpoint="newapi-video",
        is_default=True,
        is_enabled=True,
        resolution=resolution,
    )
    db_session.add(model)
    await db_session.flush()
    return make_provider_id(provider.id)


# --- Pure project-dictionary precedence; noncustom providers always have no DB default ---


@pytest.mark.asyncio
async def test_returns_none_when_nothing_configured(resolver: ConfigResolver):
    assert await resolver.resolve_resolution({}, "gemini-aistudio", "veo-3.1-lite-generate-preview") is None


@pytest.mark.asyncio
async def test_legacy_only(resolver: ConfigResolver):
    project = {"video_model_settings": {"veo-3.1": {"resolution": "1080p"}}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "1080p"


@pytest.mark.asyncio
async def test_model_settings_overrides_legacy(resolver: ConfigResolver):
    project = {
        "model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}},
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "720p"


@pytest.mark.asyncio
async def test_empty_string_override_treated_as_unset(resolver: ConfigResolver):
    project = {"model_settings": {"gemini-aistudio/m": {"resolution": ""}}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_composite_key_format_uses_slash(resolver: ConfigResolver):
    project = {"model_settings": {"gemini-aistudio/b": {"resolution": "4K"}}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "b") == "4K"


@pytest.mark.asyncio
async def test_tolerates_null_entries(resolver: ConfigResolver):
    # Hand-edited project.json may contain null; neither crash nor treat it as configured.
    project = {
        "model_settings": {"gemini-aistudio/b": None},
        "video_model_settings": {"m": None},
    }
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "b") is None
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_top_level_field_as_string(resolver: ConfigResolver):
    # Invalid hand edits: model_settings / video_model_settings top level is a string.
    project = {"model_settings": "oops", "video_model_settings": "also-broken"}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_top_level_field_as_list(resolver: ConfigResolver):
    # Invalid hand edits: model_settings / video_model_settings top level is a list.
    project = {"model_settings": ["gemini-aistudio/m"], "video_model_settings": ["m"]}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_composite_key_entry_as_string(resolver: ConfigResolver):
    # Invalid hand edits: one composite-key model_settings entry is a string instead of dict.
    project = {"model_settings": {"gemini-aistudio/m": "1080p"}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_legacy_model_entry_as_list(resolver: ConfigResolver):
    # Invalid hand edits: one legacy video_model_settings model entry is a list.
    project = {"video_model_settings": {"m": ["1080p"]}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_dirty_model_settings_falls_through_to_legacy(resolver: ConfigResolver):
    # Invalid top-level model_settings must not break valid legacy fallback resolution.
    project = {
        "model_settings": "oops",
        "video_model_settings": {"m": {"resolution": "1080p"}},
    }
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") == "1080p"


# --- Custom-provider defaults, real DB ---


@pytest.mark.asyncio
async def test_returns_custom_default_when_only_custom(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "my-model", "720p")
    assert await resolver.resolve_resolution({}, provider_id, "my-model") == "720p"


@pytest.mark.asyncio
async def test_custom_default_none_when_model_has_no_resolution(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "my-model", None)
    assert await resolver.resolve_resolution({}, provider_id, "my-model") is None


@pytest.mark.asyncio
async def test_custom_default_none_when_model_missing(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "my-model", "720p")
    assert await resolver.resolve_resolution({}, provider_id, "other-model") is None


@pytest.mark.asyncio
async def test_project_override_wins_over_custom_default(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "1K")
    project = {"model_settings": {f"{provider_id}/m": {"resolution": "2K"}}}
    assert await resolver.resolve_resolution(project, provider_id, "m") == "2K"


@pytest.mark.asyncio
async def test_legacy_wins_over_custom_default(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "720p")
    project = {"video_model_settings": {"m": {"resolution": "1080p"}}}
    assert await resolver.resolve_resolution(project, provider_id, "m") == "1080p"


@pytest.mark.asyncio
async def test_falls_through_to_custom_when_project_empty_string(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "1K")
    project = {"model_settings": {f"{provider_id}/m": {"resolution": ""}}}
    assert await resolver.resolve_resolution(project, provider_id, "m") == "1K"


# ---------------------------------------------------------------------------
# Duration coupling constraints.
# ---------------------------------------------------------------------------

_VEO = ("gemini-aistudio", "veo-3.1-generate-preview")  # Declare {1080p:[8], 4k:[8]} plus reference [8].
_HAILUO = ("minimax", "MiniMax-Hailuo-2.3")  # Declare {1080p:[6]}, without reference declaration.


def test_constrain_durations_by_resolution():
    """Registered declarations narrow options case-insensitively."""
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="4K") == [8]
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="1080p") == [8]


def test_constrain_durations_by_reference_images():
    """Reference constraints trigger independently of resolution, only on reference paths."""
    assert constrain_durations(*_VEO, [4, 6, 8], uses_reference_images=True) == [8]
    assert constrain_durations(*_VEO, [4, 6, 8], uses_reference_images=False) == [4, 6, 8]
    # Model without reference declaration: no narrowing on that dimension.
    assert constrain_durations(*_HAILUO, [6, 10], uses_reference_images=True) == [6, 10]


def test_constrain_durations_drops_vidu_r2v_ghost_tiers():
    """Vidu reference-to-video minimum is 3 seconds; remove phantom 1s / 2s options on reference paths.

    Otherwise users select 1s, backend silently chooses 3 seconds after submission, and bills 3 seconds.
    """
    full = list(range(1, 17))
    assert constrain_durations("vidu", "viduq3-turbo", full, uses_reference_images=True) == list(range(3, 17))
    # Nonreference text/image paths remain unaffected; 1s / 2s are valid there.
    assert constrain_durations("vidu", "viduq3-turbo", full, uses_reference_images=False) == full


def test_constrain_durations_both_dimensions_intersect():
    """Intersect both simultaneously applicable constraints."""
    assert constrain_durations(*_HAILUO, [6, 10], resolution="1080p", uses_reference_images=True) == [6]


def test_constrain_durations_falls_back():
    """Absent declarations/unregistered models/empty intersections/missing arguments retain original candidates, never clearing them."""
    # No declaration for this resolution.
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="720p") == [4, 6, 8]
    # Unregistered model, e.g. relay/custom-provider wrapper.
    assert constrain_durations("gemini-aistudio", "veo-3.1-via-relay", [4, 6, 8], resolution="4k") == [4, 6, 8]
    # Preserve empty intersections for fact consumers to handle.
    assert constrain_durations(*_VEO, [4, 6], resolution="4k") == []
    assert constrain_durations(*_VEO, [4, 6], uses_reference_images=True) == []
    # Missing resolution without reference path triggers neither dimension.
    assert constrain_durations(*_VEO, [4, 6, 8]) == [4, 6, 8]
    # Missing identity, capabilities unresolvable.
    assert constrain_durations(None, None, [4, 6, 8], resolution="4k") == [4, 6, 8]
    # Empty candidates return unchanged.
    assert constrain_durations(*_VEO, [], resolution="4k") == []
