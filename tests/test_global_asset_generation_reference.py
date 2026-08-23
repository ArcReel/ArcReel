from pathlib import Path

import pytest

from lib.db.repositories.asset_repo import AssetRepository
from lib.project_manager import ProjectManager
from server.services import generation_tasks
from server.services.effective_global_assets import resolve_linked_global_reference_audio_paths

pytestmark = pytest.mark.integration


async def test_linked_global_image_is_generation_reference_only_in_reference_mode(
    tmp_path: Path, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(str(projects_root))
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_scene("demo", "客厅", "暖色客厅")
    image_path = projects_root / "_global_assets" / "scene" / "living-room.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="scene", name="客厅", image_path="_global_assets/scene/living-room.png"
        )
        await session.commit()
        asset_id = asset.id

    def set_mode(mode: str) -> None:
        pm.update_asset_entry(
            "scene",
            "demo",
            "客厅",
            lambda entry: entry.update(
                global_asset_id=asset_id,
                matched_global_asset_id=asset_id,
                global_asset_image_usage=mode,
            ),
        )

    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "async_session_factory", db_factory)
    set_mode("main")
    assert await generation_tasks._resolve_linked_global_image_reference("demo", "scene", "客厅") is None
    set_mode("reference")
    assert await generation_tasks._resolve_linked_global_image_reference("demo", "scene", "客厅") == image_path


async def test_selected_global_reference_audio_enters_generation_inputs(tmp_path: Path, db_factory) -> None:
    projects_root = tmp_path / "projects"
    audio_path = projects_root / "_global_assets" / "character" / "dad.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    async with db_factory() as session:
        asset = await AssetRepository(session).create(
            type="character", name="鳄鱼爸爸", audio_path="_global_assets/character/dad.wav"
        )
        await session.commit()
        asset_id = asset.id
    project = {
        "characters": {
            "鳄鱼爸爸": {
                "description": "角色",
                "global_asset_id": asset_id,
                "global_asset_voice_source": "reference_audio",
            }
        }
    }
    assert await resolve_linked_global_reference_audio_paths(project, projects_root, session_factory=db_factory) == {
        "鳄鱼爸爸": audio_path
    }


async def test_project_reference_snapshot_wins_after_generated_main_moves_over_linked_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo")
    pm.add_project_character("demo", "鳄鱼爸爸", "角色")
    project_reference = pm.get_project_path("demo") / "characters" / "refs" / "鳄鱼爸爸.png"
    project_reference.parent.mkdir(parents=True)
    project_reference.write_bytes(b"project-reference-b")
    pm.update_character_reference_image("demo", "鳄鱼爸爸", "characters/refs/鳄鱼爸爸.png")
    global_reference = tmp_path / "global-a.png"
    global_reference.write_bytes(b"global-reference-a")
    captured: list[bytes] = []

    async def _run(**kwargs):
        frozen = kwargs["frozen_references"]
        captured.extend(path.read_bytes() for path in frozen.reference_images or [])
        frozen.cleanup()
        return {"resource_type": "characters", "resource_id": "鳄鱼爸爸"}

    async def _linked_reference(*_args, **_kwargs):
        return global_reference

    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(
        generation_tasks,
        "_resolve_linked_global_image_reference",
        _linked_reference,
    )
    monkeypatch.setattr(generation_tasks, "_run_asset_sheet_image_task", _run)

    await generation_tasks.execute_character_task("demo", "鳄鱼爸爸", {"prompt": "角色"})

    assert captured == [b"project-reference-b"]
