import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.db.base import Base
from lib.config.service import ConfigService


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def config_service(session: AsyncSession) -> ConfigService:
    return ConfigService(session)


async def test_get_all_providers_status_empty(config_service: ConfigService):
    statuses = await config_service.get_all_providers_status()
    assert len(statuses) == 4
    for s in statuses:
        assert s.status == "unconfigured"


async def test_provider_becomes_ready(config_service: ConfigService):
    await config_service.set_provider_config("gemini-aistudio", "api_key", "AIza-test")
    statuses = await config_service.get_all_providers_status()
    aistudio = next(s for s in statuses if s.name == "gemini-aistudio")
    assert aistudio.status == "ready"
    assert "api_key" in aistudio.configured_keys
    assert aistudio.missing_keys == []


async def test_get_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    config = await config_service.get_provider_config("grok")
    assert config == {"api_key": "xai-test"}


async def test_delete_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    await config_service.delete_provider_config("grok", "api_key")
    config = await config_service.get_provider_config("grok")
    assert config == {}


async def test_system_settings(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "gemini-vertex/veo-3.1-fast-generate-001")
    val = await config_service.get_setting("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"


async def test_get_default_video_backend(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "seedance/doubao-seedance-1-5-pro-251215")
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "seedance"
    assert model_id == "doubao-seedance-1-5-pro-251215"


async def test_get_default_backend_fallback(config_service: ConfigService):
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "gemini-aistudio"


async def test_unknown_provider_raises(config_service: ConfigService):
    with pytest.raises(ValueError, match="Unknown provider"):
        await config_service.set_provider_config("unknown-provider", "key", "val")
