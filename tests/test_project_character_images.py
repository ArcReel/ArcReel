from __future__ import annotations

from pathlib import Path

import pytest

from lib.db.repositories.asset_repo import AssetRepository
from lib.project_manager import ProjectManager
from server.services.project_character_images import (
    ProjectCharacterMainImageMissing,
    move_character_main_to_reference,
)

pytestmark = pytest.mark.integration


def _project(pm: ProjectManager, *, sheet: bytes | None = None) -> None:
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    if sheet is not None:
        path = pm.get_project_path("demo") / "characters" / "鳄鱼爸爸.png"
        path.write_bytes(sheet)
        pm.update_project_character_sheet("demo", "鳄鱼爸爸", "characters/鳄鱼爸爸.png")


async def _global_character(projects_root: Path, db_factory, image: bytes) -> tuple[str, str]:
    relative = "_global_assets/character/dad.png"
    path = projects_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character",
            name="鳄鱼爸爸",
            image_path=relative,
        )
        await session.commit()
        return asset.id, relative


def _link(pm: ProjectManager, asset_id: str, *, usage: str) -> None:
    pm.update_asset_entry(
        "character",
        "demo",
        "鳄鱼爸爸",
        lambda entry: entry.update(
            global_asset_id=asset_id,
            matched_global_asset_id=asset_id,
            global_asset_image_usage=usage,
        ),
    )


async def test_linked_global_main_moves_to_reference_and_clears_duplicate_local_sheet(
    tmp_path: Path,
    db_factory,
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    _project(pm, sheet=b"same-image")
    asset_id, global_path = await _global_character(projects_root, db_factory, b"same-image")
    _link(pm, asset_id, usage="main")

    result = await move_character_main_to_reference(
        "demo",
        "鳄鱼爸爸",
        manager=pm,
        session_factory=db_factory,
    )

    assert result.source == "global"
    assert (pm.get_project_path("demo") / result.reference_path).read_bytes() == b"same-image"
    character = pm.load_project("demo")["characters"]["鳄鱼爸爸"]
    assert character["character_sheet"] == ""
    assert character["reference_image"] == result.reference_path
    assert character["global_asset_image_usage"] == "reference"
    assert (projects_root / global_path).read_bytes() == b"same-image"
    assert (pm.get_project_path("demo") / "characters" / "鳄鱼爸爸.png").exists()


async def test_generated_project_main_replaces_reference_without_changing_global_primary(
    tmp_path: Path,
    db_factory,
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    _project(pm, sheet=b"project-main-b")
    asset_id, global_path = await _global_character(projects_root, db_factory, b"global-main-a")
    _link(pm, asset_id, usage="reference")
    old_reference = pm.get_project_path("demo") / "characters" / "refs" / "鳄鱼爸爸.png"
    old_reference.parent.mkdir(parents=True)
    old_reference.write_bytes(b"global-main-a")
    pm.update_character_reference_image("demo", "鳄鱼爸爸", "characters/refs/鳄鱼爸爸.png")

    result = await move_character_main_to_reference(
        "demo",
        "鳄鱼爸爸",
        manager=pm,
        session_factory=db_factory,
    )

    assert result.source == "project"
    assert old_reference.read_bytes() == b"project-main-b"
    character = pm.load_project("demo")["characters"]["鳄鱼爸爸"]
    assert character["character_sheet"] == ""
    assert character["reference_image"] == "characters/refs/鳄鱼爸爸.png"
    assert character["global_asset_image_usage"] == "reference"
    async with db_factory() as session:
        asset = await AssetRepository(session).get_by_id(asset_id)
        assert asset is not None and asset.image_path == global_path
    assert (projects_root / global_path).read_bytes() == b"global-main-a"


async def test_unlinked_project_main_can_move_to_reference(tmp_path: Path, db_factory) -> None:
    pm = ProjectManager(tmp_path / "projects")
    _project(pm, sheet=b"project-main")

    result = await move_character_main_to_reference(
        "demo",
        "鳄鱼爸爸",
        manager=pm,
        session_factory=db_factory,
    )

    assert result.source == "project"
    character = pm.load_project("demo")["characters"]["鳄鱼爸爸"]
    assert character["character_sheet"] == ""
    assert (pm.get_project_path("demo") / character["reference_image"]).read_bytes() == b"project-main"
    assert "global_asset_image_usage" not in character


async def test_move_rejects_character_without_current_main(tmp_path: Path, db_factory) -> None:
    pm = ProjectManager(tmp_path / "projects")
    _project(pm)

    with pytest.raises(ProjectCharacterMainImageMissing):
        await move_character_main_to_reference(
            "demo",
            "鳄鱼爸爸",
            manager=pm,
            session_factory=db_factory,
        )
