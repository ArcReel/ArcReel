import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.repository import ProviderConfigRepository, SystemSettingRepository
from lib.db.base import Base


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


# --- ProviderConfigRepository ---


async def test_set_and_get(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "AIza-test", is_secret=True)
    config = await repo.get_all("gemini-aistudio")
    assert config == {"api_key": "AIza-test"}


async def test_set_overwrites(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "old", is_secret=True)
    await repo.set("gemini-aistudio", "api_key", "new", is_secret=True)
    config = await repo.get_all("gemini-aistudio")
    assert config == {"api_key": "new"}


async def test_delete(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("grok", "api_key", "xai-test", is_secret=True)
    await repo.delete("grok", "api_key")
    config = await repo.get_all("grok")
    assert config == {}


async def test_get_secrets_masked(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "AIzaSyD-longkey123", is_secret=True)
    await repo.set("gemini-aistudio", "base_url", "https://example.com", is_secret=False)
    masked = await repo.get_all_masked("gemini-aistudio")
    assert masked["api_key"]["is_set"] is True
    assert "AIzaSyD" not in masked["api_key"]["masked"]  # value is masked
    assert masked["base_url"]["is_set"] is True
    assert masked["base_url"]["value"] == "https://example.com"


async def test_get_configured_keys(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("ark", "api_key", "ark-test", is_secret=True)
    keys = await repo.get_configured_keys("ark")
    assert keys == ["api_key"]


# --- SystemSettingRepository ---


async def test_setting_set_and_get(session: AsyncSession):
    repo = SystemSettingRepository(session)
    await repo.set("default_video_backend", "gemini-vertex/veo-3.1-fast-generate-001")
    val = await repo.get("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"


async def test_setting_get_default(session: AsyncSession):
    repo = SystemSettingRepository(session)
    val = await repo.get("nonexistent", default="fallback")
    assert val == "fallback"


async def test_setting_get_all(session: AsyncSession):
    repo = SystemSettingRepository(session)
    await repo.set("key1", "val1")
    await repo.set("key2", "val2")
    all_settings = await repo.get_all()
    assert all_settings == {"key1": "val1", "key2": "val2"}
