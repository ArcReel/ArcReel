from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifact_activation import ArtifactCurrencyResolver
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
from lib.project_migrations.runner import migrate_project_dir
from lib.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_storyboard_image_visual_basis,
)
from lib.workflow_state import WorkflowStateService

pytestmark = pytest.mark.integration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
