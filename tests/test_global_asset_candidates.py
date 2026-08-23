from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.db.repositories.asset_repo import AssetRepository
from lib.db.repositories.asset_resource_repo import AssetResourceRepository
from lib.image_reference_snapshot import FrozenImageReferences
from lib.project_manager import ProjectManager
from server.services import generation_tasks
from server.services.global_asset_candidates import register_linked_character_image_candidate

pytestmark = pytest.mark.integration


def _link_character(pm: ProjectManager, asset_id: str, *, usage: str = "reference") -> None:
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


async def test_generated_character_sheet_becomes_non_primary_global_candidate(
    tmp_path: Path,
    db_factory,
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    project_sheet = projects_root / "demo" / "characters" / "鳄鱼爸爸.png"
    project_sheet.write_bytes(b"new-project-main")
    pm.update_project_character_sheet("demo", "鳄鱼爸爸", "characters/鳄鱼爸爸.png")

    global_primary = projects_root / "_global_assets" / "character" / "primary.png"
    global_primary.parent.mkdir(parents=True)
    global_primary.write_bytes(b"global-primary")
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character",
            name="鳄鱼爸爸",
            image_path="_global_assets/character/primary.png",
        )
        await session.commit()
        asset_id = asset.id
    _link_character(pm, asset_id)

    registration = await register_linked_character_image_candidate(
        "demo",
        "鳄鱼爸爸",
        "characters/鳄鱼爸爸.png",
        manager=pm,
        session_factory=db_factory,
    )

    assert registration is not None and registration.created is True
    assert (projects_root / registration.path).read_bytes() == b"new-project-main"
    project_character = pm.load_project("demo")["characters"]["鳄鱼爸爸"]
    assert project_character["character_sheet"] == "characters/鳄鱼爸爸.png"
    assert project_character["global_asset_image_usage"] == "reference"
    async with db_factory() as session:
        refreshed = await AssetRepository(session).get_by_id(asset_id)
        assert refreshed is not None
        assert refreshed.image_path == "_global_assets/character/primary.png"
        assert {resource.path for resource in refreshed.resources} == {
            "_global_assets/character/primary.png",
            registration.path,
        }
        candidate = next(resource for resource in refreshed.resources if resource.id == registration.resource_id)
        assert candidate.origin == "local"
        assert candidate.media_type == "image"
        assert candidate.path != refreshed.image_path

    repeated = await register_linked_character_image_candidate(
        "demo",
        "鳄鱼爸爸",
        "characters/鳄鱼爸爸.png",
        manager=pm,
        session_factory=db_factory,
    )
    assert repeated is not None and repeated.created is False
    assert repeated.resource_id == registration.resource_id
    async with db_factory() as session:
        refreshed = await AssetRepository(session).get_by_id(asset_id)
        assert refreshed is not None
        assert len(refreshed.resources) == 2


async def test_unlinked_character_does_not_create_global_candidate(tmp_path: Path, db_factory) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    sheet = projects_root / "demo" / "characters" / "鳄鱼爸爸.png"
    sheet.write_bytes(b"project-main")

    assert (
        await register_linked_character_image_candidate(
            "demo",
            "鳄鱼爸爸",
            "characters/鳄鱼爸爸.png",
            manager=pm,
            session_factory=db_factory,
        )
        is None
    )
    assert list((pm.get_global_assets_root() / "character").iterdir()) == []


