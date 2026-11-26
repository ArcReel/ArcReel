"""旧项目的逐字段媒体内容在原地迁移与带信封的归档导入后保持不变。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver, read_artifact_content_digest
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestArchiveSnapshot,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    encode_artifact_manifest_payload,
)
from lib.artifacts.version_manager import VersionManager
from lib.infra.path_safety import try_safe_join
from lib.infra.project_write_journal import is_history_database_name
from lib.project import project_trash
from lib.project.asset_types import ASSET_SPECS, normalize_asset_name
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.speech.speech_presentation import presentation_artifact_paths
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.project_archive import (
    ARCHIVE_MANIFEST_NAME,
    ProjectArchiveService,
    ProjectArchiveValidationError,
)
from tests.invariant_profiles import io_property_settings
from tests.legacy_project_shapes import (
    LEGACY_PROJECT_SHAPES,
    cross_bind_episode_scripts,
    read_version_history,
    share_a_sheet_with_a_missing_reference_and_occupied_target,
    write_legacy_storyboard_project,
)

_RELEASE = Path(__file__).resolve().parents[4] / "fixtures/released_archives/v0.37.0-tts.zip"
_project_examples = io_property_settings()

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


def _write_enveloped_archive(project_dir: Path, archive_path: Path, *, full_manifest: bool = False) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    media_kinds = {
        ArtifactKind.ASSET_SHEET,
        ArtifactKind.EPISODE_GRID,
        ArtifactKind.EPISODE_STORYBOARD,
        ArtifactKind.EPISODE_VIDEO,
        ArtifactKind.EPISODE_AUDIO,
    }
    entries = {
        key: entry
        for key, entry in adapter.snapshot_entries().items()
        if (full_manifest or key.kind in media_kinds) and not entry.artifact_path.startswith("renders/")
    }
    digests = {key: read_artifact_content_digest(adapter, entry.artifact_path) for key, entry in entries.items()}
    manifest = ArtifactManifestArchiveSnapshot(entries=entries, content_digests=digests)
    envelope = {"project_name": project_dir.name, "artifact_manifest": encode_artifact_manifest_payload(manifest)}
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(ARCHIVE_MANIFEST_NAME, json.dumps(envelope))
        for path in sorted(project_dir.rglob("*")):
            relative = path.relative_to(project_dir)
            if relative.parts[0] != "renders" and all(not part.startswith(".") for part in relative.parts):
                archive.write(path, relative.as_posix())


@pytest.mark.parametrize(
    ("shape", "root_is_alias"),
    [
        *(
            pytest.param(shape, False, id=shape)
            for shape in LEGACY_PROJECT_SHAPES
            if shape != "missing-original-occupied-target"
        ),
        pytest.param("shared-item-id", True, id="shared-item-id-root-alias"),
        pytest.param("ad-duplicate-ids", True, id="ad-duplicate-ids-root-alias"),
    ],
)
@pytest.mark.parametrize("entry_point", ["in_place", "archive_import"])
def test_legacy_media_fields_preserve_readable_bytes_and_unreadable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, entry_point: str, root_is_alias: bool
) -> None:
    tmp_path = tmp_path.resolve()
    project_dir = LEGACY_PROJECT_SHAPES[shape](tmp_path / "source" / "projects")
    tts_snapshot = shape in {"v15-tts", "v20-tts-exports"}
    before_updated_at = None
    media_bytes: dict[Path, bytes] = {}
    if tts_snapshot:
        before_updated_at = _read_json(project_dir / "project.json")["metadata"]["updated_at"]
        media_bytes = {
            path.relative_to(project_dir): path.read_bytes()
            for pattern in ("versions/audio/*.wav", "renders/*/*/*.zip", "renders/*/*/*.mp4")
            for path in project_dir.glob(pattern)
            if entry_point == "in_place" or path.relative_to(project_dir).parts[0] != "renders"
        }
    expected = _media_snapshot(project_dir)
    cleared = ((0, "storyboard_image"), (1, "storyboard_image"), (1, "video_clip"))
    if shape == "shared-item-id":
        for index, field in cleared:
            assert expected.pop(f"episodes[2].segments[{index}].generated_assets.{field}") is not None
    if shape == "ad-duplicate-ids":
        assert expected.pop("episodes[1].video_units[1].generated_assets.video_clip") is not None
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
        _write_enveloped_archive(project_dir, archive_path, full_manifest=tts_snapshot)
        target = ProjectManager(tmp_path / "imported")
        service = ProjectArchiveService(target)
        if shape in {"missing-character-original", "dangling-alias-reference"}:
            if shape == "missing-character-original":
                key, field, reference = (
                    "val_path_missing",
                    "characters[张三].reference_image",
                    "characters/refs/张三.png",
                )
            else:
                key, field = "val_path_traversal", "characters[Bob].reference_image"
                reference = _read_json(project_dir / "project.json")["characters"]["Bob"]["reference_image"]
            with pytest.raises(ProjectArchiveValidationError) as rejected:
                service.import_project_archive(archive_path, confirm_report=True)
            assert [(error.key, error.params) for error in rejected.value.errors] == [
                (key, {"field": field, "path": reference})
            ]
            return
        try:
            result = service.import_project_archive(archive_path, confirm_report=True)
        except ProjectArchiveValidationError as exc:
            pytest.fail(f"Archive import failed: {exc.render_errors()}")
        project_dir = target.get_project_path(result.project_name)

    project = _read_json(project_dir / "project.json")
    assert project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    if tts_snapshot:
        assert project["metadata"]["updated_at"] == before_updated_at
        assert {path: (project_dir / path).read_bytes() for path in media_bytes} == media_bytes
        _assert_released_tts_delivery(
            project_dir, has_timeline_exports=shape == "v20-tts-exports" and entry_point == "in_place"
        )
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


def _assert_released_tts_delivery(project_dir: Path, *, has_timeline_exports: bool) -> None:
    project = _read_json(project_dir / "project.json")
    assert project["narration_delivery"] == "use_tts"
    assert (project["audio_backend"], project["narration_voice"], project["narration_speed"]) == (
        "dashscope/qwen3-tts-flash",
        "voice-clone-rain",
        1.2,
    )
    workflow = WorkflowStateService(ProjectManager(project_dir.parent.parent))
    audio = workflow.get_status(project_dir.name, 1).artifacts["audio"]
    assert audio["current_ids"] == ["E1S2"]
    assert audio["stale_ids"] == ["E1S1"]
    versions = VersionManager(project_dir).get_versions("audio", "E1S1")
    assert all(record["artifact_audio_basis"]["kind_version"] == 2 for record in versions["versions"])
    assert versions["current_version"] == 2
    historical, selected = sorted(versions["versions"], key=lambda record: record["version"])
    assert (project_dir / selected["file"]).read_bytes() == (project_dir / "audio/segment_E1S1.wav").read_bytes()
    assert (project_dir / historical["file"]).read_bytes() != (project_dir / selected["file"]).read_bytes()
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    resolver = ArtifactCurrencyResolver(project_dir)
    for index in (1, 2):
        unit = f"E1S{index}"
        subtitle_path, presentation_path = presentation_artifact_paths(1, unit, "use_tts")
        presentation = _read_json(project_dir / presentation_path)
        cues = _read_json(project_dir / subtitle_path)["cues"]
        assert cues == presentation["subtitles"]
        assert [cue["text"] for cue in cues] == [f"第{index}段旁白。"]
        for key, path in (
            (ArtifactKey.episode_subtitle(1, unit, "use_tts"), subtitle_path),
            (ArtifactKey.episode_presentation(1, unit, "use_tts"), presentation_path),
        ):
            assert entries[key].artifact_path == path
            expected_currency = ArtifactStatus.CURRENT if index == 2 else ArtifactStatus.STALE
            assert resolver.compare(key, artifact_path=path).status is expected_currency
        if index == 1:
            assert presentation["narration_audio"]["artifact_path"] == historical["file"]
            assert presentation["narration_audio"]["basis"] == historical["artifact_audio_basis"]
    exports = {
        key.kind: resolver.compare(key, artifact_path=entry.artifact_path).status
        for key, entry in entries.items()
        if key.kind in {ArtifactKind.EPISODE_FINAL_CUT, ArtifactKind.EPISODE_JIANYING_DRAFT}
    }
    if not has_timeline_exports:
        assert not any((project_dir / "renders").rglob("*.*"))
    assert exports == (
        {
            ArtifactKind.EPISODE_FINAL_CUT: ArtifactStatus.CURRENT,
            ArtifactKind.EPISODE_JIANYING_DRAFT: ArtifactStatus.CURRENT,
        }
        if has_timeline_exports
        else {}
    )


@pytest.mark.parametrize("member", ["scripts/episode_1.json", presentation_artifact_paths(1, "E1S2", "use_tts")[1]])
def test_import_rejects_changed_json_even_when_migration_would_rewrite_it(tmp_path: Path, member: str) -> None:
    project_dir = LEGACY_PROJECT_SHAPES["v15-tts"](tmp_path / "source")
    original = tmp_path / "original.zip"
    modified = tmp_path / "modified.zip"
    _write_enveloped_archive(project_dir, original, full_manifest=True)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(modified, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            target.writestr(info, data + b" " if info.filename == member else data)

    manager = ProjectManager(tmp_path / "imported")
    with pytest.raises(ProjectArchiveValidationError) as rejected:
        ProjectArchiveService(manager).import_project_archive(modified, confirm_report=True)
    assert "content does not match" in str(rejected.value.errors[0].params["reason"])
    assert manager.list_projects() == []


def test_audio_upgrade_retries_after_versions_write_before_manifest_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = LEGACY_PROJECT_SHAPES["v20-tts-exports"](tmp_path / "projects")

    def interrupt(*args, **kwargs):
        assert (
            VersionManager(root).get_versions("audio", "E1S2")["versions"][0]["artifact_audio_basis"]["kind_version"]
            == 2
        )
        raise RuntimeError("interrupted manifest commit")

    with monkeypatch.context() as fault:
        fault.setattr(ProjectArtifactManifestAdapter, "replace_entries_if_matches_atomically", interrupt)
        with pytest.raises(RuntimeError, match="interrupted manifest commit"):
            migrate_project_dir(root)
    migrate_project_dir(root)
    _assert_released_tts_delivery(root, has_timeline_exports=True)
    snapshots = {path: path.read_bytes() for path in root.rglob("*.json")}
    migrate_project_dir(root)
    assert {path: path.read_bytes() for path in snapshots} == snapshots


def test_explicitly_restoring_stale_pre_upgrade_audio_uses_text_currency(tmp_path: Path) -> None:
    from server.services.currency.artifact_version_restore import restore_typed_media_version

    root = LEGACY_PROJECT_SHAPES["v20-tts-exports"](tmp_path / "projects")
    migrate_project_dir(root)
    manager = ProjectManager(tmp_path)
    manager.update_project(
        root.name, lambda p: [p.pop(k, None) for k in ("audio_backend", "narration_voice", "narration_speed")]
    )
    relative = "audio/segment_E1S1.wav"
    resolver = ArtifactCurrencyResolver(root)
    key = ArtifactKey.episode_audio(1, "E1S1")
    assert resolver.compare(key, artifact_path=relative).status is ArtifactStatus.STALE
    restore_typed_media_version(
        project_manager=manager,
        project_name=root.name,
        project_path=root,
        versions=VersionManager(root),
        resource_type="audio",
        resource_id="E1S1",
        version=1,
        current_file=root / relative,
        artifact_path=relative,
    )
    assert ArtifactCurrencyResolver(root).compare(key, artifact_path=relative).status is ArtifactStatus.CURRENT


def test_partial_project_group_preserves_provable_historical_presentation(tmp_path: Path) -> None:
    root = LEGACY_PROJECT_SHAPES["v20-tts-exports"](tmp_path / "projects")
    project_path = root / "project.json"
    project = _read_json(project_path)
    del project["narration_voice"]
    project_path.write_text(json.dumps(project))
    migrate_project_dir(root)
    migrated = _read_json(project_path)
    assert migrated["narration_delivery"] == "use_tts"
    assert {"audio_backend", "narration_voice", "narration_speed"}.isdisjoint(migrated)
    _, pres = presentation_artifact_paths(1, "E1S1", "use_tts")
    audio = _read_json(root / pres)["narration_audio"]
    assert (audio["version"], audio["basis"]["kind_version"]) == (1, 2)
    assert (
        ArtifactCurrencyResolver(root)
        .compare(ArtifactKey.episode_audio(1, "E1S2"), artifact_path="audio/segment_E1S2.wav")
        .status
        is ArtifactStatus.STALE
    )


def _assert_second_upgrade_stable(root: Path) -> None:
    def business_state():
        return (
            {p.relative_to(root): p.read_bytes() for p in root.rglob("*.json")},
            read_version_history(root),
            _media_snapshot(root),
        )

    before = business_state()
    assert not migrate_project_dir(root)
    assert business_state() == before


@pytest.mark.released_archive
@pytest.mark.parametrize("archive", sorted(_RELEASE.parent.glob("*.zip")), ids=lambda path: path.name)
def test_released_archive_upgrades_to_current_schema(tmp_path: Path, archive: Path) -> None:
    manager = ProjectManager(tmp_path)
    imported = ProjectArchiveService(manager).import_project_archive(archive, confirm_report=True)
    root = manager.get_project_path(imported.project_name)
    assert _read_json(root / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    _assert_second_upgrade_stable(root)


@pytest.mark.invariant
@pytest.mark.released_archive
def test_released_v037_full_archive_upgrades(tmp_path: Path) -> None:
    assert (
        hashlib.sha256(_RELEASE.read_bytes()).hexdigest()
        == "7999e89300124e6747672ffd6b291ce0aef009c24e6234577e12f1025c715836"
    )
    with zipfile.ZipFile(_RELEASE) as archive:
        assert json.loads(archive.read("tts/project.json"))["schema_version"] == 20
        media = {
            name.removeprefix("tts/"): archive.read(name)
            for name in archive.namelist()
            if name.endswith((".wav", ".mp4", ".png"))
        }
    manager = ProjectManager(tmp_path)
    imported = ProjectArchiveService(manager).import_project_archive(_RELEASE, confirm_report=True)
    root = manager.get_project_path(imported.project_name)
    assert _read_json(root / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
    assert {name: (root / name).read_bytes() for name in media} == media
    _assert_released_tts_delivery(root, has_timeline_exports=False)
    _assert_second_upgrade_stable(root)


@pytest.mark.invariant
@_project_examples
@given(
    tree=st.sampled_from(("one-unit", "two-units", "frozen-v1")),
    crossed=st.booleans(),
    missing=st.booleans(),
    salt=st.binary(max_size=8),
)
@example(tree="one-unit", crossed=True, missing=False, salt=b"")
@example(tree="two-units", crossed=True, missing=False, salt=b"")
@example(tree="two-units", crossed=False, missing=True, salt=b"")
@example(tree="frozen-v1", crossed=True, missing=False, salt=b"")
def test_upgrade_preserves_field_owners(tree: str, crossed: bool, missing: bool, salt: bytes) -> None:
    with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as patch:
        base = Path(directory).resolve()
        if tree == "frozen-v1":
            root = base / "source/legacy-storyboard"
            with zipfile.ZipFile(_RELEASE.parents[1] / "perf_before/legacy-v1.zip") as snapshot:
                snapshot.extractall(root)
        else:
            units = ("E1S1",) if tree == "one-unit" else ("E1S1", "E1S2")
            root = write_legacy_storyboard_project(base / "source", unit_ids=units)
        if crossed:
            cross_bind_episode_scripts(root, {1: "episode_2.json", 2: "episode_1.json"}, ledger_order=[2, 1])
        if missing:
            share_a_sheet_with_a_missing_reference_and_occupied_target(root)
        for path in root.glob("storyboards/*.png"):
            path.write_bytes(path.read_bytes() + salt)
        expected = _media_snapshot(root)
        package = base / "old.zip"
        _write_enveloped_archive(root, package)
        alias = base / "alias"
        alias.symlink_to(root.parent, target_is_directory=True)
        assert migrate_project_dir(alias / root.name)
        assert _media_snapshot(root) == expected
        _assert_second_upgrade_stable(root)
        patch.setattr(tempfile, "tempdir", str(alias))
        manager = ProjectManager(base / "target")
        imported = ProjectArchiveService(manager).import_project_archive(package, confirm_report=True)
        target = manager.get_project_path(imported.project_name)
        assert _read_json(target / "project.json")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
        if missing:
            location = "characters[Bob].reference_image"
            assert expected[location] is None
            expected[location] = b"orphan-reference"
        assert _media_snapshot(target) == expected
        _assert_second_upgrade_stable(target)
        if crossed:
            for project in (root, target):
                for entry in _read_json(project / "project.json")["episodes"]:
                    number = entry["episode"]
                    assert entry["script_file"] == f"scripts/episode_{number}.json"
                    assert _read_json(project / entry["script_file"])["episode"] == number


@pytest.mark.invariant
@_project_examples
@given(contents=st.lists(st.binary(max_size=64), min_size=1, max_size=3))
@example(contents=[b""])
def test_upgrade_restores_only_quarantined_draft(contents: list[bytes]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = write_legacy_storyboard_project(Path(directory), schema_version=5, unit_ids=("E1S1",))
        drafts = root / "drafts/episode_1"
        names = ("segments", "normalized_script", "reference_units")
        expected = {}
        for name, content in zip(names, contents, strict=False):
            (drafts / f"step1_{name}.invalid.json").write_bytes(content)
            expected[f"drafts/episode_1/script_plan_{name}.invalid.json"] = content
        assert migrate_project_dir(root)
        records = project_trash.list_records(root)
        assert sorted(r.files for r in records) == sorted((name,) for name in expected)

        def active_files():
            return {
                p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
                and not is_history_database_name(p.name)
                and not any(part.startswith(".") for part in p.relative_to(root).parts)
            }

        history = read_version_history(root)
        before = active_files()
        assert expected.keys().isdisjoint(before)
        for record in records:
            [relative] = record.files
            restored = project_trash.restore(root, record.id)
            assert [file.path for file in restored.restored] == [relative]
            before[relative] = expected[relative]
            assert active_files() == before
            assert read_version_history(root) == history
        assert project_trash.list_records(root) == []


@pytest.mark.invariant
@_project_examples
@given(uploads=st.lists(st.booleans(), min_size=1, max_size=4), later=st.lists(st.booleans(), max_size=2))
@example(uploads=[True], later=[])
def test_current_export_keeps_selected_storyboard(uploads: list[bool], later: list[bool]) -> None:
    with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as patch:
        base = Path(directory)
        patch.setattr(tempfile, "tempdir", str(base))
        manager = ProjectManager(base / "source")
        manager.create_project("demo")
        manager.create_project_metadata("demo", "Demo", "Anime", "narration")
        root = manager.get_project_path("demo")
        with zipfile.ZipFile(_RELEASE) as archive:
            script = json.loads(archive.read("tts/scripts/episode_1.json"))
        script["segments"] = script["segments"][:1]
        [segment] = script["segments"]
        relative = segment["generated_assets"]["storyboard_image"]
        segment["generated_assets"] = {"storyboard_image": relative}
        manager.save_script("demo", script, "episode_1.json", validate=False)
        current = root / relative
        versions = VersionManager(root)
        for index, upload in enumerate(uploads + later, 1):
            current.write_bytes(f"frame-{index}".encode())
            versions.add_version(
                "storyboards",
                segment["segment_id"],
                "",
                source_file=current,
                **({"source": "manual_upload"} if upload else {}),
            )
        if later:
            versions.restore_version("storyboards", segment["segment_id"], len(uploads), current)
        expected_bytes = f"frame-{len(uploads)}".encode()
        archive_path, _ = ProjectArchiveService(manager).export_project("demo", scope="current")
        with zipfile.ZipFile(archive_path) as packed:
            history = json.loads(packed.read("demo/versions/versions.json"))
            selected = history.get("storyboards", {}).get(segment["segment_id"], {}).get("versions", [])
            assert [record["version"] for record in selected] == ([len(uploads)] if uploads[-1] else [])
            assert {
                p for p in packed.namelist() if p.startswith("demo/versions/storyboards/") and not p.endswith("/")
            } == {"demo/" + record["file"] for record in selected}
            assert packed.read("demo/" + relative) == expected_bytes
            if selected:
                assert packed.read("demo/" + selected[0]["file"]) == expected_bytes
