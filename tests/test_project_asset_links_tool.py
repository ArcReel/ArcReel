import json
from pathlib import Path

import pytest

from lib.db.repositories.asset_repo import AssetRepository
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.project_asset_links import manage_project_asset_link_tool

pytestmark = pytest.mark.integration


async def test_agent_can_link_configure_and_unlink_project_asset(
    tmp_path: Path, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(str(projects_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "项目角色", "沉稳")
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character", name="鳄鱼爸爸", audio_path="_global_assets/character/dad.wav", voice_id="dad-tts"
        )
        await session.commit()
        asset_id = asset.id
    monkeypatch.setattr("server.services.project_asset_links.async_session_factory", db_factory)
    tool = manage_project_asset_link_tool(ToolContext(project_name="demo", projects_root=projects_root, pm=pm))

    linked = await tool.handler(
        {"action": "link", "resource_type": "character", "resource_id": "鳄鱼爸爸", "asset_id": asset_id}
    )
    assert "is_error" not in linked
    assert pm.load_project("demo")["characters"]["鳄鱼爸爸"]["global_asset_voice_source"] == "reference_audio"

    configured = await tool.handler(
        {
            "action": "configure",
            "resource_type": "character",
            "resource_id": "鳄鱼爸爸",
            "voice_source": "voice_id",
        }
    )
    assert "is_error" not in configured
    entry = json.loads(configured["content"][0]["text"])["project_asset"]
    assert entry["global_asset_image_usage"] == "main"
    assert entry["global_asset_voice_source"] == "voice_id"

    unlinked = await tool.handler({"action": "unlink", "resource_type": "character", "resource_id": "鳄鱼爸爸"})
    assert "is_error" not in unlinked
    assert "global_asset_id" not in pm.load_project("demo")["characters"]["鳄鱼爸爸"]