async def test_link_removed_during_registration_discards_candidate(tmp_path: Path, db_factory, monkeypatch) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    sheet = projects_root / "demo" / "characters" / "鳄鱼爸爸.png"
    sheet.write_bytes(b"project-main")
    primary = projects_root / "_global_assets" / "character" / "primary.png"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"primary")
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character",
            name="鳄鱼爸爸",
            image_path="_global_assets/character/primary.png",
        )
        await session.commit()
        asset_id = asset.id
    _link_character(pm, asset_id)

    original_load = pm.load_project
    load_count = 0

    def load_with_concurrent_unlink(project_name: str):
        nonlocal load_count
        load_count += 1
        if load_count == 2:

            def unlink(entry: dict) -> None:
                for field in ("global_asset_id", "matched_global_asset_id", "global_asset_image_usage"):
                    entry.pop(field, None)

            pm.update_asset_entry(
                "character",
                project_name,
                "鳄鱼爸爸",
                unlink,
            )
        return original_load(project_name)

    monkeypatch.setattr(pm, "load_project", load_with_concurrent_unlink)

    assert (
        await register_linked_character_image_candidate(
            "demo",
            "鳄鱼爸爸",
            "characters/鳄鱼爸爸.png",
            manager=pm,
            session_factory=db_factory,
        )
        is None
    )
    assert [path.name for path in (pm.get_global_assets_root() / "character").iterdir()] == ["primary.png"]
    async with db_factory() as session:
        refreshed = await AssetRepository(session).get_by_id(asset_id)
        assert refreshed is not None
        assert refreshed.resources == []


async def test_asset_sheet_task_registers_only_character_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    formal_result = {"resource_type": "characters", "resource_id": "鳄鱼爸爸"}
    monkeypatch.setattr(generation_tasks, "_run_formal_image_task", AsyncMock(return_value=formal_result))
    register = AsyncMock(return_value=None)
    monkeypatch.setattr(generation_tasks, "register_linked_character_image_candidate", register)
    frozen = FrozenImageReferences(None, (), None)

    result = await generation_tasks._run_asset_sheet_image_task(
        asset_type="character",
        project_name="demo",
        resource_id="鳄鱼爸爸",
        payload={},
        user_id="user",
        task_id="task-1",
        project={},
        full_prompt="prompt",
        frozen_references=frozen,
        basis=None,
    )

    assert result == formal_result
    register.assert_awaited_once_with("demo", "鳄鱼爸爸", "characters/鳄鱼爸爸.png")

    register.reset_mock(side_effect=True)
    register.side_effect = OSError("global asset storage unavailable")
    assert (
        await generation_tasks._run_asset_sheet_image_task(
            asset_type="character",
            project_name="demo",
            resource_id="鳄鱼爸爸",
            payload={},
            user_id="user",
            task_id="task-2",
            project={},
            full_prompt="prompt",
            frozen_references=frozen,
            basis=None,
        )
        == formal_result
    )

    register.reset_mock()
    register.side_effect = None
    await generation_tasks._run_asset_sheet_image_task(
        asset_type="scene",
        project_name="demo",
        resource_id="客厅",
        payload={},
        user_id="user",
        task_id="task-3",
        project={},
        full_prompt="prompt",
        frozen_references=frozen,
        basis=None,
    )
    register.assert_not_awaited()


async def test_generated_candidate_does_not_change_global_primary(tmp_path: Path, db_factory) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    sheet = projects_root / "demo" / "characters" / "鳄鱼爸爸.png"
    sheet.write_bytes(b"candidate")
    primary = projects_root / "_global_assets" / "character" / "primary.png"
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"primary")

    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character",
            name="鳄鱼爸爸",
            image_path="_global_assets/character/primary.png",
        )
        primary_resource = await AssetResourceRepository(session).create(
            asset_id=asset.id,
            resource_key="catalog:primary",
            origin="catalog",
            media_type="image",
            mime_type="image/png",
            path="_global_assets/character/primary.png",
            sort_order=0,
        )
        await session.commit()
        asset_id = asset.id
        primary_resource_id = primary_resource.id
    _link_character(pm, asset_id, usage="reference")

    registration = await register_linked_character_image_candidate(
        "demo",
        "鳄鱼爸爸",
        "characters/鳄鱼爸爸.png",
        manager=pm,
        session_factory=db_factory,
    )

    assert registration is not None
    async with db_factory() as session:
        refreshed = await AssetRepository(session).get_by_id(asset_id)
        assert refreshed is not None
        assert refreshed.image_path == "_global_assets/character/primary.png"
        assert next(resource for resource in refreshed.resources if resource.id == primary_resource_id).path == (
            refreshed.image_path
        )
        assert registration.path != refreshed.image_path
