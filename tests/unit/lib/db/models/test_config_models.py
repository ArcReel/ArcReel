import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.config import ProviderConfig, SystemSetting


async def test_provider_config_crud(db_session: AsyncSession):
    row = ProviderConfig(
        provider="gemini-aistudio",
        key="api_key",
        value="AIza-test",
        is_secret=True,
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(ProviderConfig).where(ProviderConfig.provider == "gemini-aistudio"))
    found = result.scalar_one()
    assert found.key == "api_key"
    assert found.value == "AIza-test"
    assert found.is_secret is True
    assert found.updated_at is not None


async def test_provider_config_unique_constraint(db_session: AsyncSession):
    row1 = ProviderConfig(provider="gemini-aistudio", key="api_key", value="v1", is_secret=True)
    row2 = ProviderConfig(provider="gemini-aistudio", key="api_key", value="v2", is_secret=True)
    db_session.add(row1)
    await db_session.flush()
    db_session.add(row2)
    with pytest.raises(Exception):  # IntegrityError
        await db_session.flush()


async def test_system_setting_crud(db_session: AsyncSession):
    row = SystemSetting(key="default_video_backend", value="gemini-vertex/veo-3.1-fast-generate-001")
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(select(SystemSetting).where(SystemSetting.key == "default_video_backend"))
    found = result.scalar_one()
    assert found.value == "gemini-vertex/veo-3.1-fast-generate-001"
