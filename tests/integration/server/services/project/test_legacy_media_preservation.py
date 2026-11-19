"""旧项目的逐字段媒体内容在原地迁移与带信封的归档导入后保持不变。"""

from __future__ import annotations

import json
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

_SCRIPT_MEDIA_FIELDS = (
    "storyboard_image",
    "storyboard_last_image",
    "video_clip",
    "video_thumbnail",
    "video_uri",
    "narration_audio",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _media_snapshot(
    project_dir: Path, *, asset_aliases: Mapping[tuple[str, str], str] | None = None
) -> dict[str, bytes | None]:
    project = _read_json(project_dir / "project.json")
    media: dict[str, bytes | None] = {}
    aliases = asset_aliases or {}

    def capture(location: str, reference: object) -> None:
        if not isinstance(reference, str) or not reference.strip():
            return
        path = try_safe_join(project_dir, reference.strip().replace("\\", "/"))
        try:
            media[location] = path.read_bytes() if path is not None and path.is_file() else None
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
                    capture(f"{location}.{field}", value)
            for name, derivative in asset.get("derivatives", {}).items():
                capture(
                    f"{location}.derivatives[{normalize_asset_name(name)}].{spec.sheet_field}",
                    derivative.get(spec.sheet_field),
                )

    for episode in project.get("episodes", []):
        script_file = episode.get("script_file")
        if not script_file or not (script_path := project_dir / script_file).is_file():
            continue
        script = _read_json(script_path)
        for bucket in ("segments", "scenes", "shots", "reference_units", "video_units"):
            # v6 广告 reference_units 会转为 video_units。按集号和条目顺序对应，
            # 这样文件名、绑定和条目 ID 的改写不会掩盖同一条目上的字节变化。
            identity = "video_units" if bucket == "reference_units" else bucket
            for index, item in enumerate(script.get(bucket, [])):
                location = f"episodes[{episode['episode']}].{identity}[{index}]"
                capture(f"{location}.end_frame_image", item.get("end_frame_image"))
                for field in _SCRIPT_MEDIA_FIELDS:
                    capture(f"{location}.generated_assets.{field}", (item.get("generated_assets") or {}).get(field))
    return media


def _write_enveloped_archive(project_dir: Path, archive_path: Path) -> None:
    # 写入旧目录原样，避免当前导出器先执行路径修复，掩盖导入阶段的回归。
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


@pytest.mark.parametrize("shape", LEGACY_PROJECT_SHAPES)
@pytest.mark.parametrize("entry_point", ["in_place", "archive_import"])
def test_legacy_media_fields_preserve_readable_bytes_and_unreadable_state(
    tmp_path: Path, shape: str, entry_point: str
) -> None:
    project_dir = LEGACY_PROJECT_SHAPES[shape](tmp_path / "source")
    before = _media_snapshot(project_dir)

    if entry_point == "in_place":
        migrate_project_dir(project_dir)
    else:
        archive_path = tmp_path / "legacy.zip"
        _write_enveloped_archive(project_dir, archive_path)
        target = ProjectManager(tmp_path / "imported")
        try:
            result = ProjectArchiveService(target).import_project_archive(archive_path, confirm_report=True)
        except ProjectArchiveValidationError as exc:
            pytest.fail(f"Archive import failed: {exc.render_errors()}")
        project_dir = target.get_project_path(result.project_name)

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    # v5→v6 给同名商品加后缀，字段仍属于原来的商品。
    aliases = {("products", "Bob_product"): "Bob"} if shape.startswith("shared-reference") else {}
    after = _media_snapshot(project_dir, asset_aliases=aliases)
    changed = {
        location: (before.get(location), after.get(location))
        for location in sorted(before.keys() | after.keys())
        if before.get(location) != after.get(location)
    }
    assert not changed, f"Media bytes changed (before, after): {changed}"
