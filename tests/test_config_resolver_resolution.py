"""测试 ConfigResolver.resolve_resolution 与模块级 get_provider_fallback。

resolve_resolution 走公开接口（不断言私有函数），按
project.model_settings → legacy video_model_settings → 自定义供应商默认 → None 解析；
自定义供应商默认路径用真实内存 DB + 真实 CustomProviderModel 断言。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.resolver import ConfigResolver, get_provider_fallback
from lib.custom_provider import make_provider_id
from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def resolver(db_session: AsyncSession) -> ConfigResolver:
    factory = async_sessionmaker(bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
    return ConfigResolver(factory, _bound_session=db_session)


async def _add_custom_video_model(db_session: AsyncSession, model_id: str, resolution: str | None) -> str:
    """建一个带指定 resolution 的自定义视频 model，返回其 registry provider_id 字符串。"""
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


# --- 纯项目字典优先级（非自定义 provider，DB 默认恒 None） ---


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
    # project.json 可能被手编为 null 值；既不应崩也不应当作已配置。
    project = {
        "model_settings": {"gemini-aistudio/b": None},
        "video_model_settings": {"m": None},
    }
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "b") is None
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_top_level_field_as_string(resolver: ConfigResolver):
    # 手编脏数据：model_settings / video_model_settings 顶层本身被写成字符串。
    project = {"model_settings": "oops", "video_model_settings": "also-broken"}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_top_level_field_as_list(resolver: ConfigResolver):
    # 手编脏数据：model_settings / video_model_settings 顶层本身被写成列表。
    project = {"model_settings": ["gemini-aistudio/m"], "video_model_settings": ["m"]}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_composite_key_entry_as_string(resolver: ConfigResolver):
    # 手编脏数据：model_settings 里具体某个复合 key 的 entry 被写成字符串而非 dict。
    project = {"model_settings": {"gemini-aistudio/m": "1080p"}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_tolerates_legacy_model_entry_as_list(resolver: ConfigResolver):
    # 手编脏数据：legacy video_model_settings 里具体某个 model 的 entry 被写成列表。
    project = {"video_model_settings": {"m": ["1080p"]}}
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") is None


@pytest.mark.asyncio
async def test_dirty_model_settings_falls_through_to_legacy(resolver: ConfigResolver):
    # model_settings 顶层脏数据不应连坐拖垮 legacy 兜底路径的正常解析。
    project = {
        "model_settings": "oops",
        "video_model_settings": {"m": {"resolution": "1080p"}},
    }
    assert await resolver.resolve_resolution(project, "gemini-aistudio", "m") == "1080p"


# --- 自定义供应商默认（真实 DB） ---


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


# --- get_provider_fallback（纯查表，不触 DB） ---


@pytest.mark.parametrize(
    "provider_id, expected",
    [
        ("gemini", "1080p"),
        ("gemini-aistudio", "1080p"),  # 短前缀归一化
        ("ark", "720p"),
        ("grok", "720p"),
        ("openai", "720p"),
        ("minimax", "768p"),
        ("minimax-hailuo", "768p"),
        ("unknown-provider", "1080p"),  # 未知 → default
        (None, "1080p"),  # None → default
    ],
)
def test_get_provider_fallback(provider_id: str | None, expected: str):
    assert get_provider_fallback(provider_id) == expected


def test_get_provider_fallback_custom_default():
    assert get_provider_fallback("unknown", default="720p") == "720p"
