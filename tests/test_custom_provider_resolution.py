"""测试 CustomProviderModel.endpoint 字段及 resolver 的 fail-loud 行为。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    """text 模型使用 openai-chat endpoint。"""
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
    """Google 图像模型使用 gemini-image endpoint。"""
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
    """配 endpoint=openai-chat 但被当作 video_backend 使用 → ValueError。"""
    from lib.config.resolver import ConfigResolver

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
        supported_durations="[5, 10]",
    )
    db_session.add(model)
    await db_session.flush()

    from lib.custom_provider import make_provider_id

    provider_id_str = make_provider_id(provider.id)
    # project.json 中 video_backend 指向这个 text-only 模型
    project = {"video_backend": f"{provider_id_str}/gpt-4o"}

    factory = async_sessionmaker(bind=db_session.get_bind(), class_=AsyncSession, expire_on_commit=False)

    from lib.config.service import ConfigService

    svc = ConfigService(db_session)
    resolver = ConfigResolver(factory, _bound_session=db_session)

    with pytest.raises(ValueError, match="endpoint media_type mismatch"):
        await resolver._resolve_video_capabilities_from_project(svc, db_session, project)
