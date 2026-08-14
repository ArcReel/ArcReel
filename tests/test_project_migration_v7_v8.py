from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Event, Thread

import pytest

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    ensure_imported_artifact_target_state,
    register_current_resource_artifact,
    register_task_current_resource_artifact,
)
from lib.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifact_provenance import build_ad_episode_script_basis, build_episode_script_basis, build_step1_basis
from lib.grid.layout import grid_aspect_ratio_for
from lib.grid.models import GridGeneration, build_frame_chain
from lib.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis_from_canonical_text
from lib.project_manager import ProjectManager
from lib.project_migrations.runner import cleanup_stale_backups, migrate_project_dir
from lib.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.speech_artifact_provenance import (
    RenditionVariant,
    SelectedMediaEvidence,
    build_video_duration_basis,
    build_video_speech_basis,
)
from lib.speech_composition import admit_script_unit
from lib.speech_presentation import PresentationMedia, materialize_speech_presentation, presentation_artifact_paths
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_storyboard_image_visual_basis,
    build_storyboard_video_artifact_visual_basis,
)
from lib.workflow_state import WorkflowStateService

pytestmark = pytest.mark.integration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_verified_presentation_claims(
    project_dir: Path,
    *,
    episode: int = 1,
    resource_id: str = "E1S01",
    variant: RenditionVariant = "post_production",
) -> tuple[ArtifactKey, ArtifactManifestEntry, ArtifactKey, ArtifactManifestEntry]:
    script_path = project_dir / "scripts" / f"episode_{episode}.json"
    script = _read_json(script_path)
    item = script["segments"][0]
    item["duration_seconds"] = 4
    item["novel_text"] = "雨夜"
    item["generated_assets"]["video_clip"] = f"videos/scene_{resource_id}.mp4"
    _write_json(script_path, script)
    video_path = project_dir / f"videos/scene_{resource_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"paid-video")

    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id=resource_id,
        visual_prompt=item["video_prompt"],
        storyboard_image=project_dir / f"storyboards/scene_{resource_id}.png",
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(4)
    facts = VideoArtifactCurrencyFacts(
        episode=episode,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4,),
        reference_image_limit=None,
        parent_version=0,
    )
    versions = VersionManager(project_dir)
    selected_version = versions.add_version(
        "videos",
        resource_id,
        "paid",
        source_file=video_path,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file=f"episode_{episode}.json",
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=facts.to_dict(),
    )
    selected = next(
        record
        for record in versions.get_versions("videos", resource_id)["versions"]
        if record["version"] == selected_version
    )
    selected_path = project_dir / selected["file"]
    media = PresentationMedia(
        artifact_path=selected["file"],
        version=selected_version,
        selection="current",
        currency="current",
        evidence=SelectedMediaEvidence.from_file(
            basis=facts.video_basis,
            path=selected_path,
            actual_duration_seconds=4.0,
        ),
    )
    presentation = materialize_speech_presentation(
        preparation,
        variant=variant,
        video=media,
        provider_audio_enabled=True,
    )
    subtitle_path, presentation_path = presentation_artifact_paths(episode, resource_id, variant)
    _write_json(project_dir / subtitle_path, presentation.subtitle_artifact_dict())
    _write_json(
        project_dir / presentation_path,
        {
            "episode": episode,
            "resource_type": "videos",
            "script_file": f"episode_{episode}.json",
            "transition_to_next": "cut",
            "subtitle_artifact_path": subtitle_path,
            "presentation_artifact_path": presentation_path,
            "persisted": True,
            **presentation.to_dict(),
        },
    )
    subtitle_key = ArtifactKey.episode_subtitle(episode, resource_id, variant)
    presentation_key = ArtifactKey.episode_presentation(episode, resource_id, variant)
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project_dir))
    manifest.register(
        ArtifactKey.episode_video(episode, resource_id),
        artifact_path=f"videos/scene_{resource_id}.mp4",
        basis=facts.video_basis,
    )
    manifest.register(subtitle_key, artifact_path=subtitle_path, basis=presentation.subtitle_basis)
    manifest.register(presentation_key, artifact_path=presentation_path, basis=presentation.presentation_basis)
    return (
        subtitle_key,
        ArtifactManifestEntry(subtitle_path, presentation.subtitle_basis.digest),
        presentation_key,
        ArtifactManifestEntry(presentation_path, presentation.presentation_basis.digest),
    )


def _project(tmp_path: Path) -> tuple[Path, dict, dict, dict]:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    project = {
        "schema_version": 7,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "style": "水墨",
        "style_description": "淡彩",
        "aspect_ratio": "9:16",
        "grid_storyboard": False,
        "characters": {
            "阿离": {
                "description": "银发旅人",
                "character_sheet": "characters/阿离.png",
            }
        },
        "scenes": {"雨巷": {"description": "湿漉石板路", "scene_sheet": "scenes/雨巷.png"}},
        "props": {"伞": {"description": "油纸伞", "prop_sheet": "props/伞.png"}},
        "products": {
            "咖啡": {
                "description": "玻璃瓶咖啡",
                "product_sheet": "products/咖啡.png",
                "reference_images": ["products/refs/咖啡.png"],
            }
        },
        "episodes": [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
            }
        ],
    }
    step1 = {"segments": [{"novel_text": "雨夜"}]}
    script = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": "E1S01",
                "image_prompt": "阿离站在雨中",
                "video_prompt": "阿离转身",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
            }
        ],
    }
    _write_json(project_dir / "project.json", project)
    (project_dir / "source").mkdir()
    (project_dir / "source" / "episode_1.txt").write_text("雨夜", encoding="utf-8")
    _write_json(project_dir / "drafts" / "episode_1" / "step1_segments.json", step1)
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    (project_dir / "characters").mkdir()
    (project_dir / "characters" / "阿离.png").write_bytes(b"character")
    (project_dir / "scenes").mkdir()
    (project_dir / "scenes" / "雨巷.png").write_bytes(b"scene")
    (project_dir / "props").mkdir()
    (project_dir / "props" / "伞.png").write_bytes(b"prop")
    (project_dir / "products" / "refs").mkdir(parents=True)
    (project_dir / "products" / "咖啡.png").write_bytes(b"product")
    (project_dir / "products" / "refs" / "咖啡.png").write_bytes(b"original")
    (project_dir / "storyboards").mkdir()
    (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"storyboard")
    return project_dir, project, step1, script


