"""旧项目的逐字段媒体内容在原地迁移与带信封的归档导入后保持不变。"""

from __future__ import annotations

import json
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import read_artifact_content_digest
from lib.artifacts.artifact_manifest import (
    ArtifactKind,
    ArtifactManifestArchiveSnapshot,
    ProjectArtifactManifestAdapter,
    encode_artifact_manifest_payload,
)
from lib.infra.path_safety import try_safe_join
from lib.project.asset_types import ASSET_SPECS, normalize_asset_name
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services.project.project_archive import (
    ARCHIVE_MANIFEST_NAME,
    ProjectArchiveService,
    ProjectArchiveValidationError,
)
from tests.legacy_project_shapes import LEGACY_PROJECT_SHAPES

_SCRIPT_MEDIA_DIRS = {
    "storyboard_image": "storyboards",
    "storyboard_last_image": "storyboards",
    "video_clip": "videos",
    "video_thumbnail": None,
    "video_uri": "videos",
    "narration_audio": "audio",
}


def _referenced_file(project_dir: Path, reference: str, default_dir: str | None) -> Path | None:
    relative = reference.strip().replace("\\", "/")
    path = try_safe_join(project_dir, relative, require_file=True)
    if path is None and default_dir and len(Path(relative).parts) == 1:
        path = try_safe_join(project_dir, default_dir, relative, require_file=True)
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _media_snapshot(
    project_dir: Path, *, asset_aliases: Mapping[tuple[str, str], str] | None = None
) -> dict[str, bytes | None]:
    project = _read_json(project_dir / "project.json")
    media: dict[str, bytes | None] = {}
    aliases = asset_aliases or {}

    def capture(location: str, reference: object, default_dir: str | None = None) -> None:
        if not isinstance(reference, str) or not reference.strip():
            return
        path = _referenced_file(project_dir, reference, default_dir)
        try:
            media[location] = path.read_bytes() if path is not None else None
        except OSError:
            media[location] = None

    capture("style_image", project.get("style_image"))
    for spec in ASSET_SPECS.values():
        for name, asset in project.get(spec.bucket_key, {}).items():
            name = normalize_asset_name(name.strip())
            identity = aliases.get((spec.bucket_key, name), name)
            location = f"{spec.bucket_key}[{identity}]"
            for field in (spec.sheet_field, *spec.original_image_fields, "reference_audio"):
                value = asset.get(field)
                if isinstance(value, list):
                    for index, reference in enumerate(value):
                        capture(f"{location}.{field}[{index}]", reference)
                else:
                    directory = {
                        spec.sheet_field: spec.bucket_key,
                        "reference_audio": "characters/refs_audio",
                        "reference_image": "characters/refs",
                    }.get(field)
                    capture(f"{location}.{field}", value, directory)
            for name, derivative in asset.get("derivatives", {}).items():
                capture(
                    f"{location}.derivatives[{normalize_asset_name(name)}].{spec.sheet_field}",
                    derivative.get(spec.sheet_field),
                )

    for episode in project.get("episodes", []):
        script_file = episode.get("script_file")
        if not script_file or (script_path := _referenced_file(project_dir, script_file, "scripts")) is None:
            continue
        script = _read_json(script_path)
        for bucket in ("segments", "scenes", "shots", "reference_units", "video_units"):
            identity = "video_units" if bucket == "reference_units" else bucket
            for index, item in enumerate(script.get(bucket, [])):
                location = f"episodes[{episode['episode']}].{identity}[{index}]"
                capture(f"{location}.end_frame_image", item.get("end_frame_image"), "end_frames")
                for field, directory in _SCRIPT_MEDIA_DIRS.items():
                    capture(
                        f"{location}.generated_assets.{field}",
                        (item.get("generated_assets") or {}).get(field),
                        directory,
                    )
    return media


