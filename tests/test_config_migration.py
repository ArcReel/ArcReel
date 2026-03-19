import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.db.base import Base
from lib.config.migration import migrate_json_to_db
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository


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
def json_file(tmp_path: Path) -> Path:
    data = {
        "version": 1,
        "overrides": {
            "gemini_api_key": "AIza-test-key",
            "video_backend": "vertex",
            "image_backend": "aistudio",
            "video_model": "veo-3.1-fast-generate-001",
            "image_model": "gemini-3.1-flash-image-preview",
            "video_generate_audio": False,
            "anthropic_api_key": "sk-ant-test",
            "anthropic_base_url": "https://proxy.example.com",
            "gemini_image_rpm": 15,
            "gemini_video_rpm": 10,
            "gemini_request_gap": 3.1,
            "image_max_workers": 3,
            "video_max_workers": 2,
            "ark_api_key": "ark-test-key",
        },
    }
    p = tmp_path / ".system_config.json"
    p.write_text(json.dumps(data))
    return p


async def test_migrate_provider_configs(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    repo = ProviderConfigRepository(session)
    config = await repo.get_all("gemini-aistudio")
    assert config["api_key"] == "AIza-test-key"
    assert config["image_rpm"] == "15"
    config = await repo.get_all("seedance")
    assert config["api_key"] == "ark-test-key"


async def test_migrate_system_settings(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    repo = SystemSettingRepository(session)
    val = await repo.get("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"
    val = await repo.get("default_image_backend")
    assert val == "gemini-aistudio/gemini-3.1-flash-image-preview"
    val = await repo.get("anthropic_api_key")
    assert val == "sk-ant-test"


async def test_migrate_renames_file(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    assert not json_file.exists()
    assert json_file.with_suffix(".json.bak").exists()


async def test_migrate_max_workers_to_all_configured_providers(
    session: AsyncSession, json_file: Path
):
    await migrate_json_to_db(session, json_file)
    repo = ProviderConfigRepository(session)
    seedance = await repo.get_all("seedance")
    assert seedance.get("video_max_workers") == "2"
    grok = await repo.get_all("grok")
    assert "video_max_workers" not in grok


async def test_migrate_aistudio_001_to_preview(session: AsyncSession, tmp_path: Path):
    """AI Studio 的 001 后缀应迁移为 preview。"""
    data = {
        "overrides": {
            "video_backend": "aistudio",
            "video_model": "veo-3.1-generate-001",
        },
    }
    p = tmp_path / ".system_config.json"
    p.write_text(json.dumps(data))
    await migrate_json_to_db(session, p)
    repo = SystemSettingRepository(session)
    val = await repo.get("default_video_backend")
    assert val == "gemini-aistudio/veo-3.1-generate-preview"


async def test_migrate_noop_if_no_file(session: AsyncSession, tmp_path: Path):
    nonexistent = tmp_path / ".system_config.json"
    await migrate_json_to_db(session, nonexistent)  # should not raise
