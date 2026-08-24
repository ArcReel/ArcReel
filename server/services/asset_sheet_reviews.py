"""Accept existing project asset sheets as matching their current definitions.

This is the non-generative counterpart to asset-sheet generation.  A user may
review an existing image after editing its description and explicitly assert
that the bytes still represent the new definition.  Web and Agent entry points
share this service so neither needs to rewrite the Artifact Manifest directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from lib.artifact_activation import register_artifact_entries_atomically
from lib.artifact_manifest import ArtifactKey, ArtifactManifestError, ProjectArtifactManifestAdapter
from lib.artifact_planner import TargetStatePlanner
from lib.asset_types import ASSET_SPECS, asset_name_comparison_key
from lib.formal_write import project_metadata_lock
from lib.project_manager import ProjectManager, get_project_manager


class AssetSheetReviewError(ValueError):
    """A requested sheet cannot be safely accepted as current."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AssetSheetSelection:
    asset_type: str
    name: str


def _normalize_selections(raw: Iterable[AssetSheetSelection] | None) -> tuple[AssetSheetSelection, ...] | None:
    if raw is None:
        return None
    selections: list[AssetSheetSelection] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if item.asset_type not in ASSET_SPECS:
            raise AssetSheetReviewError("unknown_asset_type", f"unknown asset type: {item.asset_type}")
        name = asset_name_comparison_key(item.name)
        if not name:
            raise AssetSheetReviewError("invalid_asset_name", "asset name must not be blank")
        identity = (item.asset_type, name)
        if identity not in seen:
            seen.add(identity)
            selections.append(AssetSheetSelection(*identity))
    if not selections:
        raise AssetSheetReviewError("empty_selection", "at least one asset sheet must be selected")
    return tuple(selections)


def _project_entries(project: Mapping[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    entries: dict[tuple[str, str], tuple[str, str]] = {}
    for asset_type, spec in ASSET_SPECS.items():
        bucket = project.get(spec.bucket_key, {})
        if not isinstance(bucket, Mapping):
            raise AssetSheetReviewError("invalid_asset_bucket", f"{spec.bucket_key} must be an object")
        for raw_name, raw_entry in bucket.items():
            if not isinstance(raw_name, str) or not isinstance(raw_entry, Mapping):
                raise AssetSheetReviewError("invalid_asset_entry", f"invalid entry in {spec.bucket_key}")
            sheet = raw_entry.get(spec.sheet_field)
            if not isinstance(sheet, str) or not sheet:
                continue
            name = asset_name_comparison_key(raw_name)
            entries[(asset_type, name)] = (raw_name, sheet)
    return entries


def confirm_asset_sheets_current(
    project_name: str,
    *,
    selections: Iterable[AssetSheetSelection] | None = None,
    manager: ProjectManager | None = None,
) -> dict[str, Any]:
    """Register reviewed existing sheets against their current canonical basis.

    No image bytes or version history are changed.  The operation only replaces
    selected Artifact Manifest claims after proving that every current target
    and file exists.  The batch is all-or-nothing.
    """

    pm = manager or get_project_manager()
    project_dir = pm.get_project_path(project_name)
    requested = _normalize_selections(selections)
    with project_metadata_lock(project_dir):
        project = pm.load_project_readonly(project_name)
        available = _project_entries(project)
        identities = (
            tuple(available) if requested is None else tuple((item.asset_type, item.name) for item in requested)
        )
        missing = [identity for identity in identities if identity not in available]
        if missing:
            labels = ", ".join(f"{asset_type}:{name}" for asset_type, name in missing)
            raise AssetSheetReviewError("asset_sheet_not_found", f"asset sheet not found: {labels}")
        if not identities:
            raise AssetSheetReviewError("no_asset_sheets", "project has no existing asset sheets to confirm")

        adapter = ProjectArtifactManifestAdapter(project_dir)
        planner = TargetStatePlanner(project_dir)
        keys = {identity: ArtifactKey.asset_sheet(identity[0], identity[1]) for identity in identities}
        before = {key: adapter.get_entry(key) for key in keys.values()}
        replacements = {}
        for identity, key in keys.items():
            target = planner.resolve_key(key)
            if target is None:
                raw_name, sheet = available[identity]
                raise AssetSheetReviewError(
                    "asset_sheet_unprovable",
                    f"cannot prove current target for {identity[0]}:{raw_name} ({sheet})",
                )
            replacements[key] = target

        try:
            changed = register_artifact_entries_atomically(
                project_dir,
                replacements,
                expected_entries=before,
                adapter=adapter,
            )
        except ArtifactManifestError as exc:
            raise AssetSheetReviewError("asset_manifest_conflict", str(exc)) from exc

        confirmed = [
            {
                "asset_type": asset_type,
                "name": available[(asset_type, name)][0],
                "sheet_path": available[(asset_type, name)][1],
            }
            for asset_type, name in identities
        ]
    return {
        "success": True,
        "changed": changed,
        "confirmed_count": len(confirmed),
        "confirmed": confirmed,
    }


__all__ = [
    "AssetSheetReviewError",
    "AssetSheetSelection",
    "confirm_asset_sheets_current",
]
