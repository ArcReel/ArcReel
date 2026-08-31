import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.migration import migrate_json_to_db
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository


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


async def test_migrate_provider_configs(db_session: AsyncSession, json_file: Path):
    await migrate_json_to_db(db_session, json_file)
    repo = ProviderConfigRepository(db_session)
    config = await repo.get_all("gemini-aistudio")
    assert config["api_key"] == "AIza-test-key"
    assert config["image_rpm"] == "15"
    config = await repo.get_all("ark")
    assert config["api_key"] == "ark-test-key"


async def test_migrate_system_settings(db_session: AsyncSession, json_file: Path):
    await migrate_json_to_db(db_session, json_file)
    repo = SystemSettingRepository(db_session)
    val = await repo.get("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"
    val = await repo.get("default_image_backend")
    assert val == "gemini-aistudio/gemini-3.1-flash-image-preview"
    val = await repo.get("anthropic_api_key")
    assert val == "sk-ant-test"


async def test_migrate_renames_file(db_session: AsyncSession, json_file: Path):
    await migrate_json_to_db(db_session, json_file)
    assert not json_file.exists()  # noqa: ASYNC240 -- 测试内本地小文件读写/断言，不在生产事件循环上
    assert json_file.with_suffix(".json.bak").exists()


async def test_migrate_max_workers_to_all_configured_providers(db_session: AsyncSession, json_file: Path):
    await migrate_json_to_db(db_session, json_file)
    repo = ProviderConfigRepository(db_session)
    ark = await repo.get_all("ark")
    assert ark.get("video_max_workers") == "2"
    grok = await repo.get_all("grok")
    assert "video_max_workers" not in grok


async def test_migrate_aistudio_001_to_preview(db_session: AsyncSession, tmp_path: Path):
    """AI Studio 的 001 后缀应迁移为 preview。"""
    data = {
        "overrides": {
            "video_backend": "aistudio",
            "video_model": "veo-3.1-generate-001",
        },
    }
    p = tmp_path / ".system_config.json"
    p.write_text(json.dumps(data))
    await migrate_json_to_db(db_session, p)
    repo = SystemSettingRepository(db_session)
    val = await repo.get("default_video_backend")
    assert val == "gemini-aistudio/veo-3.1-generate-preview"


async def test_migrate_noop_if_no_file(db_session: AsyncSession, tmp_path: Path):
    """源文件不存在：迁移直接返回，不抛错也不落任何配置行。"""
    nonexistent = tmp_path / ".system_config.json"

    assert await migrate_json_to_db(db_session, nonexistent) is None
    assert not nonexistent.exists()
    assert await ProviderConfigRepository(db_session).get_all_configs_bulk() == {}
    assert await SystemSettingRepository(db_session).get_all() == {}


# ---------------------------------------------------------------------------
# 旧任务级文本键 → 档位键迁移
# ---------------------------------------------------------------------------

from lib.config.migration import migrate_text_tier_settings


async def test_tier_migration_noop_when_no_legacy_keys(db_session: AsyncSession):
    await migrate_text_tier_settings(db_session)
    repo = SystemSettingRepository(db_session)
    assert await repo.get("text_backend_simple") == ""
    assert await repo.get("text_backend_complex") == ""


async def test_tier_migration_script_to_complex(db_session: AsyncSession):
    repo = SystemSettingRepository(db_session)
    await repo.set("text_backend_script", "gemini-aistudio/gemini-3.1-pro-preview")
    await migrate_text_tier_settings(db_session)
    assert await repo.get("text_backend_complex") == "gemini-aistudio/gemini-3.1-pro-preview"
    assert await repo.get("text_backend_simple") == ""
    assert await repo.get("text_backend_script") == ""


@pytest.mark.parametrize(
    ("overview", "style", "expected_simple"),
    [
        # 仅 overview
        ("gemini-aistudio/a", "", "gemini-aistudio/a"),
        # 仅 style
        ("", "gemini-aistudio/b", "gemini-aistudio/b"),
        # 两者相同
        ("gemini-aistudio/a", "gemini-aistudio/a", "gemini-aistudio/a"),
        # 两者不同：取 style 的值（保 vision）
        ("gemini-aistudio/a", "gemini-aistudio/b", "gemini-aistudio/b"),
    ],
)
async def test_tier_migration_simple_combinations(
    db_session: AsyncSession, overview: str, style: str, expected_simple: str
):
    repo = SystemSettingRepository(db_session)
    if overview:
        await repo.set("text_backend_overview", overview)
    if style:
        await repo.set("text_backend_style", style)
    await migrate_text_tier_settings(db_session)
    assert await repo.get("text_backend_simple") == expected_simple
    assert await repo.get("text_backend_overview") == ""
    assert await repo.get("text_backend_style") == ""


async def test_tier_migration_does_not_overwrite_existing_tier_keys(db_session: AsyncSession):
    repo = SystemSettingRepository(db_session)
    await repo.set("text_backend_script", "gemini-aistudio/old")
    await repo.set("text_backend_style", "gemini-aistudio/old-style")
    await repo.set("text_backend_complex", "gemini-aistudio/new")
    await repo.set("text_backend_simple", "gemini-aistudio/new-simple")
    await migrate_text_tier_settings(db_session)
    assert await repo.get("text_backend_complex") == "gemini-aistudio/new"
    assert await repo.get("text_backend_simple") == "gemini-aistudio/new-simple"
    assert await repo.get("text_backend_script") == ""


async def test_tier_migration_idempotent_on_rerun(db_session: AsyncSession):
    repo = SystemSettingRepository(db_session)
    await repo.set("text_backend_script", "gemini-aistudio/x")
    await migrate_text_tier_settings(db_session)
    # 第二次启动：旧键已删除，不再改写
    await repo.set("text_backend_complex", "gemini-aistudio/user-changed")
    await migrate_text_tier_settings(db_session)
    assert await repo.get("text_backend_complex") == "gemini-aistudio/user-changed"