def _stored_entries(project_dir: Path) -> dict[str, dict[str, str]]:
    return _read_json(project_dir / MANIFEST_FILENAME)["entries"]


def _reference_video_facts(resource_id: str, *, episode: int = 1) -> VideoArtifactCurrencyFacts:
    visual = ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": resource_id,
            "visual_shots": [{"shot_index": 0, "lines": ["产品掠过画面"]}],
            "style": "写实",
            "canvas": {"aspect_ratio": "9:16"},
            "request_references": [],
        },
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "silent"})
    duration = build_video_duration_basis(8)
    return VideoArtifactCurrencyFacts(
        episode=episode,
        request_duration_seconds=8,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(8,),
        reference_image_limit=None,
        parent_version=0,
    )


def test_v7_activation_replaces_partial_manifest_from_canonical_target_state(tmp_path: Path) -> None:
    project_dir, project, step1, _script = _project(tmp_path)
    orphan = project_dir / "output" / "orphan.srt"
    orphan.parent.mkdir()
    orphan.write_text("history", encoding="utf-8")
    old_key = ArtifactKey.episode_subtitle(1, "E1S01", "post_production")
    old_basis = ArtifactBasis.build("old/subtitle", kind_version=1, inputs={})
    ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register(
        old_key,
        artifact_path="output/orphan.srt",
        basis=old_basis,
    )

    assert migrate_project_dir(project_dir) is True

    expected = {
        ArtifactKey.asset_sheet("character", "阿离"): ArtifactManifestEntry(
            artifact_path="characters/阿离.png",
            basis_digest=build_asset_sheet_visual_basis(
                asset_type="character",
                asset_id="阿离",
                description="银发旅人",
                style="水墨",
                style_description="淡彩",
                aspect_ratio="16:9",
            ).digest,
        ),
        ArtifactKey.asset_sheet("scene", "雨巷"): ArtifactManifestEntry(
            artifact_path="scenes/雨巷.png",
            basis_digest=build_asset_sheet_visual_basis(
                asset_type="scene",
                asset_id="雨巷",
                description="湿漉石板路",
                style="水墨",
                style_description="淡彩",
                aspect_ratio="16:9",
            ).digest,
        ),
        ArtifactKey.asset_sheet("prop", "伞"): ArtifactManifestEntry(
            artifact_path="props/伞.png",
            basis_digest=build_asset_sheet_visual_basis(
                asset_type="prop",
                asset_id="伞",
                description="油纸伞",
                style="水墨",
                style_description="淡彩",
                aspect_ratio="16:9",
            ).digest,
        ),
        ArtifactKey.asset_sheet("product", "咖啡"): ArtifactManifestEntry(
            artifact_path="products/咖啡.png",
            basis_digest=build_asset_sheet_visual_basis(
                asset_type="product",
                asset_id="咖啡",
                description="玻璃瓶咖啡",
                style="水墨",
                style_description="淡彩",
                aspect_ratio="16:9",
                references=(
                    VisualReference(
                        path=project_dir / "products" / "refs" / "咖啡.png",
                        role="source",
                        logical_type="product",
                        logical_id="咖啡",
                        kind="original",
                    ),
                ),
            ).digest,
        ),
        ArtifactKey.episode_step1(1): ArtifactManifestEntry(
            artifact_path="drafts/episode_1/step1_segments.json",
            basis_digest=build_step1_basis("雨夜", project=project).digest,
        ),
        ArtifactKey.episode_script(1): ArtifactManifestEntry(
            artifact_path="scripts/episode_1.json",
            basis_digest=build_episode_script_basis(step1, project=project).digest,
        ),
        ArtifactKey.episode_storyboard(1, "E1S01"): ArtifactManifestEntry(
            artifact_path="storyboards/scene_E1S01.png",
            basis_digest=build_storyboard_image_visual_basis(
                resource_id="E1S01",
                image_prompt="阿离站在雨中",
                style="水墨",
                aspect_ratio="9:16",
            ).digest,
        ),
    }
    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert _stored_entries(project_dir) == {
        key.encode(): {
            "artifact_path": entry.artifact_path,
            "basis_digest": entry.basis_digest,
        }
        for key, entry in expected.items()
    }
    assert old_key.encode() not in _stored_entries(project_dir)
    assert orphan.read_text(encoding="utf-8") == "history"


def test_v7_activation_preserves_verified_presentation_claims_in_the_complete_target(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    subtitle_key, subtitle_entry, presentation_key, presentation_entry = _write_verified_presentation_claims(
        project_dir
    )

    migrate_v7_to_v8(project_dir)

    adapter = ProjectArtifactManifestAdapter(project_dir)
    assert adapter.get_entry(subtitle_key) == subtitle_entry
    assert adapter.get_entry(presentation_key) == presentation_entry
    resolver = ArtifactCurrencyResolver(project_dir)
    assert resolver.compare(subtitle_key, artifact_path=subtitle_entry.artifact_path).status is ArtifactStatus.CURRENT
    assert (
        resolver.compare(presentation_key, artifact_path=presentation_entry.artifact_path).status
        is ArtifactStatus.CURRENT
    )
    assert list(project_dir.glob("project.json.bak.v7-*"))
    assert list((project_dir / "scripts").glob("episode_1.json.bak.v7-*"))
    assert list(project_dir.glob(f"{MANIFEST_FILENAME}.bak.v7-*"))

    tracked = [
        project_dir / "project.json",
        project_dir / "scripts" / "episode_1.json",
        project_dir / MANIFEST_FILENAME,
    ]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked]
    assert migrate_project_dir(project_dir) is False
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked] == before

    changed_script = _read_json(project_dir / "scripts" / "episode_1.json")
    changed_script["segments"][0]["novel_text"] = "雨停之后"
    _write_json(project_dir / "scripts" / "episode_1.json", changed_script)
    stale_resolver = ArtifactCurrencyResolver(project_dir)
    assert (
        stale_resolver.compare(subtitle_key, artifact_path=subtitle_entry.artifact_path).status is ArtifactStatus.STALE
    )
    assert (
        stale_resolver.compare(presentation_key, artifact_path=presentation_entry.artifact_path).status
        is ArtifactStatus.STALE
    )