def _write_enveloped_archive(project_dir: Path, archive_path: Path) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    media_kinds = {
        ArtifactKind.ASSET_SHEET,
        ArtifactKind.EPISODE_GRID,
        ArtifactKind.EPISODE_STORYBOARD,
        ArtifactKind.EPISODE_VIDEO,
        ArtifactKind.EPISODE_AUDIO,
    }
    entries = {key: entry for key, entry in adapter.snapshot_entries().items() if key.kind in media_kinds}
    digests = {key: read_artifact_content_digest(adapter, entry.artifact_path) for key, entry in entries.items()}
    manifest = ArtifactManifestArchiveSnapshot(entries=entries, content_digests=digests)
    envelope = {"project_name": project_dir.name, "artifact_manifest": encode_artifact_manifest_payload(manifest)}
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(ARCHIVE_MANIFEST_NAME, json.dumps(envelope))
        for path in sorted(project_dir.rglob("*")):
            relative = path.relative_to(project_dir)
            if all(not part.startswith(".") for part in relative.parts):
                archive.write(path, relative.as_posix())


@pytest.mark.parametrize(
    ("shape", "root_is_alias"),
    [
        *(pytest.param(shape, False, id=shape) for shape in LEGACY_PROJECT_SHAPES),
        pytest.param("crossed-script-bindings", True, id="crossed-script-bindings-root-alias"),
        pytest.param("shared-item-id", True, id="shared-item-id-root-alias"),
    ],
)
@pytest.mark.parametrize("entry_point", ["in_place", "archive_import"])
def test_legacy_media_fields_preserve_readable_bytes_and_unreadable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, entry_point: str, root_is_alias: bool
) -> None:
    tmp_path = tmp_path.resolve()
    project_dir = LEGACY_PROJECT_SHAPES[shape](tmp_path / "source")
    expected = _media_snapshot(project_dir)
    cleared = ((0, "storyboard_image"), (1, "storyboard_image"), (1, "video_clip"))
    if shape == "shared-item-id":
        for index, field in cleared:
            assert expected.pop(f"episodes[2].segments[{index}].generated_assets.{field}") is not None
    if shape == "missing-original-occupied-target":
        location = "characters[Bob].reference_image"
        assert expected[location] is None
        if entry_point == "archive_import":
            expected[location] = (project_dir / "characters/refs/Bob.png").read_bytes()

    if entry_point == "in_place":
        if root_is_alias:
            alias = tmp_path / "project-alias"
            alias.symlink_to(project_dir.parent, target_is_directory=True)
            project_dir = alias / project_dir.name
        migrate_project_dir(project_dir)
    else:
        extraction_root = tmp_path / "extraction"
        extraction_root.mkdir()
        if root_is_alias:
            alias = tmp_path / "extraction-alias"
            alias.symlink_to(extraction_root, target_is_directory=True)
            extraction_root = alias
        monkeypatch.setattr(tempfile, "tempdir", str(extraction_root))
        archive_path = tmp_path / "legacy.zip"
        _write_enveloped_archive(project_dir, archive_path)
        target = ProjectManager(tmp_path / "imported")
        service = ProjectArchiveService(target)
        if shape == "missing-character-original":
            with pytest.raises(ProjectArchiveValidationError) as rejected:
                service.import_project_archive(archive_path, confirm_report=True)
            assert [(error.key, error.params) for error in rejected.value.errors] == [
                ("val_path_missing", {"field": "characters[张三].reference_image", "path": "characters/refs/张三.png"})
            ]
            return
        try:
            result = service.import_project_archive(archive_path, confirm_report=True)
        except ProjectArchiveValidationError as exc:
            pytest.fail(f"Archive import failed: {exc.render_errors()}")
        project_dir = target.get_project_path(result.project_name)

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    if shape == "crossed-script-bindings":
        expected_bindings = {1: "scripts/episode_1.json", 2: "scripts/episode_2.json"}
        assert {entry["episode"]: entry["script_file"] for entry in project["episodes"]} == expected_bindings
        for episode, binding in expected_bindings.items():
            assert _read_json(project_dir / binding)["episode"] == episode
    aliases = {("products", "Bob_product"): "Bob"} if shape.startswith("shared-reference") else {}
    after = _media_snapshot(project_dir, asset_aliases=aliases)
    if shape == "shared-item-id":
        segments = _read_json(project_dir / "scripts/episode_2.json")["segments"]
        for index, field in cleared:
            assert segments[index]["generated_assets"][field] is None
    changed = {
        location: (expected.get(location), after.get(location))
        for location in sorted(expected.keys() | after.keys())
        if expected.get(location) != after.get(location)
    }
    assert not changed, f"Media bytes changed (expected, actual): {changed}"
