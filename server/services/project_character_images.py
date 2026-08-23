"""Project character image-slot operations shared by Web and Agent entrypoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lib.artifact_activation import reconcile_artifact_target_claims
from lib.artifact_manifest import ArtifactKey
from lib.asset_types import (
    GLOBAL_ASSET_ID_FIELD,
    GLOBAL_ASSET_IMAGE_USAGE_FIELD,
    MATCHED_GLOBAL_ASSET_ID_FIELD,
    resolve_asset_key,
)
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.path_safety import safe_exists, safe_join
from lib.project_change_hints import ProjectChangeSource, project_change_source
from lib.project_manager import ProjectManager, get_project_manager


class ProjectCharacterImageError(ValueError):
    """Base error for project character image-slot transitions."""


class ProjectCharacterMainImageMissing(ProjectCharacterImageError):
    """The character has no currently displayed main image to move."""


class ProjectCharacterImageConflict(ProjectCharacterImageError):
    """The image source changed between resolution and the locked commit."""


@dataclass(frozen=True, slots=True)
class CharacterMainToReferenceResult:
    project_asset: dict[str, Any]
    source: Literal["global", "project"]
    reference_path: str


def _character_entry(project: dict[str, Any], character_name: str) -> tuple[str, dict[str, Any]]:
    characters = project.get("characters")
    key = resolve_asset_key(characters, character_name)
    if not isinstance(characters, dict) or key is None:
        raise KeyError(character_name)
    entry = characters.get(key)
    if not isinstance(entry, dict):
        raise ProjectCharacterImageError(f"project character {key!r} must be an object")
    return key, entry


def _linked_asset_id(entry: dict[str, Any]) -> str | None:
    raw = entry.get(GLOBAL_ASSET_ID_FIELD) or entry.get(MATCHED_GLOBAL_ASSET_ID_FIELD)
    return raw if isinstance(raw, str) and raw else None


async def move_character_main_to_reference(
    project_name: str,
    character_name: str,
    *,
    manager: ProjectManager | None = None,
    session_factory=None,
    source: ProjectChangeSource = "webui",
) -> CharacterMainToReferenceResult:
    """Move the card's current main image into its project reference slot.

    A linked Global Asset is only the initial source of the card main image. The
    operation snapshots whichever image is currently visible (Global or project)
    into the project reference slot, clears ``character_sheet``, and leaves the
    Global Asset and its primary selection untouched.
    """

    pm = manager or get_project_manager()
    factory = session_factory or async_session_factory
    project = await asyncio.to_thread(pm.load_project, project_name)
    _key, entry = _character_entry(project, character_name)
    expected_link_id = _linked_asset_id(entry)
    expected_usage = entry.get(GLOBAL_ASSET_IMAGE_USAGE_FIELD, "main")
    expected_sheet = entry.get("character_sheet")

    source_kind: Literal["global", "project"]
    source_relative: str
    source_root: Path

    global_relative: str | None = None
    if expected_link_id is not None and expected_usage == "main":
        async with factory() as session:
            asset = await AssetRepository(session).get_by_id(expected_link_id)
        if (
            asset is not None
            and asset.type == "character"
            and isinstance(asset.image_path, str)
            and safe_exists(pm.projects_root, asset.image_path)
        ):
            global_relative = asset.image_path

    if global_relative is not None:
        source_kind = "global"
        source_relative = global_relative
        source_root = pm.projects_root
    elif (
        isinstance(expected_sheet, str)
        and expected_sheet
        and safe_exists(pm.get_project_path(project_name), expected_sheet)
    ):
        source_kind = "project"
        source_relative = expected_sheet
        source_root = pm.get_project_path(project_name)
    else:
        raise ProjectCharacterMainImageMissing(character_name)

    copies: list[tuple[Path, Path]] = []
    moved_entry: dict[str, Any] = {}
    reference_path = ""
    canonical_name = ""
    project_dir = pm.get_project_path(project_name)

    def _mutate(locked_project: dict[str, Any]) -> None:
        nonlocal canonical_name, reference_path
        canonical_name, current = _character_entry(locked_project, character_name)
        current_link_id = _linked_asset_id(current)
        current_usage = current.get(GLOBAL_ASSET_IMAGE_USAGE_FIELD, "main")
        current_sheet = current.get("character_sheet")
        if source_kind == "global":
            if current_link_id != expected_link_id or current_usage != "main":
                raise ProjectCharacterImageConflict(character_name)
        elif current_sheet != expected_sheet:
            raise ProjectCharacterImageConflict(character_name)

        try:
            source_file = safe_join(source_root, source_relative, require_file=True)
        except FileNotFoundError as exc:
            raise ProjectCharacterMainImageMissing(character_name) from exc
        except ValueError as exc:
            raise ProjectCharacterImageError(str(exc)) from exc
        suffix = source_file.suffix.lower() or ".png"
        reference_path = f"characters/refs/{canonical_name}{suffix}"
        target = safe_join(project_dir, reference_path)
        if source_file.resolve(strict=False) != target.resolve(strict=False):
            copies.append((source_file, target))

        current["reference_image"] = reference_path
        current["character_sheet"] = ""
        if current_link_id is not None:
            current[GLOBAL_ASSET_IMAGE_USAGE_FIELD] = "reference"
        moved_entry.update(current)

    def _reconcile_sheet_claim(_project_file: Path) -> None:
        if not canonical_name:  # pragma: no cover - mutation contract
            raise RuntimeError("character image move did not resolve a canonical identity")
        reconcile_artifact_target_claims(
            project_dir,
            (ArtifactKey.asset_sheet("character", canonical_name),),
        )

    def _commit() -> None:
        with project_change_source(source):
            pm.update_project_with_file_copies(
                project_name,
                _mutate,
                copies,
                on_commit=_reconcile_sheet_claim,
            )

    await asyncio.to_thread(_commit)
    return CharacterMainToReferenceResult(
        project_asset=moved_entry,
        source=source_kind,
        reference_path=reference_path,
    )


__all__ = [
    "CharacterMainToReferenceResult",
    "ProjectCharacterImageConflict",
    "ProjectCharacterImageError",
    "ProjectCharacterMainImageMissing",
    "move_character_main_to_reference",
]