def test_schema8_archive_activation_reconstructs_only_self_proving_presentation_pairs(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    subtitle_key, subtitle_entry, presentation_key, presentation_entry = _write_verified_presentation_claims(
        project_dir
    )
    migrate_v7_to_v8(project_dir)
    subtitle_file = project_dir / subtitle_entry.artifact_path
    presentation_file = project_dir / presentation_entry.artifact_path
    before_artifacts = (subtitle_file.read_bytes(), presentation_file.read_bytes())

    # Official archives retain the visible typed artifacts and managed version
    # snapshot, but deliberately filter the hidden Manifest sidecar.
    (project_dir / MANIFEST_FILENAME).unlink()

    assert ensure_imported_artifact_target_state(project_dir) is True
    adapter = ProjectArtifactManifestAdapter(project_dir)
    assert adapter.get_entry(subtitle_key) == subtitle_entry
    assert adapter.get_entry(presentation_key) == presentation_entry
    assert (subtitle_file.read_bytes(), presentation_file.read_bytes()) == before_artifacts


def test_schema8_archive_activation_rejects_tampered_presentation_evidence(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    subtitle_key, _subtitle_entry, presentation_key, presentation_entry = _write_verified_presentation_claims(
        project_dir
    )
    migrate_v7_to_v8(project_dir)
    presentation_file = project_dir / presentation_entry.artifact_path
    tampered = _read_json(presentation_file)
    tampered["video"]["content_digest"] = f"sha256-v1:{'0' * 64}"
    _write_json(presentation_file, tampered)
    (project_dir / MANIFEST_FILENAME).unlink()

    assert ensure_imported_artifact_target_state(project_dir) is True
    adapter = ProjectArtifactManifestAdapter(project_dir)
    assert adapter.get_entry(subtitle_key) is None
    assert adapter.get_entry(presentation_key) is None


def test_runtime_resolver_plans_storyboards_only_once_per_snapshot(tmp_path: Path, monkeypatch) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    migrate_v7_to_v8(project_dir)
    from lib import artifact_activation

    calls = 0
    original = artifact_activation.build_storyboard_image_visual_basis

    def _counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(artifact_activation, "build_storyboard_image_visual_basis", _counted)
    resolver = ArtifactCurrencyResolver(project_dir)
    key = ArtifactKey.episode_storyboard(1, "E1S01")

    assert resolver.compare(key, artifact_path="storyboards/scene_E1S01.png").status is ArtifactStatus.CURRENT
    assert resolver.compare(key, artifact_path="storyboards/scene_E1S01.png").status is ArtifactStatus.CURRENT
    assert calls == 1


def test_runtime_single_episode_resolution_ignores_a_malformed_sibling_script(tmp_path: Path) -> None:
    project_dir, project, _step1, _script = _project(tmp_path)
    migrate_v7_to_v8(project_dir)
    project = _read_json(project_dir / "project.json")
    project["episodes"].append(
        {
            "episode": 2,
            "title": "第二集",
            "script_file": "scripts/episode_2.json",
        }
    )
    _write_json(project_dir / "project.json", project)
    (project_dir / "scripts" / "episode_2.json").write_text("{broken", encoding="utf-8")

    key = ArtifactKey.episode_storyboard(1, "E1S01")
    resolver = ArtifactCurrencyResolver(project_dir)

    assert resolver.compare(key, artifact_path="storyboards/scene_E1S01.png").status is ArtifactStatus.CURRENT
    assert (
        register_current_resource_artifact(
            project_dir,
            resource_type="storyboards",
            resource_id="E1S01",
            script_file="scripts/episode_1.json",
        )
        is False
    )


def test_formal_script_registration_failure_restores_script_and_project(tmp_path: Path, monkeypatch) -> None:
    project_dir, _project_data, _step1, script = _project(tmp_path)
    migrate_v7_to_v8(project_dir)
    pm = ProjectManager(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    project_path = project_dir / "project.json"
    before = (script_path.read_bytes(), project_path.read_bytes())
    script["title"] = "must roll back"

    def _fail(*_args, **_kwargs):
        raise RuntimeError("injected manifest failure")

    monkeypatch.setattr("lib.artifact_activation.register_artifact_entries_atomically", _fail)

    with pytest.raises(RuntimeError, match="injected manifest failure"):
        pm.save_script("demo", script, "episode_1.json", validate=False)

    assert (script_path.read_bytes(), project_path.read_bytes()) == before


def test_formal_step1_registration_failure_restores_the_previous_file(tmp_path: Path, monkeypatch) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    migrate_v7_to_v8(project_dir)
    formal_path = project_dir / "drafts" / "episode_1" / "step1_reference_units.json"

    def _fail(*_args, **_kwargs):
        raise RuntimeError("injected manifest failure")

    monkeypatch.setattr("lib.artifact_activation.register_current_artifact_if_provable", _fail)

    with pytest.raises(RuntimeError, match="injected manifest failure"):
        with ProjectManager(tmp_path).file_lock(formal_path):
            from lib.script_review import write_step1_locked

            write_step1_locked(project_dir, 1, {"units": [{"unit_id": "E1U1"}]})

    assert not formal_path.exists()


def test_formal_step1_write_serializes_with_schema_last_activation(tmp_path: Path, monkeypatch) -> None:
    from lib import artifact_activation
    from lib.script_review import write_step1_json

    project_dir, _project_data, step1, _script = _project(tmp_path)
    formal_path = project_dir / "drafts" / "episode_1" / "step1_segments.json"
    replacement = {"segments": [{"novel_text": "activation overlap replacement"}]}
    activation_ready = Event()
    release_activation = Event()
    writer_done = Event()
    failures: list[BaseException] = []
    original_commit_schema = artifact_activation._commit_schema_version

    def _pause_before_schema(project_dir_arg, project):
        activation_ready.set()
        assert release_activation.wait(timeout=5)
        original_commit_schema(project_dir_arg, project)

    monkeypatch.setattr(artifact_activation, "_commit_schema_version", _pause_before_schema)

    def _activate() -> None:
        try:
            artifact_activation.activate_artifact_target_state(project_dir, bump_schema=True)
        except Exception as exc:
            failures.append(exc)

    def _write() -> None:
        try:
            write_step1_json(project_dir, 1, formal_path, replacement)
        except Exception as exc:
            failures.append(exc)
        finally:
            writer_done.set()

    activation_thread = Thread(target=_activate)
    writer_thread = Thread(target=_write)
    activation_thread.start()
    assert activation_ready.wait(timeout=5)
    writer_thread.start()
    writer_done.wait(timeout=0.2)
    release_activation.set()
    activation_thread.join(timeout=5)
    writer_thread.join(timeout=5)

    assert not activation_thread.is_alive()
    assert not writer_thread.is_alive()
    assert failures == []
    assert _read_json(formal_path) == replacement
    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.episode_step1(1),
        artifact_path="drafts/episode_1/step1_segments.json",
    )
    assert comparison.status is ArtifactStatus.CURRENT
    assert step1 != replacement


def test_formal_step1_transaction_holds_the_project_lock_through_the_write(tmp_path: Path) -> None:
    from lib.formal_write import project_metadata_lock
    from lib.script_review import formal_step1_write_transaction

    project_dir, _project_data, _step1, _script = _project(tmp_path)
    formal_path = project_dir / "drafts" / "episode_1" / "step1_segments.json"
    transaction_entered = Event()
    release_transaction = Event()
    competing_lock_acquired = Event()
    failures: list[BaseException] = []

    def _write() -> None:
        try:
            with ProjectManager(tmp_path).file_lock(formal_path):
                with formal_step1_write_transaction(project_dir, 1, formal_path):
                    transaction_entered.set()
                    assert release_transaction.wait(timeout=5)
        except Exception as exc:
            failures.append(exc)

    def _compete() -> None:
        try:
            with project_metadata_lock(project_dir):
                competing_lock_acquired.set()
        except Exception as exc:
            failures.append(exc)

    writer = Thread(target=_write)
    competitor = Thread(target=_compete)
    writer.start()
    assert transaction_entered.wait(timeout=5)
    competitor.start()
    try:
        assert not competing_lock_acquired.wait(timeout=0.1)
    finally:
        release_transaction.set()
    writer.join(timeout=5)
    competitor.join(timeout=5)

    assert not writer.is_alive()
    assert not competitor.is_alive()
    assert competing_lock_acquired.is_set()
    assert failures == []


def test_v7_preflight_failure_writes_no_manifest_schema_or_backups(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    script_path.write_text("{broken", encoding="utf-8")
    project_before = (project_dir / "project.json").read_bytes()

    with pytest.raises(ValueError, match="episode script"):
        migrate_project_dir(project_dir)

    assert (project_dir / "project.json").read_bytes() == project_before
    assert not (project_dir / MANIFEST_FILENAME).exists()
    assert not list(project_dir.rglob("*.bak.v7-*"))


def test_v7_activation_replaces_an_interrupted_backup_on_retry(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    project_path = project_dir / "project.json"
    project_before = project_path.read_bytes()
    (project_dir / "project.json.bak.v7-interrupted").write_bytes(b"partial")

    migrate_v7_to_v8(project_dir)

    assert any(backup.read_bytes() == project_before for backup in project_dir.glob("project.json.bak.v7-*"))


def test_task_registration_receipt_restores_only_its_own_current_claim(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, script = _project(tmp_path)
    migrate_v7_to_v8(project_dir)
    key = ArtifactKey.episode_storyboard(1, "E1S01")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    previous = adapter.get_entry(key)
    assert previous is not None

    script["segments"][0]["image_prompt"] = "阿离撑伞站在雨中"
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    receipt = register_task_current_resource_artifact(
        project_dir,
        resource_type="storyboards",
        resource_id="E1S01",
        script_file="episode_1.json",
    )
    registered = adapter.get_entry(key)
    assert registered is not None and registered != previous

    receipt.compensate_cancelled()
    receipt.compensate_cancelled()
    assert adapter.get_entry(key) == previous

    adapter.put_entry(key, registered)
    later = ArtifactManifestEntry(
        artifact_path=registered.artifact_path,
        basis_digest="sha256-v1:" + "f" * 64,
    )
    adapter.put_entry(key, later)
    receipt.compensate_cancelled()
    assert adapter.get_entry(key) == later


def test_v7_activation_does_not_backfill_sheet_with_dangling_declared_reference(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    (project_dir / "products" / "refs" / "咖啡.png").unlink()

    migrate_v7_to_v8(project_dir)

    assert ArtifactKey.asset_sheet("product", "咖啡").encode() not in _stored_entries(project_dir)


def test_v7_activation_backfills_formal_step1_before_final_script_exists(tmp_path: Path) -> None:
    project_dir, _project_data, step1, _script = _project(tmp_path)
    (project_dir / "scripts" / "episode_1.json").unlink()

    migrate_v7_to_v8(project_dir)

    entries = _stored_entries(project_dir)
    assert (
        entries[ArtifactKey.episode_step1(1).encode()]["basis_digest"]
        == build_step1_basis(
            "雨夜",
            project=_read_json(project_dir / "project.json"),
        ).digest
    )
    assert ArtifactKey.episode_script(1).encode() not in entries
    assert step1 == _read_json(project_dir / "drafts" / "episode_1" / "step1_segments.json")


def test_v7_activation_does_not_use_unowned_same_name_storyboard_as_previous_input(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, script = _project(tmp_path)
    script["segments"][0]["generated_assets"] = {}
    script["segments"].append(
        {
            "segment_id": "E1S02",
            "image_prompt": "雨巷尽头",
            "video_prompt": "镜头前推",
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S02.png"},
        }
    )
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    (project_dir / "storyboards" / "scene_E1S02.png").write_bytes(b"second")

    migrate_v7_to_v8(project_dir)

    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(ArtifactKey.episode_storyboard(1, "E1S02"))
    assert entry == ArtifactManifestEntry(
        artifact_path="storyboards/scene_E1S02.png",
        basis_digest=build_storyboard_image_visual_basis(
            resource_id="E1S02",
            image_prompt="雨巷尽头",
            style="水墨",
            aspect_ratio="9:16",
            references=(),
        ).digest,
    )


def test_v7_activation_rejects_symlinked_project_control_file_without_writes(tmp_path: Path) -> None:
    project_dir, project, _step1, _script = _project(tmp_path)
    project_path = project_dir / "project.json"
    external = tmp_path / "external-project.json"
    _write_json(external, project)
    project_path.unlink()
    try:
        project_path.symlink_to(external)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(ArtifactManifestError, match="safely|symlink"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(external)["schema_version"] == 7
    assert not (project_dir / MANIFEST_FILENAME).exists()
    assert not list(project_dir.rglob("*.bak.v7-*"))


def test_v7_schema_commit_failure_leaves_complete_manifest_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    from lib import artifact_activation

    original = artifact_activation._commit_schema_version

    def fail_schema(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected schema failure")

    monkeypatch.setattr(artifact_activation, "_commit_schema_version", fail_schema)
    with pytest.raises(OSError, match="injected schema failure"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    monkeypatch.setattr(artifact_activation, "_commit_schema_version", original)

    migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before


def test_v7_schema_promotion_does_not_overwrite_a_concurrent_project_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    from lib import artifact_activation

    original_assert = artifact_activation._assert_preflight_unchanged
    final_check_reached = Event()
    writer_finished = Event()
    writer_errors: list[BaseException] = []
    check_count = 0

    def _update_project() -> None:
        final_check_reached.wait()
        try:
            ProjectManager(tmp_path).update_project(
                "demo",
                lambda project: project.update({"title": "Concurrent writer"}),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    def _assert_then_release_writer(project_path: Path, plan) -> None:
        nonlocal check_count
        original_assert(project_path, plan)
        check_count += 1
        if check_count == 3:
            final_check_reached.set()
            writer_finished.wait(timeout=0.2)

    writer = Thread(target=_update_project)
    writer.start()
    monkeypatch.setattr(artifact_activation, "_assert_preflight_unchanged", _assert_then_release_writer)

    migrate_v7_to_v8(project_dir)

    writer.join(timeout=2)
    assert not writer.is_alive()
    assert writer_errors == []
    promoted = _read_json(project_dir / "project.json")
    assert promoted["schema_version"] == 8
    assert promoted["title"] == "Concurrent writer"


def test_script_save_rechecks_manifest_activation_inside_the_project_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, script = _project(tmp_path)
    pm = ProjectManager(tmp_path)
    replacement = {
        **script,
        "segments": [
            {
                **script["segments"][0],
                "segment_id": "E1S02",
                "generated_assets": {},
            }
        ],
    }
    commit_reached = Event()
    release_save = Event()
    save_errors: list[BaseException] = []
    original_commit = pm._commit_script_unlocked

    def _commit_after_activation(*args: object, **kwargs: object) -> Path:
        commit_reached.set()
        release_save.wait()
        return original_commit(*args, **kwargs)  # type: ignore[arg-type]

    def _save() -> None:
        try:
            pm.save_script("demo", replacement, "episode_1.json", validate=False)
        except BaseException as exc:  # pragma: no cover - asserted below
            save_errors.append(exc)

    monkeypatch.setattr(pm, "_commit_script_unlocked", _commit_after_activation)
    writer = Thread(target=_save)
    writer.start()
    assert commit_reached.wait(timeout=2)
    try:
        migrate_v7_to_v8(project_dir)
        assert (
            ProjectArtifactManifestAdapter(project_dir).get_entry(ArtifactKey.episode_storyboard(1, "E1S01"))
            is not None
        )
    finally:
        release_save.set()

    writer.join(timeout=2)
    assert not writer.is_alive()
    assert save_errors == []
    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert _read_json(project_dir / "scripts" / "episode_1.json")["segments"][0]["segment_id"] == "E1S02"
    assert ProjectArtifactManifestAdapter(project_dir).get_entry(ArtifactKey.episode_storyboard(1, "E1S01")) is None


def test_v7_activation_restores_manifest_when_a_dependency_changes_after_its_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    script_path = project_dir / "scripts" / "episode_1.json"
    orphan_path = project_dir / "output" / "orphan.srt"
    orphan_path.parent.mkdir()
    orphan_path.write_text("old claim", encoding="utf-8")
    ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register(
        ArtifactKey.episode_subtitle(1, "E1S01", "post_production"),
        artifact_path="output/orphan.srt",
        basis=ArtifactBasis.build("old/subtitle", kind_version=1, inputs={}),
    )
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    concurrent_script = _read_json(script_path)
    concurrent_script["segments"][0]["novel_text"] = "并发改写"
    from lib import artifact_activation

    original_replace = artifact_activation.ProjectArtifactManifestAdapter.replace_entries_atomically

    def _replace_then_edit_dependency(self, entries):
        changed = original_replace(self, entries)
        _write_json(script_path, concurrent_script)
        return changed

    monkeypatch.setattr(
        artifact_activation.ProjectArtifactManifestAdapter,
        "replace_entries_atomically",
        _replace_then_edit_dependency,
    )

    with pytest.raises(RuntimeError, match="artifact activation dependency changed after preflight"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert _read_json(script_path) == concurrent_script
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before


def test_v7_activation_rejects_two_artifact_keys_that_own_one_formal_path(tmp_path: Path) -> None:
    project_dir, _project_data, _step1, script = _project(tmp_path)
    script["segments"].append(
        {
            "segment_id": "E1S02",
            "image_prompt": "同一张正式图的第二个身份",
            "video_prompt": "镜头前推",
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
        }
    )
    _write_json(project_dir / "scripts" / "episode_1.json", script)

    with pytest.raises(ValueError, match="formal artifact path.*multiple keys"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert not (project_dir / MANIFEST_FILENAME).exists()
    assert not list(project_dir.rglob("*.bak.v7-*"))


def test_v7_activation_restores_manifest_when_a_formal_image_changes_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    sheet_path = project_dir / "characters" / "阿离.png"
    orphan_path = project_dir / "output" / "orphan.srt"
    orphan_path.parent.mkdir()
    orphan_path.write_text("old claim", encoding="utf-8")
    ArtifactManifest(ProjectArtifactManifestAdapter(project_dir)).register(
        ArtifactKey.episode_subtitle(1, "E1S01", "post_production"),
        artifact_path="output/orphan.srt",
        basis=ArtifactBasis.build("old/subtitle", kind_version=1, inputs={}),
    )
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    original_replace = ProjectArtifactManifestAdapter.replace_entries_atomically

    def _replace_after_formal_image_change(self, entries):
        sheet_path.write_bytes(b"concurrent-sheet")
        return original_replace(self, entries)

    monkeypatch.setattr(
        ProjectArtifactManifestAdapter,
        "replace_entries_atomically",
        _replace_after_formal_image_change,
    )

    with pytest.raises(RuntimeError, match="artifact activation dependency changed after preflight"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert sheet_path.read_bytes() == b"concurrent-sheet"
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before


def test_v7_activation_restores_manifest_when_typed_media_changes_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    _write_verified_presentation_claims(project_dir)
    video_path = project_dir / "videos" / "scene_E1S01.mp4"
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()
    original_replace = ProjectArtifactManifestAdapter.replace_entries_atomically

    def _replace_after_typed_media_change(self, entries):
        changed = original_replace(self, entries)
        video_path.write_bytes(b"concurrent-video")
        return changed

    monkeypatch.setattr(
        ProjectArtifactManifestAdapter,
        "replace_entries_atomically",
        _replace_after_typed_media_change,
    )

    with pytest.raises(RuntimeError, match="artifact activation dependency changed after preflight"):
        migrate_v7_to_v8(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 7
    assert video_path.read_bytes() == b"concurrent-video"
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before


def test_v7_activation_retry_refreshes_matching_backups_before_startup_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir, _project_data, _step1, _script = _project(tmp_path)
    original_replace = ProjectArtifactManifestAdapter.replace_entries_atomically
    attempts = 0

    def _fail_first_manifest_commit(self, entries):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected manifest failure")
        return original_replace(self, entries)

    monkeypatch.setattr(
        ProjectArtifactManifestAdapter,
        "replace_entries_atomically",
        _fail_first_manifest_commit,
    )
    with pytest.raises(OSError, match="injected manifest failure"):
        migrate_v7_to_v8(project_dir)

    backups = list(project_dir.rglob("*.bak.v7-*"))
    assert backups
    expired = time.time() - 8 * 86400
    for backup in backups:
        os.utime(backup, (expired, expired))

    migrate_v7_to_v8(project_dir)
    cleanup_stale_backups(tmp_path, max_age_days=7)

    assert _read_json(project_dir / "project.json")["schema_version"] == 8
    assert all(backup.exists() for backup in backups)


def test_workflow_uses_the_activation_asset_identity_for_legacy_whitespace(tmp_path: Path) -> None:
    project_dir, project, _step1, _script = _project(tmp_path)
    raw_name = "  阿离  "
    project["characters"] = {raw_name: project["characters"]["阿离"]}
    _write_json(project_dir / "project.json", project)

    migrate_v7_to_v8(project_dir)

    status = WorkflowStateService(ProjectManager(tmp_path)).get_status("demo")
    characters = status.artifacts["asset_sheets"]["character"]
    assert characters["current_ids"] == [raw_name]
    assert characters["missing_ids"] == []


def test_v7_activation_uses_only_selected_complete_typed_media_facts(tmp_path: Path) -> None:
    project_dir = tmp_path / "ad"
    project_dir.mkdir()
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 7,
            "content_mode": "ad",
            "generation_mode": "reference_video",
            "style": "写实",
            "aspect_ratio": "9:16",
            "target_duration": 30,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        },
    )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "duration_seconds": 8,
                    "shots": [{"text": "产品掠过画面"}],
                    "references": [],
                    "generated_assets": {
                        "video_clip": "reference_videos/E1U1.mp4",
                        "source_signature": "legacy-must-not-be-read",
                    },
                },
                {
                    "unit_id": "E1U2",
                    "duration_seconds": 8,
                    "shots": [{"text": "旧视频"}],
                    "references": [],
                    "needs_replan": True,
                    "generated_assets": {"video_clip": "reference_videos/E1U2.mp4"},
                },
                {
                    "unit_id": "E1U3",
                    "duration_seconds": 8,
                    "shots": [{"text": "旧旁白"}],
                    "references": [],
                    "generated_assets": {"narration_audio": "audio/segment_E1U3.wav"},
                },
                {
                    "unit_id": "E1U4",
                    "duration_seconds": 8,
                    "shots": [{"text": "{新旁白}"}],
                    "references": [],
                    "generated_assets": {"narration_audio": "audio/segment_E1U4.wav"},
                },
                {
                    "unit_id": "E1U5",
                    "duration_seconds": 8,
                    "shots": [{"text": "{伪造快照旁白}"}],
                    "references": [],
                    "generated_assets": {"narration_audio": "audio/segment_E1U5.wav"},
                },
            ],
        },
    )
    versions = VersionManager(project_dir)
    for resource_id in ("E1U1", "E1U2"):
        current = project_dir / "reference_videos" / f"{resource_id}.mp4"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(resource_id.encode())
        facts = _reference_video_facts(resource_id)
        versions.add_version(
            "reference_videos",
            resource_id,
            "paid",
            source_file=current,
            execution_checkpoint_schema_version=3,
            execution_duration_seconds=8,
            execution_request_digest="a" * 64,
            execution_script_file="episode_1.json",
            execution_provider_media=[],
            artifact_video_currency=facts.to_dict(),
        )

    legacy_audio = project_dir / "audio" / "segment_E1U3.wav"
    legacy_audio.parent.mkdir(parents=True, exist_ok=True)
    legacy_audio.write_bytes(b"legacy")
    versions.add_version("audio", "E1U3", "legacy", source_file=legacy_audio)

    typed_audio = project_dir / "audio" / "segment_E1U4.wav"
    typed_audio.write_bytes(b"typed")
    settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
    audio_basis = build_narration_audio_basis_from_canonical_text("新旁白", settings)
    audio_descriptor = ArtifactBasisDescriptor.from_basis(audio_basis)
    versions.add_version(
        "audio",
        "E1U4",
        "新旁白",
        source_file=typed_audio,
        artifact_episode=1,
        artifact_audio_basis=audio_descriptor.to_dict(),
        execution_script_file="episode_1.json",
        tts_actual_duration_seconds=5.0,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=audio_descriptor.digest,
    )

    forged_audio = project_dir / "audio" / "segment_E1U5.wav"
    forged_audio.write_bytes(b"forged")
    forged_basis = build_narration_audio_basis_from_canonical_text("伪造快照旁白", settings)
    forged_descriptor = ArtifactBasisDescriptor.from_basis(forged_basis)
    versions.add_version(
        "audio",
        "E1U5",
        "伪造快照旁白",
        source_file=forged_audio,
        artifact_episode=1,
        artifact_audio_basis=forged_descriptor.to_dict(),
        execution_script_file="episode_1.json",
        tts_actual_duration_seconds=5.0,
        tts_provider_id=settings.provider_id,
        tts_model_id=settings.model_id,
        tts_voice=settings.voice,
        tts_speed=settings.speed,
        tts_basis_digest=forged_descriptor.digest,
    )
    versions_payload = _read_json(versions.versions_file)
    versions_payload["audio"]["E1U5"]["versions"][0]["file"] = "audio/segment_E1U5.wav"
    _write_json(versions.versions_file, versions_payload)

    migrate_v7_to_v8(project_dir)

    entries = _stored_entries(project_dir)
    assert (
        entries[ArtifactKey.episode_script(1).encode()]["basis_digest"]
        == build_ad_episode_script_basis(
            1,
            project=_read_json(project_dir / "project.json"),
        ).digest
    )
    assert (
        entries[ArtifactKey.episode_video(1, "E1U1").encode()]["basis_digest"]
        == _reference_video_facts("E1U1").video_descriptor.digest
    )
    assert ArtifactKey.episode_video(1, "E1U2").encode() not in entries
    assert ArtifactKey.episode_audio(1, "E1U3").encode() not in entries
    assert entries[ArtifactKey.episode_audio(1, "E1U4").encode()]["basis_digest"] == audio_descriptor.digest
    assert ArtifactKey.episode_audio(1, "E1U5").encode() not in entries

    resolver = ArtifactCurrencyResolver(project_dir)
    assert (
        resolver.compare(
            ArtifactKey.episode_video(1, "E1U1"),
            artifact_path="reference_videos/E1U1.mp4",
        ).status
        is ArtifactStatus.CURRENT
    )
    assert (
        resolver.compare(
            ArtifactKey.episode_audio(1, "E1U4"),
            artifact_path="audio/segment_E1U4.wav",
        ).status
        is ArtifactStatus.CURRENT
    )

    changed_script = _read_json(project_dir / "scripts" / "episode_1.json")
    changed_script["video_units"][0]["shots"] = [{"text": "新品掠过画面"}]
    changed_script["video_units"][3]["shots"] = [{"text": "{修改旁白}"}]
    _write_json(project_dir / "scripts" / "episode_1.json", changed_script)
    resolver = ArtifactCurrencyResolver(project_dir)
    assert (
        resolver.compare(
            ArtifactKey.episode_video(1, "E1U1"),
            artifact_path="reference_videos/E1U1.mp4",
        ).status
        is ArtifactStatus.STALE
    )
    assert (
        resolver.compare(
            ArtifactKey.episode_audio(1, "E1U4"),
            artifact_path="audio/segment_E1U4.wav",
        ).status
        is ArtifactStatus.STALE
    )


def test_v7_activation_does_not_use_same_name_storyboard_residue_for_video_basis(tmp_path: Path) -> None:
    project_dir = tmp_path / "ad"
    project_dir.mkdir()
    project = {
        "schema_version": 7,
        "content_mode": "ad",
        "generation_mode": "storyboard",
        "style": "写实",
        "aspect_ratio": "9:16",
        "target_duration": 30,
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    shot = {
        "shot_id": "E1S01",
        "duration_seconds": 4,
        "voiceover_text": "",
        "image_prompt": "产品特写",
        "video_prompt": "产品缓慢旋转",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "generated_assets": {"video_clip": "videos/scene_E1S01.mp4"},
    }
    _write_json(project_dir / "project.json", project)
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "ad", "shots": [shot]},
    )
    storyboard = project_dir / "storyboards" / "scene_E1S01.png"
    storyboard.parent.mkdir(parents=True)
    storyboard.write_bytes(b"unowned-residue")
    video = project_dir / "videos" / "scene_E1S01.mp4"
    video.parent.mkdir()
    video.write_bytes(b"paid-video")

    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=shot["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(admit_script_unit("shots", shot).preparation, voices=())
    duration = build_video_duration_basis(4)
    facts = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4,),
        reference_image_limit=None,
        parent_version=0,
    )
    VersionManager(project_dir).add_version(
        "videos",
        "E1S01",
        "paid",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file="episode_1.json",
        execution_provider_media=[],
        artifact_video_currency=facts.to_dict(),
    )

    migrate_v7_to_v8(project_dir)

    entries = _stored_entries(project_dir)
    assert ArtifactKey.episode_storyboard(1, "E1S01").encode() not in entries
    assert ArtifactKey.episode_video(1, "E1S01").encode() not in entries


def test_schema8_workflow_keeps_a_stale_typed_video_usable(tmp_path: Path) -> None:
    project_dir = tmp_path / "ad"
    project_dir.mkdir()
    project = {
        "schema_version": 7,
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "grid_storyboard": False,
        "style": "写实",
        "aspect_ratio": "9:16",
        "target_duration": 8,
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "广告",
        "content_mode": "ad",
        "video_units": [
            {
                "unit_id": "E1U1",
                "duration_seconds": 8,
                "shots": [{"text": "产品掠过画面"}],
                "references": [],
                "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"},
            }
        ],
    }
    _write_json(project_dir / "project.json", project)
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    current = project_dir / "reference_videos" / "E1U1.mp4"
    current.parent.mkdir()
    current.write_bytes(b"paid")
    VersionManager(project_dir).add_version(
        "reference_videos",
        "E1U1",
        "paid",
        source_file=current,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=8,
        execution_request_digest="a" * 64,
        execution_script_file="episode_1.json",
        execution_provider_media=[],
        artifact_video_currency=_reference_video_facts("E1U1").to_dict(),
    )
    migrate_v7_to_v8(project_dir)
    workflow = WorkflowStateService(ProjectManager(tmp_path))

    ready = workflow.get_status("ad")
    assert ready.state == "EXPORT_READY"
    assert ready.artifacts["videos"]["current_ids"] == ["E1U1"]

    script["video_units"][0]["shots"] = [{"text": "产品换成蓝色后掠过画面"}]
    _write_json(project_dir / "scripts" / "episode_1.json", script)
    stale = workflow.get_status("ad")
    assert stale.state == "EXPORT_READY"
    assert stale.artifacts["videos"]["stale_ids"] == ["E1U1"]
    assert stale.next_action.type == "export"


def test_v7_activation_backfills_grid_composite_and_split_members(tmp_path: Path) -> None:
    project_dir = tmp_path / "grid"
    project_dir.mkdir()
    project = {
        "schema_version": 7,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "grid_storyboard": True,
        "style": "水墨",
        "aspect_ratio": "9:16",
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
    }
    items = [
        {
            "segment_id": resource_id,
            "image_prompt": {"scene": scene, "composition": {"shot_type": "Medium Shot"}},
            "video_prompt": {"action": action},
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "generated_assets": {
                "storyboard_image": f"storyboards/scene_{resource_id}.png",
                "grid_id": "grid_123456789abc",
                "grid_cell_index": index,
            },
        }
        for index, (resource_id, scene, action) in enumerate((("E1S01", "雨巷", "转身"), ("E1S02", "门厅", "推门")))
    ]
    _write_json(project_dir / "project.json", project)
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "narration", "segments": items},
    )
    grid = GridGeneration(
        id="grid_123456789abc",
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02"],
        grid_image_path="grids/grid_123456789abc.png",
        rows=2,
        cols=2,
        cell_count=4,
        frame_chain=build_frame_chain(["E1S01", "E1S02"], 2, 2),
        status="completed",
        prompt="grid",
        provider="provider",
        model="model",
        grid_size="grid_4",
        created_at="2026-01-01T00:00:00Z",
        split_at="2026-01-01T00:01:00Z",
        video_aspect_ratio="9:16",
    )
    _write_json(project_dir / "grids" / f"{grid.id}.json", grid.to_dict())
    (project_dir / "grids" / f"{grid.id}.png").write_bytes(b"composite")
    (project_dir / "storyboards").mkdir()
    for resource_id in grid.scene_ids:
        (project_dir / "storyboards" / f"scene_{resource_id}.png").write_bytes(resource_id.encode())

    migrate_v7_to_v8(project_dir)

    members = tuple(
        GridStoryboardVisual(
            resource_id=item["segment_id"],
            image_prompt=item["image_prompt"],
            video_prompt=item["video_prompt"],
        )
        for item in items
    )
    composite = build_grid_composite_visual_basis(
        group_id=grid.id,
        members=members,
        rows=2,
        columns=2,
        style="水墨",
        grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
    )
    entries = _stored_entries(project_dir)
    assert entries[ArtifactKey.episode_grid(1, grid.id).encode()]["basis_digest"] == composite.digest
    for index, resource_id in enumerate(grid.scene_ids):
        member = build_grid_member_storyboard_visual_basis(
            group_id=grid.id,
            members=members,
            cell_index=index,
            composite_image=project_dir / "grids" / f"{grid.id}.png",
            rows=2,
            columns=2,
            style="水墨",
            member_aspect_ratio="9:16",
        )
        assert entries[ArtifactKey.episode_storyboard(1, resource_id).encode()]["basis_digest"] == member.digest
