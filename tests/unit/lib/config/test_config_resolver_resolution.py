"""测试 project_resolution、自定义模型默认分辨率（经 VideoRuntime）与 constrain_durations。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import (
    ConfigResolver,
    constrain_durations,
    project_resolution,
)
from lib.custom_provider import make_provider_id
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.video_runtime.resolution import VideoRuntime


@pytest.fixture
async def resolver(db_session: AsyncSession) -> ConfigResolver:
    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    return ConfigResolver(factory, _bound_session=db_session)


async def _add_custom_video_model(db_session: AsyncSession, model_id: str, resolution: str | None) -> str:
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


def test_returns_none_when_nothing_configured():
    assert project_resolution({}, "gemini-aistudio", "veo-3.1-lite-generate-preview") is None


def test_legacy_only():
    project = {"video_model_settings": {"veo-3.1": {"resolution": "1080p"}}}
    assert project_resolution(project, "gemini-aistudio", "veo-3.1") == "1080p"


def test_model_settings_overrides_legacy():
    project = {
        "model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}},
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }
    assert project_resolution(project, "gemini-aistudio", "veo-3.1") == "720p"


def test_empty_string_override_treated_as_unset():
    project = {"model_settings": {"gemini-aistudio/m": {"resolution": ""}}}
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_composite_key_format_uses_slash():
    project = {"model_settings": {"gemini-aistudio/b": {"resolution": "4K"}}}
    assert project_resolution(project, "gemini-aistudio", "b") == "4K"


def test_tolerates_null_entries():
    project = {
        "model_settings": {"gemini-aistudio/b": None},
        "video_model_settings": {"m": None},
    }
    assert project_resolution(project, "gemini-aistudio", "b") is None
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_tolerates_top_level_field_as_string():
    project = {"model_settings": "oops", "video_model_settings": "also-broken"}
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_tolerates_top_level_field_as_list():
    project = {"model_settings": ["gemini-aistudio/m"], "video_model_settings": ["m"]}
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_tolerates_composite_key_entry_as_string():
    project = {"model_settings": {"gemini-aistudio/m": "1080p"}}
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_tolerates_legacy_model_entry_as_list():
    project = {"video_model_settings": {"m": ["1080p"]}}
    assert project_resolution(project, "gemini-aistudio", "m") is None


def test_dirty_model_settings_falls_through_to_legacy():
    project = {
        "model_settings": "oops",
        "video_model_settings": {"m": {"resolution": "1080p"}},
    }
    assert project_resolution(project, "gemini-aistudio", "m") == "1080p"


@pytest.mark.asyncio
async def test_returns_custom_default_when_only_custom(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "my-model", "720p")
    target = await VideoRuntime(resolver).default({"video_backend": f"{provider_id}/my-model"})
    assert (project_resolution({}, provider_id, "my-model") or target.snapshot.default_resolution) == "720p"


@pytest.mark.asyncio
async def test_custom_default_none_when_model_has_no_resolution(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "my-model", None)
    target = await VideoRuntime(resolver).default({"video_backend": f"{provider_id}/my-model"})
    assert (project_resolution({}, provider_id, "my-model") or target.snapshot.default_resolution) is None


@pytest.mark.asyncio
async def test_project_override_wins_over_custom_default(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "1K")
    project = {"video_backend": f"{provider_id}/m", "model_settings": {f"{provider_id}/m": {"resolution": "2K"}}}
    target = await VideoRuntime(resolver).default(project)
    assert (project_resolution(project, provider_id, "m") or target.snapshot.default_resolution) == "2K"


@pytest.mark.asyncio
async def test_legacy_wins_over_custom_default(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "720p")
    project = {"video_backend": f"{provider_id}/m", "video_model_settings": {"m": {"resolution": "1080p"}}}
    target = await VideoRuntime(resolver).default(project)
    assert (project_resolution(project, provider_id, "m") or target.snapshot.default_resolution) == "1080p"


@pytest.mark.asyncio
async def test_falls_through_to_custom_when_project_empty_string(resolver: ConfigResolver, db_session: AsyncSession):
    provider_id = await _add_custom_video_model(db_session, "m", "1K")
    project = {"video_backend": f"{provider_id}/m", "model_settings": {f"{provider_id}/m": {"resolution": ""}}}
    target = await VideoRuntime(resolver).default(project)
    assert (project_resolution(project, provider_id, "m") or target.snapshot.default_resolution) == "1K"


_VEO = ("gemini-aistudio", "veo-3.1-generate-preview")
_HAILUO = ("minimax", "MiniMax-Hailuo-2.3")


def test_constrain_durations_by_resolution():
    """已登记且有声明时按声明收窄，大小写不敏感。"""
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="4K") == [8]
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="1080p") == [8]


def test_constrain_durations_by_reference_images():
    """参考图约束独立于分辨率触发；未走参考图路径时不施加。"""
    assert constrain_durations(*_VEO, [4, 6, 8], uses_reference_images=True) == [8]
    assert constrain_durations(*_VEO, [4, 6, 8], uses_reference_images=False) == [4, 6, 8]
    assert constrain_durations(*_HAILUO, [6, 10], uses_reference_images=True) == [6, 10]


def test_constrain_durations_drops_vidu_r2v_ghost_tiers():
    """Vidu 参考生视频端点时长下限为 3 秒：走参考图路径时 1s / 2s 幽灵档位被剔除。"""
    full = list(range(1, 17))
    assert constrain_durations("vidu", "viduq3-turbo", full, uses_reference_images=True) == list(range(3, 17))
    assert constrain_durations("vidu", "viduq3-turbo", full, uses_reference_images=False) == full


def test_constrain_durations_both_dimensions_intersect():
    """两条约束同时生效时取交集。"""
    assert constrain_durations(*_HAILUO, [6, 10], resolution="1080p", uses_reference_images=True) == [6]


def test_constrain_durations_falls_back():
    """无声明 / 未登记型号 / 交集为空 / 缺参数时返回原候选，不把候选清空。"""
    assert constrain_durations(*_VEO, [4, 6, 8], resolution="720p") == [4, 6, 8]
    assert constrain_durations("unknown", "unknown-model", [5, 10], resolution="720p") == [5, 10]
    assert constrain_durations(*_VEO, [], resolution="4K") == []
    assert constrain_durations(None, None, [4, 6, 8], resolution="4K") == [4, 6, 8]
