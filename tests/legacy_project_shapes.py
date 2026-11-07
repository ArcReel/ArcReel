"""Project-directory shapes written by old code, shared by migration and read-side tests.

See ``docs/agents/project-migrations.md``, Known legacy shapes. Constructed directories stop at
the ``schema_version`` argument; ``advance_project_schema`` advances stepwise through the chain
to the requested version, simulating an already-upgraded installation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.version_manager import VERSION_CONTENT_DIGEST, VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from lib.project import project_trash
from lib.project.project_migrations.runner import MIGRATORS
from lib.project.source_revision import SourceScope, compute_source_revision
from lib.script.grid.models import GridGeneration, build_frame_chain
from lib.script.script_review import content_fingerprint
from lib.speech.narration_config import TtsSynthesisSettings, tts_snapshot_fields
from lib.speech.narration_delivery import build_narration_audio_basis
from lib.speech.speech_artifact_provenance import (
    SelectedMediaEvidence,
    build_video_duration_basis,
    build_video_speech_basis,
)
from lib.speech.speech_composition import admit_script_unit
from lib.speech.speech_presentation import (
    PresentationMedia,
    materialize_speech_presentation,
    presentation_artifact_paths,
)

_LEGACY_SNAPSHOT_TIMESTAMP = "20260302T145652"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_video_version_record(
    resource_type: str,
    resource_id: str,
    *,
    version: int = 1,
    prompt: str = "Action: 环顾四周\nCamera_Motion: Pan Right\n",
    duration_seconds: int | str = 4,
) -> dict[str, Any]:
    """One legacy video-version record without any typed provenance fields."""

    return {
        "version": version,
        "file": f"versions/{resource_type}/{resource_id}_v{version}_{_LEGACY_SNAPSHOT_TIMESTAMP}.mp4",
        "prompt": prompt,
        "created_at": "2026-03-02T14:56:52Z",
        "duration_seconds": duration_seconds,
    }


def _write_legacy_video(project_dir: Path, resource_type: str, resource_id: str, record: dict[str, Any]) -> None:
    content = f"provider-video-{resource_id}".encode()
    current = (
        project_dir
        / resource_type
        / (f"scene_{resource_id}.mp4" if resource_type == "videos" else f"{resource_id}.mp4")
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(content)
    snapshot = project_dir / record["file"]
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(content)


def _write_versions(project_dir: Path, buckets: dict[str, dict[str, dict[str, Any]]]) -> None:
    resource_types = (
        "storyboards",
        "end_frames",
        "videos",
        "characters",
        "scenes",
        "props",
        "products",
        "grids",
        "reference_videos",
        "audio",
    )
    data: dict[str, Any] = {resource_type: buckets.get(resource_type, {}) for resource_type in resource_types}
    _write_json(project_dir / "versions" / "versions.json", data)


def write_legacy_storyboard_project(
    root: Path,
    name: str = "legacy-storyboard",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1S1", "E1S2"),
) -> Path:
    """Legacy narration + storyboard project: complete storyboard images/videos, legacy-shaped version records."""

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧项目",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "style": "写实",
            "style_description": "电影感",
            "aspect_ratio": "9:16",
            "grid_storyboard": False,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    segments = []
    for index, unit_id in enumerate(unit_ids, start=1):
        segments.append(
            {
                "segment_id": unit_id,
                "episode": 1,
                "duration_seconds": 4,
                "novel_text": f"第{index}段旁白。",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {"scene": f"画面 {index}"},
                "video_prompt": {"action": f"动作 {index}", "camera_motion": "Pan Right"},
                "transition_to_next": "fade" if index == 1 else "cut",
                "generated_assets": {
                    "storyboard_image": f"storyboards/scene_{unit_id}.png",
                    "video_clip": f"videos/scene_{unit_id}.mp4",
                    "status": "completed",
                },
            }
        )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "1-7-0227.txt").write_text("第一段旁白。第二段旁白。", encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    draft_name = "step1_segments.md" if schema_version < 10 else "script_plan_segments.md"
    (drafts / draft_name).write_text("# 分段\n\n1. 第一段旁白。\n2. 第二段旁白。\n", encoding="utf-8")
    videos: dict[str, dict[str, Any]] = {}
    for index, unit_id in enumerate(unit_ids):
        (project_dir / "storyboards").mkdir(exist_ok=True)
        (project_dir / "storyboards" / f"scene_{unit_id}.png").write_bytes(f"storyboard-{unit_id}".encode())
        record = _legacy_video_version_record("videos", unit_id, duration_seconds="4" if index == 0 else 4)
        _write_legacy_video(project_dir, "videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
    _write_versions(project_dir, {"videos": videos})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_retired_flash_lite_project(root: Path, *, provider_id: str) -> Path:
    """Schema 16 project retaining Flash-Lite preview IDs in default/simple text-backend fields."""

    project_dir = write_legacy_storyboard_project(
        root,
        name=f"legacy-retired-flash-lite-{provider_id}",
    )
    advance_project_schema(project_dir, to_version=16)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    retired = f"{provider_id}/gemini-3.1-flash-lite-preview"
    project.update(
        {
            "default_text_backend": retired,
            "text_backend_simple": retired,
            "text_backend_complex": "gemini-aistudio/gemini-3-flash-preview",
        }
    )
    _write_json(project_path, project)
    return project_dir


def write_legacy_retired_model_references_project(root: Path) -> Path:
    """Schema 17 project: renamed Gemini image preview IDs and a retired video model without a successor.

    ``model_settings`` stores resolutions under old IDs; one successor ID has separate settings.
    """

    project_dir = write_legacy_storyboard_project(root, name="legacy-retired-model-references")
    advance_project_schema(project_dir, to_version=17)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.update(
        {
            "default_image_backend": "gemini-aistudio/gemini-3.1-flash-image-preview",
            "image_provider_i2i": "gemini-vertex/gemini-3-pro-image-preview",
            "video_backend": "openai/sora-2",
            "model_settings": {
                "gemini-aistudio/gemini-3.1-flash-image-preview": {"resolution": "4K"},
                "gemini-vertex/gemini-3-pro-image-preview": {"resolution": "2K"},
                "gemini-vertex/gemini-3-pro-image": {"resolution": "1K"},
            },
        }
    )
    _write_json(project_path, project)
    return project_dir


def write_legacy_timeline_agent_turn_project(root: Path) -> Path:
    """Schema 17 project whose editing-timeline revisions retain Agent-turn fields (one create and one edit revision)."""

    project_dir = write_legacy_storyboard_project(root, name="legacy-timeline-agent-turn")
    advance_project_schema(project_dir, to_version=17)
    clip = {"id": "c1", "unit_id": "E1S1", "source_volume": 1.0}
    _write_json(
        project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json",
        {
            "schema_version": 1,
            "id": "tl-0000abcd",
            "episode": 1,
            "name": "初剪",
            "created_at": "2026-09-01T00:00:00Z",
            "next_clip_number": 2,
            "revisions": [
                {
                    "number": 1,
                    "author": {"kind": "arcreel_agent"},
                    "summary": "按脚本新建",
                    "agent_turn": "user-entry-1",
                    "created_at": "2026-09-01T00:00:00Z",
                    "content": {"clips": [clip]},
                },
                {
                    "number": 2,
                    "parent": 1,
                    "author": {"kind": "creator", "user_id": "u1"},
                    "summary": "调整音量",
                    "agent_turn": None,
                    "created_at": "2026-09-02T00:00:00Z",
                    "content": {"clips": [{**clip, "source_volume": 0.6}]},
                    "changed_clip_ids": ["c1"],
                },
            ],
        },
    )
    return project_dir


def write_legacy_item_id_reuse_project(root: Path, name: str = "legacy-item-id-reuse") -> Path:
    """Schema 18 project: new items avoid only IDs still in the script; deleted items leave larger IDs elsewhere.

    The script contains E1S1/E1S2; deleted E1S5 survives only in version history, E1S7 in the project recycle
    bin deletion record, and a timeline clip still references deleted E1S9. Before upgrade, appending picks
    E1S3, joining the history of these old IDs.
    """

    project_dir = write_legacy_storyboard_project(root, name=name)
    advance_project_schema(project_dir, to_version=18)
    removed_image = project_dir / "storyboards" / "scene_E1S5.png"
    removed_image.write_bytes(b"removed-E1S5")
    VersionManager(project_dir).add_version("storyboards", "E1S5", "删掉的分镜", source_file=removed_image)
    removed_image.unlink()
    trashed_audio = project_dir / "audio" / "segment_E1S7.wav"
    trashed_audio.parent.mkdir(parents=True, exist_ok=True)
    trashed_audio.write_bytes(b"removed-E1S7")
    project_trash.record_deletion(
        project_dir,
        {"kind": "script_entry", "id": "E1S7", "episode": 1, "script_file": "episode_1.json", "item_kind": "segments"},
        ["audio/segment_E1S7.wav"],
    )
    _write_json(
        project_dir / "edit_timelines" / "episode_1" / "tl-00000e19.json",
        {
            "schema_version": 1,
            "id": "tl-00000e19",
            "episode": 1,
            "name": "初剪",
            "created_at": "2026-09-01T00:00:00Z",
            "next_clip_number": 3,
            "revisions": [
                {
                    "number": 1,
                    "author": {"kind": "creator", "user_id": "u1"},
                    "summary": "按脚本新建",
                    "created_at": "2026-09-01T00:00:00Z",
                    "content": {
                        "clips": [
                            {"id": "c1", "unit_id": "E1S1", "source_volume": 1.0},
                            {"id": "c2", "unit_id": "E1S9", "source_volume": 1.0},
                        ]
                    },
                }
            ],
        },
    )
    return project_dir


def write_legacy_episode_id_remnants_project(
    root: Path,
    name: str = "legacy-episode-id-remnants",
    *,
    schema_version: int = 7,
    record_remnant: Literal["version_history", "grid"] | None = None,
    asset_named_like_an_item: bool = False,
) -> Path:
    """Project from the ledger-max-plus-one era: only episode 1 remains in the ledger; higher IDs survive on disk.

    Reset/replanning once reused episode IDs. Removed ledger entries leave drafts (``drafts/episode_5/``),
    source backups (``source/_episode_6.txt.bak``), and media (``videos/scene_E8S01.mp4``).
    ``asset_named_like_an_item`` adds a character named like an item ID (``E12345678901234567A1``);
    asset images, version snapshots, and history all carry this name.
    """

    project_dir = write_legacy_storyboard_project(root, name, schema_version=schema_version)
    (project_dir / "drafts" / "episode_5").mkdir(parents=True)
    (project_dir / "drafts" / "episode_5" / "notes.txt").write_text("旧草稿", encoding="utf-8")
    (project_dir / "source" / "_episode_6.txt.bak").write_text("旧集原文", encoding="utf-8")
    (project_dir / "videos" / "scene_E8S01.mp4").write_bytes(b"orphan-video")
    if record_remnant == "version_history":
        versions_file = project_dir / "versions" / "versions.json"
        versions = json.loads(versions_file.read_text(encoding="utf-8")) if versions_file.exists() else {}
        versions.setdefault("videos", {})["E41S01"] = {"current_version": 1, "versions": []}
        _write_json(versions_file, versions)
    elif record_remnant == "grid":
        _write_json(project_dir / "grids" / "grid_old.json", {"episode": 41, "scene_ids": ["E41S01"]})
    if asset_named_like_an_item:
        asset = "E12345678901234567A1"
        snapshot = f"versions/characters/{asset}_v1_20250101T000000.png"
        (project_dir / "characters").mkdir(exist_ok=True)
        (project_dir / "characters" / f"{asset}.png").write_bytes(b"asset-sheet")
        (project_dir / "versions" / "characters").mkdir(parents=True, exist_ok=True)
        (project_dir / snapshot).write_bytes(b"asset-sheet")
        versions_file = project_dir / "versions" / "versions.json"
        versions = json.loads(versions_file.read_text(encoding="utf-8")) if versions_file.exists() else {}
        versions.setdefault("characters", {})[asset] = {
            "current_version": 1,
            "versions": [{"version": 1, "file": snapshot, "created_at": "2025-01-01T00:00:00"}],
        }
        _write_json(versions_file, versions)
    return project_dir


LEGACY_CHAPTER_TEN = "第十章开头。第十章中段。第十章结尾。"
LEGACY_CHAPTER_TWO = "第二章全文。"


def write_legacy_episode_sources_project(
    root: Path,
    name: str = "legacy-episode-sources",
    *,
    schema_version: int = 15,
    pre_split: bool = False,
    legacy_split: bool = False,
    edited_outside: bool = False,
    content_mode: str = "narration",
    source_kind: str = "novel",
) -> Path:
    """Project determining whole-book source by ``source/`` filename order and registering unlisted episode files on read.

    ``content_mode`` and project-level ``source_kind`` are written unchanged to ``project.json`` (schema ≤ 15 source-file types).

    Default: ``第10章.txt`` sorts before ``第2章.txt``; the ledger has two episodes, ``planning_cursor`` stops midway
    through ``第10章.txt``, and ``episode_7.txt`` has no ledger entry. With ``pre_split=True``, ``source/`` contains
    only user-split ``episode_1.txt`` / ``episode_2.txt`` and the ledger is empty.

    ``legacy_split=True``: two legacy-split episodes lack ``source_range``, source fingerprints, and cursors;
    ``source/`` retains ``_remaining.txt``. Episode 3 has a ledger entry but no episode file.
    ``edited_outside=True``: ``第10章.txt`` changed outside the service after fingerprint recording.
    """

    project_dir = root / name
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True)
    project: dict[str, Any] = {
        "schema_version": schema_version,
        "title": "旧分集项目",
        "content_mode": content_mode,
        "generation_mode": "storyboard",
        "source_kind": source_kind,
        "source_language": "zh",
        "style": "写实",
        "aspect_ratio": "9:16",
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": [],
    }
    if pre_split:
        (source_dir / "episode_1.txt").write_text("用户拆好的第一集。", encoding="utf-8")
        (source_dir / "episode_2.txt").write_text("用户拆好的第二集。", encoding="utf-8")
    else:
        (source_dir / "第10章.txt").write_text(LEGACY_CHAPTER_TEN, encoding="utf-8")
        (source_dir / "第2章.txt").write_text(LEGACY_CHAPTER_TWO, encoding="utf-8")
        first_end = LEGACY_CHAPTER_TEN.index("第十章中段")
        second_end = LEGACY_CHAPTER_TEN.index("第十章结尾")
        ranges = [(1, 0, first_end), (2, first_end, second_end)]
        for episode, start, end in ranges:
            (source_dir / f"episode_{episode}.txt").write_text(LEGACY_CHAPTER_TEN[start:end], encoding="utf-8")
            entry: dict[str, Any] = {
                "episode": episode,
                "title": f"第{episode}集",
                "script_file": f"scripts/episode_{episode}.json",
                "ledger_status": "planned",
            }
            if not legacy_split:
                entry["source_range"] = {"source_file": "source/第10章.txt", "start": start, "end": end}
            project["episodes"].append(entry)
        (source_dir / "episode_7.txt").write_text("另放进来的一集。", encoding="utf-8")
        if legacy_split:
            (source_dir / "_remaining.txt").write_text(LEGACY_CHAPTER_TEN[second_end:], encoding="utf-8")
            project["episodes"].append(
                {"episode": 3, "title": "第3集", "script_file": "scripts/episode_3.json", "ledger_status": "planned"}
            )
        else:
            project["planning_cursor"] = {"source_file": "source/第10章.txt", "offset": second_end}
            project["source_fingerprints"] = {
                "source/第10章.txt": hashlib.sha256(LEGACY_CHAPTER_TEN.encode("utf-8")).hexdigest()
            }
        if edited_outside:
            (source_dir / "第10章.txt").write_text(f"{LEGACY_CHAPTER_TEN}补写的一句。", encoding="utf-8")
    _write_json(project_dir / "project.json", project)
    return project_dir


def write_legacy_tts_narration_project(
    root: Path,
    name: str = "legacy-tts-narration",
    *,
    settings: tuple[TtsSynthesisSettings, ...] = (
        TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None),
        TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Ethan", 1.2),
    ),
    raised_video_duration_seconds: int | None = None,
) -> Path:
    """Add narration voiceover to ``write_legacy_storyboard_project``: one selected audio version per storyboard with complete TTS settings.

    Legacy ``project.json`` lacks narration delivery; audio backends use bare provider names, and blank voice/speed
    follow global defaults. Storyboard i uses ``settings[i]``; ``created_at`` increases in storyboard order.
    Nonempty ``raised_video_duration_seconds`` stops at schema 13: video requests round up to narration minimums
    while orchestration remains 4 seconds.
    """

    unit_ids = tuple(f"E1S{index}" for index in range(1, len(settings) + 1))
    project_dir = write_legacy_storyboard_project(root, name, unit_ids=unit_ids)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["audio_backend"] = "dashscope"
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    versions = VersionManager(project_dir)
    for index, (segment, synthesis) in enumerate(zip(script["segments"], settings, strict=True), start=1):
        unit_id = segment["segment_id"]
        audio_path = f"audio/segment_{unit_id}.wav"
        segment["generated_assets"]["narration_audio"] = audio_path
        speech = {key: value for key, value in segment.items() if key != "transition_to_next"}
        basis = ArtifactBasisDescriptor.from_basis(
            build_narration_audio_basis(admit_script_unit("segments", speech).preparation, synthesis)
        )
        audio = project_dir / audio_path
        audio.parent.mkdir(exist_ok=True)
        audio.write_bytes(f"tts-{unit_id}".encode())
        versions.add_version(
            "audio",
            unit_id,
            segment["novel_text"],
            source_file=audio,
            artifact_episode=1,
            artifact_audio_basis=basis.to_dict(),
            execution_script_file="episode_1.json",
            tts_actual_duration_seconds=3.0,
            tts_provider_id=synthesis.provider_id,
            tts_model_id=synthesis.model_id,
            tts_voice=synthesis.voice,
            tts_speed=synthesis.speed,
            tts_basis_digest=basis.digest,
        )
        metadata_path = project_dir / "versions" / "versions.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["audio"][unit_id]["versions"][-1]["created_at"] = f"2026-04-0{index}T10:00:00Z"
        # Versions 0.33 and earlier lack content fingerprints (backfilled in v17→v18).
        metadata["audio"][unit_id]["versions"][-1].pop(VERSION_CONTENT_DIGEST)
        _write_json(metadata_path, metadata)
    _write_json(script_path, script)
    if raised_video_duration_seconds is not None:
        advance_project_schema(project_dir, to_version=13)
        project = json.loads(project_path.read_bytes())
        project.update(tts_snapshot_fields(settings[-1]))
        _write_json(project_path, project)
        metadata_path = project_dir / "versions" / "versions.json"
        metadata = json.loads(metadata_path.read_bytes())
        adapter = ProjectArtifactManifestAdapter(project_dir)
        for unit_id in unit_ids:
            metadata["audio"][unit_id]["versions"][0]["tts_actual_duration_seconds"] = (
                raised_video_duration_seconds - 2.0
            )
            record = metadata["videos"][unit_id]["versions"][0]
            facts = VideoArtifactCurrencyFacts.from_dict(record["artifact_video_currency"])
            duration = build_video_duration_basis(raised_video_duration_seconds)
            facts = replace(
                facts,
                request_duration_seconds=raised_video_duration_seconds,
                duration_tiers=(4, raised_video_duration_seconds),
                duration_basis=duration,
                video_basis=compose_video_artifact_basis(
                    visual=facts.visual_basis, speech=facts.speech_basis, duration=duration
                ),
            )
            record.update(
                artifact_video_currency=facts.to_dict(),
                execution_checkpoint_schema_version=3,
                execution_duration_seconds=raised_video_duration_seconds,
                duration_seconds=raised_video_duration_seconds,
                execution_narration={"delivery": "use_tts"},
            )
            adapter.put_entry(
                ArtifactKey.episode_video(1, unit_id),
                ArtifactManifestEntry(f"videos/scene_{unit_id}.mp4", facts.video_basis.digest),
            )
        _write_json(metadata_path, metadata)
    return project_dir


def write_legacy_unregistrable_asset_sheet_project(root: Path) -> Path:
    """Legacy asset images include missing originals, empty descriptions, and derivatives dependent on unregistrable base assets."""

    project_dir = write_legacy_storyboard_project(root, "legacy-asset-sheet-unregistrable-input")
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"] = {
        "张三": {
            "description": "主角",
            "reference_image": "characters/refs/张三.png",
            "character_sheet": "characters/张三.png",
            "derivatives": {
                "劲装": {"description": "换上劲装", "character_sheet": "characters/derivatives/张三/劲装.png"}
            },
        },
        "李四": {
            "description": "配角",
            "character_sheet": "characters/李四.png",
            "derivatives": {
                "便装": {"description": "换上便装", "character_sheet": "characters/derivatives/李四/便装.png"}
            },
        },
    }
    project["scenes"] = {"祠堂": {"description": "", "scene_sheet": "scenes/祠堂.png"}}
    for relative in (
        "characters/张三.png",
        "characters/derivatives/张三/劲装.png",
        "characters/李四.png",
        "characters/derivatives/李四/便装.png",
        "scenes/祠堂.png",
    ):
        (project_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / relative).write_bytes(f"sheet-{relative}".encode())
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    _write_json(project_path, project)
    return project_dir


def write_legacy_reference_video_project(
    root: Path,
    name: str = "legacy-reference",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1U01", "E1U02"),
    with_legacy_audio: bool = False,
    style: str = "写实",
    style_description: str = "电影感",
    split_unit_text: bool = False,
) -> Path:
    """Legacy drama + reference_video project: direct video-unit output, legacy-shaped version records."""

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧参考生视频项目",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "source_kind": "novel",
            "source_language": "中文",
            "style": style,
            "style_description": style_description,
            "aspect_ratio": "9:16",
            "default_duration": 8,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    units = []
    for index, unit_id in enumerate(unit_ids, start=1):
        assets: dict[str, Any] = {"video_clip": f"reference_videos/{unit_id}.mp4", "status": "completed"}
        if with_legacy_audio:
            assets["narration_audio"] = f"audio/segment_{unit_id}.wav"
        units.append(
            {
                "unit_id": unit_id,
                "duration_seconds": 8,
                "text": f"第{index}个单元的画面描述。",
                "transition_to_next": "dissolve" if index == 1 else "cut",
                "generated_assets": assets,
            }
        )
    if split_unit_text:
        for unit in units:
            text = unit.pop("text")
            unit["shots"] = [{"shot_id": f"{unit['unit_id']}S1", "text": text}]
            unit["references"] = []
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "drama", "video_units": units},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "原著.txt").write_text("原著正文。", encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    draft_name = "step1_reference_units.md" if schema_version < 10 else "script_plan_reference_units.md"
    (drafts / draft_name).write_text("# 单元\n\n1. 第一个单元。\n2. 第二个单元。\n", encoding="utf-8")
    videos: dict[str, dict[str, Any]] = {}
    audio: dict[str, dict[str, Any]] = {}
    for unit_id in unit_ids:
        record = _legacy_video_version_record("reference_videos", unit_id, duration_seconds=8)
        _write_legacy_video(project_dir, "reference_videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
        if with_legacy_audio:
            wav = project_dir / "audio" / f"segment_{unit_id}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(f"tts-{unit_id}".encode())
            snapshot_rel = f"versions/audio/{unit_id}_v1_{_LEGACY_SNAPSHOT_TIMESTAMP}.wav"
            snapshot = project_dir / snapshot_rel
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(wav.read_bytes())
            audio[unit_id] = {
                "current_version": 1,
                "versions": [
                    {
                        "version": 1,
                        "file": snapshot_rel,
                        "prompt": "旁白",
                        "created_at": "2026-03-02T14:56:52Z",
                    }
                ],
            }
    _write_versions(project_dir, {"reference_videos": videos, "audio": audio})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_ad_reference_video_project(root: Path, *, converted: bool = False) -> Path:
    """Schema 6 advertisement legacy shot indexes, or interrupted state with shots converted but project version uncommitted."""

    project_dir = write_legacy_reference_video_project(root, schema_version=6)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_bytes())
    project["content_mode"] = "ad"
    project["target_duration"] = 16
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_bytes())
    script["content_mode"] = "ad"
    units = script.pop("video_units")
    script["duration_seconds"] = 16
    if converted:
        script["video_units"] = [
            {**unit, "text": f"镜头{index}；缓慢转动", "note": None} for index, unit in enumerate(units, start=1)
        ]
    else:
        script["shots"] = [
            {
                "shot_id": f"E1S{index}",
                "duration_seconds": 8,
                "image_prompt": {"scene": f"镜头{index}"},
                "video_prompt": {"action": "缓慢转动"},
                "transition_to_next": unit["transition_to_next"],
            }
            for index, unit in enumerate(units, start=1)
        ]
        script["reference_units"] = [
            {
                "unit_id": unit["unit_id"],
                "shot_ids": [f"E1S{index}"],
                "generated_assets": unit["generated_assets"],
            }
            for index, unit in enumerate(units, start=1)
        ]
    _write_json(script_path, script)
    return project_dir


def write_legacy_style_project(
    root: Path,
    name: str = "legacy-style",
    *,
    schema_version: int = 7,
    style: str = "画风：写实电影感",
    style_description: str = "淡彩",
    style_template_id: str | None = None,
) -> Path:
    """Legacy-style project with asset images, grids, and individual storyboard images: all four visual bases exist.

    ``style`` accepts values prefixed with 画风： or short labels such as ``Photographic``; omit ``style_template_id``
    when None, the prerequisite for short-label parsing. Grids and individual storyboard images coexist because
    only these two basis categories normalize style values, and both must be assertable.
    """

    project_dir = root / name
    project_dir.mkdir(parents=True)
    project: dict[str, Any] = {
        "schema_version": schema_version,
        "title": "旧风格项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "style": style,
        "style_description": style_description,
        "aspect_ratio": "9:16",
        "grid_storyboard": True,
        "characters": {"阿离": {"description": "银发旅人", "character_sheet": "characters/阿离.png"}},
        "scenes": {"雨巷": {"description": "湿漉石板路", "scene_sheet": "scenes/雨巷.png"}},
        "props": {},
        "products": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    if style_template_id is not None:
        project["style_template_id"] = style_template_id
    _write_json(project_dir / "project.json", project)

    grid_id = "grid_123456789abc"
    grid_members = ("E1S01", "E1S02")
    segments: list[dict[str, Any]] = [
        {
            "segment_id": resource_id,
            "episode": 1,
            "duration_seconds": 4,
            "novel_text": f"{resource_id} 的旁白。",
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "image_prompt": {"scene": f"{resource_id} 的画面", "composition": {"shot_type": "Medium Shot"}},
            "video_prompt": {"action": f"{resource_id} 的动作"},
            "transition_to_next": "cut",
            "generated_assets": {
                "storyboard_image": f"storyboards/scene_{resource_id}.png",
                "grid_id": grid_id,
                "grid_cell_index": index,
            },
        }
        for index, resource_id in enumerate(grid_members)
    ]
    segments.append(
        {
            "segment_id": "E1S03",
            "episode": 1,
            "duration_seconds": 4,
            "novel_text": "第三段旁白。",
            "segment_break": True,
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "image_prompt": {
                "scene": "巷口回望",
                "composition": {"shot_type": "Wide Shot", "lighting": "夜色", "ambiance": "清冷"},
            },
            "video_prompt": {"action": "回望"},
            "transition_to_next": "fade",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S03.png", "status": "completed"},
        }
    )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments},
    )
    _write_json(
        project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": [{"novel_text": "雨夜"}]}
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "episode_1.txt").write_text("雨夜", encoding="utf-8")

    grid = GridGeneration(
        id=grid_id,
        episode=1,
        script_file="episode_1.json",
        scene_ids=list(grid_members),
        grid_image_path=f"grids/{grid_id}.png",
        rows=2,
        cols=2,
        cell_count=4,
        frame_chain=build_frame_chain(list(grid_members), 2, 2),
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
    for resource_id in (*grid_members, "E1S03"):
        (project_dir / "storyboards" / f"scene_{resource_id}.png").write_bytes(resource_id.encode())
    (project_dir / "characters").mkdir()
    (project_dir / "characters" / "阿离.png").write_bytes(b"character-sheet")
    (project_dir / "scenes").mkdir()
    (project_dir / "scenes" / "雨巷.png").write_bytes(b"scene-sheet")
    _write_versions(project_dir, {})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_named_reference_bases_project(root: Path, name: str = "legacy-named-references") -> Path:
    """Schema 18 project whose storyboard/grid/cropped-storyboard bases retain asset names (removed from v19).

    Build on :func:`write_legacy_style_project`: all three storyboards reference 阿离, identified by ``@[阿离]``
    in visual descriptions; the grid freezes the same character image. Advance through v18 so the manifest registers
    names under that policy. Then write selected v18-style version records (frozen bases) for the grid and E1S03;
    write two E1S01 versions and select version 2.
    """

    project_dir = write_legacy_style_project(root, name, style="写实电影感")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    for segment in script["segments"]:
        segment["characters_in_segment"] = ["阿离"]
        segment["image_prompt"]["scene"] = f"@[阿离]站在{segment['segment_id']}的雨巷里"
    _write_json(script_path, script)
    grid_path = next((project_dir / "grids").glob("grid_*.json"))
    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    grid["reference_images"] = [{"path": "characters/阿离.png", "name": "阿离", "ref_type": "character"}]
    _write_json(grid_path, grid)
    advance_project_schema(project_dir, to_version=18)

    project_bytes = (project_dir / "project.json").read_bytes()
    planner = TargetStatePlanner(project_dir, project_bytes=project_bytes)
    planner.plan()
    versions_path = project_dir / "versions" / "versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    for bucket, resource_id, key, source in (
        ("grids", grid["id"], ArtifactKey.episode_grid(1, grid["id"]), f"grids/{grid['id']}.png"),
        ("storyboards", "E1S03", ArtifactKey.episode_storyboard(1, "E1S03"), "storyboards/scene_E1S03.png"),
        ("storyboards", "E1S01", ArtifactKey.episode_storyboard(1, "E1S01"), "storyboards/scene_E1S01.png"),
    ):
        records = []
        for version in range(1, 3 if resource_id == "E1S01" else 2):
            snapshot = f"versions/{bucket}/{resource_id}_v{version}_20260101T000000.png"
            (project_dir / snapshot).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / snapshot).write_bytes((project_dir / source).read_bytes())
            records.append(
                {
                    "version": version,
                    "file": snapshot,
                    "prompt": resource_id,
                    "created_at": f"2026-01-0{version}T00:00:00Z",
                    IMAGE_ARTIFACT_BASIS_FIELD: planner.bases[key].to_evidence_dict(),
                }
            )
        versions[bucket][resource_id] = {"current_version": len(records), "versions": records}
    _write_json(versions_path, versions)
    return project_dir


ScriptPlanVariantName = Literal["drama", "narration", "reference_video"]

#: Retired item/episode fingerprints: samples require field presence only; values are immaterial.
_LEGACY_ENTRY_REVISION = "sha256-legacy-entry-revision"
_LEGACY_SCRIPT_REVISION = "sha256-legacy-script-revision"


def _legacy_plan_entry(variant: ScriptPlanVariantName, episode: int, index: int) -> dict[str, Any]:
    if variant == "reference_video":
        return {
            "unit_id": f"E{episode}U{index:02d}",
            "text": f"第{episode}集第{index}个单元的画面。",
            "duration_seconds": 8,
            "source_text": f"第{episode}集第{index}段原文。",
        }
    if variant == "narration":
        return {
            "segment_id": f"E{episode}S{index:02d}",
            "novel_text": f"第{episode}集第{index}段旁白。",
            "duration_seconds": 4,
            "segment_break": False,
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
        }
    return {
        "scene_id": f"E{episode}S{index:02d}",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": [],
        "scenes": [],
        "props": [],
        "scene_description": f"第{episode}集第{index}镜的视觉改编。",
        "utterances": [{"kind": "voiceover", "speaker": None, "text": f"第{episode}集第{index}句旁白。"}],
        "source_text": f"第{episode}集第{index}段原文。",
    }


def _legacy_script_entry(
    variant: ScriptPlanVariantName, plan_entry: dict[str, Any], *, authored: bool, with_revisions: bool
) -> dict[str, Any]:
    """One item in an existing formal script: ``authored=False`` is the legacy state with both visual sides empty and no pending-authoring marker."""

    entry = dict(plan_entry)
    if variant == "reference_video":
        # Legacy reference units lack corresponding source text.
        entry.pop("source_text")
    else:
        if variant == "drama":
            # Legacy drama storyboards lack visual-adaptation descriptions.
            entry.pop("scene_description")
        entry["image_prompt"] = {"scene": "画面", "composition": {"shot_type": "Medium Shot"}} if authored else None
        entry["video_prompt"] = {"action": "动作"} if authored else None
    if with_revisions:
        entry["script_plan_entry_revision"] = _LEGACY_ENTRY_REVISION
    entry["transition_to_next"] = "cut"
    return entry


def write_legacy_script_plan_project(
    root: Path,
    name: str = "legacy-script-plan",
    *,
    variant: ScriptPlanVariantName,
    schema_version: int = 14,
    source_kind: str = "novel",
) -> Path:
    """Script-plan project from the item-fingerprint era, with three legacy episode shapes to adopt in v15.

    ``source_kind`` is the project-level source-file type (schema ≤ 15; v15→v16 moves it to source-file records).

    - Episode 1: confirmed; formal script has item/episode fingerprints; item 2 has an empty visual layer without
      a pending-authoring marker; drama storyboards lack visual adaptation, reference units lack source text.
    - Episode 2: confirmed, but its bound formal script is absent (legacy prompt authoring produced it; confirmation did not).
    - Episode 3: formal script and script plan, no confirmation record (grandfather episode).

    For ``schema_version < 10``, drafts, confirmation records, and episode fingerprints use pre-v9 names; omit
    item fingerprints, which did not yet exist. Chain step v7→v8 activates manifest backfill.
    """

    content_mode = "narration" if variant == "narration" else "drama"
    generation_mode = "reference_video" if variant == "reference_video" else "storyboard"
    legacy_names = schema_version < 10
    plan_filename = {
        "drama": "normalized_script.json",
        "narration": "segments.json",
        "reference_video": "reference_units.json",
    }[variant]
    plan_filename = f"{'step1' if legacy_names else 'script_plan'}_{plan_filename}"
    review_field = "step1_review" if legacy_names else "script_plan_review"
    metadata_field = "step1_revision" if legacy_names else "script_plan_revision"
    with_revisions = schema_version >= 14
    items_key = {"drama": "scenes", "narration": "segments", "reference_video": "units"}[variant]
    script_items_key = "video_units" if variant == "reference_video" else items_key

    project_dir = root / name
    project_dir.mkdir(parents=True)
    episodes: list[dict[str, Any]] = []
    for episode in (1, 2, 3):
        plan_entries = [_legacy_plan_entry(variant, episode, index) for index in (1, 2)]
        plan: dict[str, Any] = {items_key: plan_entries}
        if variant == "drama":
            plan["title"] = f"规划第{episode}集"
        plan_path = project_dir / "drafts" / f"episode_{episode}" / plan_filename
        _write_json(plan_path, plan)
        (project_dir / "source").mkdir(exist_ok=True)
        (project_dir / "source" / f"episode_{episode}.txt").write_text(f"第{episode}集原文。", encoding="utf-8")
        script_file = f"scripts/episode_{episode}.json"
        ledger: dict[str, Any] = {"episode": episode, "title": f"第{episode}集", "script_file": script_file}
        if episode in (1, 2):
            ledger[review_field] = {
                "fingerprint": content_fingerprint(plan_path),
                "confirmed_at": "2026-01-01T00:00:00Z",
            }
        if episode in (1, 3):
            script: dict[str, Any] = {
                "episode": episode,
                "title": f"第{episode}集",
                "content_mode": content_mode,
                script_items_key: [
                    _legacy_script_entry(variant, entry, authored=index == 0, with_revisions=with_revisions)
                    for index, entry in enumerate(plan_entries)
                ],
                "metadata": {metadata_field: content_fingerprint(plan_path)},
            }
            _write_json(project_dir / script_file, script)
        episodes.append(ledger)
    project: dict[str, Any] = {
        "schema_version": schema_version,
        "title": "旧脚本规划项目",
        "content_mode": content_mode,
        "generation_mode": generation_mode,
        "source_kind": source_kind,
        "source_language": "中文",
        "style": "写实",
        "style_description": "电影感",
        "aspect_ratio": "9:16",
        "default_duration": 8,
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": episodes,
    }
    _write_json(project_dir / "project.json", project)
    _write_versions(project_dir, {})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_drama_storyboard_project(
    root: Path,
    name: str = "legacy-drama-storyboard",
    *,
    schema_version: int = 7,
) -> Path:
    """Drama project without ``aspect_ratio`` in ``project.json``; episode 1's first storyboard already has an image.

    Script-plan/formal-script shapes match ``write_legacy_script_plan_project(variant="drama")``.
    """

    project_dir = write_legacy_script_plan_project(root, name, variant="drama", schema_version=schema_version)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.pop("aspect_ratio")
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    first = script["scenes"][0]
    storyboard = f"storyboards/scene_{first['scene_id']}.png"
    first["generated_assets"] = {"storyboard_image": storyboard, "status": "completed"}
    _write_json(script_path, script)
    (project_dir / storyboard).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / storyboard).write_bytes(f"storyboard-{first['scene_id']}".encode())
    return project_dir


def _mark_asset_inventory_current(project_dir: Path) -> None:
    """Legacy projects all completed book-wide asset extraction; ``project.json`` retains an asset-inventory marker matching the source then."""

    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    _write_json(project_path, project)


def write_undescribed_style_bases_project(
    root: Path,
    name: str,
    *,
    route: Literal["grid", "reference_video"],
    style: str = "写实电影感",
    style_description: str = "胶片颗粒，低饱和",
    schema_version: Literal[12, 13] = 13,
) -> Path:
    """Custom-style project stopped at v12/v13: grid, cropped-storyboard, or reference-video bases lack style descriptions.

    Version records freeze typed bases from 0.27, when basis construction omitted ``style_description``. Complete
    migration with an empty description to register all artifacts, then add it; the manifest/version records match
    historical shapes. Samples stopped at v12 represent 0.27–0.29 projects; disk shapes match, differing only in
    ``schema_version``. Asset/individual-storyboard bases always included descriptions: after identical construction
    they are stale even before migration, because descriptions were added after registration. ``style`` accepts
    legacy values, combining that state with description backfill.
    """

    if route == "grid":
        project_dir = write_legacy_style_project(root, name, style=style, style_description="")
    else:
        project_dir = write_legacy_reference_video_project(root, name, style=style, style_description="")
    advance_project_schema(project_dir, to_version=13)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["style_description"] = style_description
    project["schema_version"] = schema_version
    _write_json(project_path, project)
    return project_dir


def bind_episode_script_to_filename(project_dir: Path, episode: int, filename: str) -> None:
    """SSE index sync once bound arbitrary ``scripts/*.json`` containing an integer ``episode`` (schema ≤ 14).

    Rename the episode script to ``scripts/<filename>`` and rebind; its manifest registration follows the new name.
    """

    script_file = f"scripts/{filename}"
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    ledger = next(entry for entry in project["episodes"] if entry["episode"] == episode)
    source = project_dir / ledger["script_file"]
    if source.is_file():
        source.rename(project_dir / script_file)
    ledger["script_file"] = script_file
    _write_json(project_path, project)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = ArtifactKey.episode_script(episode)
    entry = adapter.get_entry(key)
    if entry is not None:
        adapter.put_entry(key, ArtifactManifestEntry(artifact_path=script_file, basis_digest=entry.basis_digest))


def _load_json_object(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ledger_entry(project: dict[str, Any], episode: int) -> dict[str, Any]:
    return next(entry for entry in project["episodes"] if entry["episode"] == episode)


def add_duplicate_ledger_entry(project_dir: Path, episode: int, filename: str) -> None:
    """Legacy SSE index sync registered self-reported episode numbers, allowing multiple entries per number (schema ≤ 14).

    Copy the script to ``scripts/<filename>`` and add a same-number ledger entry bound to the copy.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    entry = _ledger_entry(project, episode)
    copy_rel = f"scripts/{filename}"
    (project_dir / copy_rel).write_bytes((project_dir / entry["script_file"]).read_bytes())
    project["episodes"].append({**entry, "script_file": copy_rel})
    _write_json(project_path, project)


def share_episode_script(project_dir: Path, episode: int, *, with_episode: int) -> None:
    """Legacy sync used self-reported episode numbers: changing one and syncing bound two episode numbers to one script.

    Register ``with_episode`` in the ledger, bound to the script for ``episode``.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    entry = _ledger_entry(project, episode)
    project["episodes"].append(
        {"episode": with_episode, "title": f"第{with_episode}集", "script_file": entry["script_file"]}
    )
    _write_json(project_path, project)


def set_script_episode(project_dir: Path, episode: int, value: int | None) -> None:
    """Legacy sync/save accepted scripts with no top-level episode or one differing from the binding; ``None`` removes the field."""

    project = _load_json_object(project_dir / "project.json")
    script_path = project_dir / _ledger_entry(project, episode)["script_file"]
    script = _load_json_object(script_path)
    if value is None:
        script.pop("episode", None)
    else:
        script["episode"] = value
    _write_json(script_path, script)


def add_colliding_asset_name(project_dir: Path, bucket: str, name: str) -> str:
    """Before asset-name comparison coordinates (trim whitespace, Unicode NFC), two assets could normalize to the same name.

    Add an asset to ``bucket`` differing from ``name`` only by trailing whitespace; return the new name.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    colliding = f"{name} "
    project[bucket][colliding] = {"description": "同名的另一份"}
    _write_json(project_path, project)
    return colliding


def add_asset_names_differing_only_in_case(project_dir: Path, bucket: str, sheet_field: str, name: str) -> str:
    """Legacy versions allowed two assets in one table differing only by case.

    Append ``name`` and its uppercase variant to ``bucket``, each with an image (both land in one file on
    case-insensitive filesystems); return the variant name.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    variant = name.upper()
    for asset_name, content in ((name, b"original"), (variant, b"variant")):
        sheet = f"{bucket}/{asset_name}.png"
        project[bucket][asset_name] = {"description": f"{asset_name} 的描述", sheet_field: sheet}
        (project_dir / sheet).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / sheet).write_bytes(content)
    _write_json(project_path, project)
    return variant


def write_legacy_shared_reference_project(root: Path, *, padded_names: bool = False) -> Path:
    """Schema 5 cross-asset sharing: characters share images/audio, and a same-name product references the character image."""
    project_dir = root / "demo"
    owner, borrower = (" Alice ", " Bob ") if padded_names else ("Alice", "Bob")
    image, audio = f"characters/refs/{owner}.png", f"characters/refs_audio/{owner}.wav"
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": 5,
            "title": "旧共用参考文件项目",
            "characters": {
                owner: {"description": "owner", "reference_image": image, "reference_audio": audio},
                borrower: {"description": "borrower", "reference_image": image, "reference_audio": audio},
            },
            "scenes": {},
            "props": {},
            "products": {borrower: {"description": "product", "reference_images": [image]}},
            "episodes": [],
        },
    )
    for rel, content in ((image, b"image"), (audio, b"audio")):
        path = project_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return project_dir


def share_reference_files(project_dir: Path) -> dict[str, bytes]:
    """Legacy asset PATCH accepted arbitrary paths; reference images/audio could point under other asset names.

    Character 阿离 owns reference images/audio; added 阿青 points both fields there and owns another unreferenced
    image. Product 咖啡 owns its first original; added 茶 points its originals list there. Return each file's contents.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    contents = {
        "characters/refs/阿离.png": b"ali-image",
        "characters/refs_audio/阿离.wav": b"ali-voice",
        "characters/refs/阿青.png": b"qing-stray",
        "products/refs/咖啡_1.png": b"coffee-1",
    }
    for rel, content in contents.items():
        (project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / rel).write_bytes(content)
    shared = {"reference_image": "characters/refs/阿离.png", "reference_audio": "characters/refs_audio/阿离.wav"}
    project["characters"]["阿离"].update(shared)
    project["characters"]["阿青"] = {"description": "青衣少年", "character_sheet": "", **shared}
    project["products"]["咖啡"]["reference_images"] = ["products/refs/咖啡_1.png"]
    project["products"]["茶"] = {
        "description": "冷泡茶",
        "product_sheet": "",
        "reference_images": ["products/refs/咖啡_1.png"],
    }
    _write_json(project_path, project)
    return contents


def share_asset_sheets(project_dir: Path) -> dict[str, bytes]:
    """Legacy asset PATCH accepted arbitrary paths, allowing asset-image fields to reference another asset's image.

    阿离 owns a reference image; added 阿青 uses it as an asset image and owns another unreferenced asset image.
    Written before v8, whole-project v7→v8 activation registers 阿青's image at the shared path (no other registration
    exists there). Return reference-image contents; use a project with 阿离 such as ``write_legacy_storyboard_project``.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    reference = "characters/refs/阿离.png"
    (project_dir / reference).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / reference).write_bytes(b"ali-image")
    (project_dir / "characters/阿青.png").write_bytes(b"qing-stray")
    project["characters"]["阿离"]["reference_image"] = reference
    project["characters"]["阿青"] = {"description": "青衣少年", "character_sheet": reference}
    _write_json(project_path, project)
    return {reference: b"ali-image"}


def share_derivative_sheet(project_dir: Path) -> str:
    """Legacy PATCH allowed a base asset image to reference another asset's derivative image."""
    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    derivative = "characters/derivatives/阿离/雨衣.png"
    project["characters"]["阿离"]["derivatives"] = {"雨衣": {"description": "穿雨衣", "character_sheet": derivative}}
    project["characters"]["阿青"] = {"description": "青衣少年", "character_sheet": derivative}
    _write_json(project_path, project)
    (project_dir / derivative).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / derivative).write_bytes(b"raincoat")
    return derivative


def repoint_registered_asset_sheet(project_dir: Path) -> dict[str, bytes]:
    """After asset-image registration, PATCH points to another asset: scene 雨巷's image points to prop 伞's image.

    雨巷 retains its image/registration; mismatched paths read as missing. Two entries declare one image, and the
    planner before repointing rejects the whole project. Return both original images' contents, using a project
    with 雨巷/伞 and existing manifest registrations.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    contents = {rel: (project_dir / rel).read_bytes() for rel in ("scenes/雨巷.png", "props/伞.png")}
    project["scenes"]["雨巷"]["scene_sheet"] = "props/伞.png"
    _write_json(project_path, project)
    return contents


def refer_to_asset_in_episode(project_dir: Path, episode: int, field: str, name: str) -> None:
    """When two case-only-different assets coexist, scripts/drafts can use either name.

    Put ``name`` in the first storyboard's reference array ``field`` and visual-description ``@`` mention, and
    the first script-plan draft segment's same reference array (narration storyboard as in ``write_legacy_storyboard_project``).
    """

    project = _load_json_object(project_dir / "project.json")
    script_path = project_dir / _ledger_entry(project, episode)["script_file"]
    script = _load_json_object(script_path)
    segment = script["segments"][0]
    segment.setdefault(field, []).append(name)
    segment["image_prompt"] = f"@[{name}]{segment.get('image_prompt') or ''}"
    _write_json(script_path, script)
    draft_path = project_dir / "drafts" / f"episode_{episode}" / "script_plan_segments.json"
    draft = _load_json_object(draft_path)
    draft["segments"][0].setdefault(field, []).append(name)
    _write_json(draft_path, draft)


def break_script_item_ids(project_dir: Path, episode: int) -> None:
    """Degraded script saves could contain duplicate or missing item ids.

    After the first item, append one with the same id but a different prompt, and one without id or artifacts.
    """

    project = _load_json_object(project_dir / "project.json")
    script_path = project_dir / _ledger_entry(project, episode)["script_file"]
    script = _load_json_object(script_path)
    kind = next(key for key in ("segments", "scenes", "shots", "video_units") if key in script)
    id_field = {"segments": "segment_id", "scenes": "scene_id", "shots": "shot_id", "video_units": "unit_id"}[kind]
    first = script[kind][0]
    duplicate = {**first, "image_prompt": "另一镜"}
    missing = {key: value for key, value in first.items() if key != id_field}
    missing["generated_assets"] = {}
    script[kind] = [first, duplicate, missing]
    _write_json(script_path, script)


def duplicate_legacy_ad_ids(project_dir: Path) -> None:
    """Duplicate schema 6 advertisement legacy shot indexes: second shot/unit reuse the first's number."""

    script_path = project_dir / "scripts" / "episode_1.json"
    script = _load_json_object(script_path)
    script["shots"][1]["shot_id"] = script["shots"][0]["shot_id"]
    script["reference_units"][1]["unit_id"] = script["reference_units"][0]["unit_id"]
    script["reference_units"][1]["shot_ids"] = []
    _write_json(script_path, script)


def blank_reference_unit_shots(project_dir: Path, unit_id: str) -> None:
    """v8 units required at least one shot but no shot text: reconstructed text is empty while duration is nonzero."""

    script_path = project_dir / "scripts" / "episode_1.json"
    script = _load_json_object(script_path)
    unit = next(unit for unit in script["video_units"] if unit["unit_id"] == unit_id)
    unit.pop("text", None)
    unit["shots"] = [{"shot_id": f"{unit_id}S1", "text": ""}]
    unit["references"] = []
    _write_json(script_path, script)


def write_unreadable_grid_record(project_dir: Path, grid_id: str = "grid_0123456789ab") -> Path:
    """One corrupted grid record (invalid JSON)."""

    record = project_dir / "grids" / f"{grid_id}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_bytes(b"{not json")
    return record


def write_unreadable_script_plan_draft(project_dir: Path, episode: int, name: str) -> Path:
    """One corrupted script-plan draft (invalid JSON)."""

    draft = project_dir / "drafts" / f"episode_{episode}" / name
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_bytes(b"{not json")
    return draft


def write_non_utf8_episode_source(project_dir: Path, episode: int, text: str) -> Path:
    """Episode source is not UTF-8 (GBK uploaded without transcoding)."""

    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(text.encode("gbk"))
    return source


def write_legacy_presentation_project(
    root: Path,
    name: str = "legacy-presentation",
    *,
    transition: str = "fade",
    schema_version: int = 7,
) -> Path:
    """Narration project with its sole storyboard's post-production voiceover presentation model materialized; script/model both contain transitions.

    Historical presentation-model bases include ``transition_to_next``. Video-version records retain typed
    provenance; the model file self-validates, and migration-chain activation registers it and subtitle drafts.
    """

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧呈现模型项目",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "style": "水墨",
            "style_description": "",
            "aspect_ratio": "9:16",
            "grid_storyboard": False,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    item: dict[str, Any] = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "雨夜",
        "image_prompt": "阿离站在雨中",
        "video_prompt": "阿离转身",
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "transition_to_next": transition,
        "generated_assets": {
            "storyboard_image": "storyboards/scene_E1S01.png",
            "video_clip": "videos/scene_E1S01.mp4",
        },
    }
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": [item]},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "episode_1.txt").write_text("雨夜", encoding="utf-8")
    _write_json(
        project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": [{"novel_text": "雨夜"}]}
    )
    storyboard = project_dir / "storyboards" / "scene_E1S01.png"
    storyboard.parent.mkdir(parents=True)
    storyboard.write_bytes(b"storyboard")
    video = project_dir / "videos" / "scene_E1S01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"paid-video")

    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
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
    versions = VersionManager(project_dir)
    selected_version = versions.add_version(
        "videos",
        "E1S01",
        "paid",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file="episode_1.json",
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=facts.to_dict(),
    )
    selected = next(
        record
        for record in versions.get_versions("videos", "E1S01")["versions"]
        if record["version"] == selected_version
    )
    media = PresentationMedia(
        artifact_path=selected["file"],
        version=selected_version,
        selection="current",
        currency="current",
        evidence=SelectedMediaEvidence.from_file(
            basis=facts.video_basis, path=project_dir / selected["file"], actual_duration_seconds=4.0
        ),
    )
    presentation = materialize_speech_presentation(
        preparation, variant="post_production", video=media, provider_audio_enabled=True
    )
    legacy_basis = legacy_transition_presentation_basis(
        variant="post_production",
        transition=transition,
        video=media.evidence.basis_input(),
        subtitle=ArtifactBasisDescriptor.from_basis(presentation.subtitle_basis).to_dict(),
        narration_audio=None,
        provider_audio_enabled=True,
    )
    subtitle_path, presentation_path = presentation_artifact_paths(1, "E1S01", "post_production")
    _write_json(project_dir / subtitle_path, presentation.subtitle_artifact_dict())
    _write_json(
        project_dir / presentation_path,
        {
            "episode": 1,
            "resource_type": "videos",
            "script_file": "episode_1.json",
            "transition_to_next": transition,
            "subtitle_artifact_path": subtitle_path,
            "presentation_artifact_path": presentation_path,
            "persisted": True,
            **presentation.to_dict(),
            "presentation_basis": ArtifactBasisDescriptor.from_basis(legacy_basis).to_dict(),
        },
    )
    _mark_asset_inventory_current(project_dir)
    return project_dir


def legacy_transition_presentation_basis(
    *,
    variant: str,
    transition: str,
    video: dict[str, object],
    subtitle: dict[str, object],
    narration_audio: dict[str, object] | None,
    provider_audio_enabled: bool,
) -> ArtifactBasis:
    """Presentation-model basis computed by schema ≤ 15 code: input includes the script item's transition."""

    return ArtifactBasis.build(
        "artifact-speech/presentation",
        kind_version=2,
        inputs={
            "variant": variant,
            "transition_to_next": transition,
            "video": video,
            "subtitle": subtitle,
            "narration_audio": narration_audio,
            "mix_policy": {
                "kind": "provider-original-plus-optional-tts",
                "version": 1,
                "provider_video_gain": 1.0,
                "narration_audio_gain": 1.0,
                "provider_audio_enabled": provider_audio_enabled,
            },
        },
    )


def add_legacy_manual_uploads(
    project_dir: Path,
    *,
    video_unit: str,
    storyboard_unit: str,
    storyboard_shape: Literal["blank-prompt", "missing-sheet", "generated-basis"] = "blank-prompt",
) -> None:
    """Write an uploaded video/storyboard image using schema ≤ 15 upload behavior.

    Uploaded video selects one ``manual_upload`` version without manifest registration. Uploaded storyboard
    images lose registration when prompts are empty or referenced assets lack images; valid generation input
    keeps registration computed from that input.
    """

    versions_path = project_dir / "versions" / "versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    uploads = {
        ("videos", video_unit): (f"videos/scene_{video_unit}.mp4", b"uploaded-video"),
        ("storyboards", storyboard_unit): (f"storyboards/scene_{storyboard_unit}.png", b"uploaded-storyboard"),
    }
    for (resource_type, unit_id), (artifact_path, content) in uploads.items():
        history = versions.setdefault(resource_type, {}).setdefault(unit_id, {"current_version": 0, "versions": []})
        version = len(history["versions"]) + 1
        snapshot = (
            f"versions/{resource_type}/{unit_id}_v{version}_{_LEGACY_SNAPSHOT_TIMESTAMP}{Path(artifact_path).suffix}"
        )
        for path in (project_dir / artifact_path, project_dir / snapshot):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        history["versions"].append(
            {
                "version": version,
                "file": snapshot,
                "prompt": "",
                "created_at": "2026-09-01T00:00:00Z",
                "source": "manual_upload",
                "original_filename": Path(artifact_path).name,
            }
        )
        history["current_version"] = version
    _write_json(versions_path, versions)

    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    for segment in script["segments"]:
        if segment["segment_id"] == storyboard_unit:
            if storyboard_shape == "blank-prompt":
                segment["image_prompt"] = ""
            elif storyboard_shape == "missing-sheet":
                segment["characters_in_segment"] = ["Alice"]
    _write_json(script_path, script)
    if storyboard_shape == "missing-sheet":
        project_path = project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["characters"]["Alice"] = {"description": "银发少女", "character_sheet": None}
        _write_json(project_path, project)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {ArtifactKey.episode_video(1, video_unit): None}
    if storyboard_shape != "generated-basis":
        replacements[ArtifactKey.episode_storyboard(1, storyboard_unit)] = None
    adapter.replace_entries_if_matches_atomically(
        expected={},
        replacements=replacements,
    )


def without_version_content_digests(versions: dict[str, Any]) -> dict[str, Any]:
    """Remove v17→v18-backfilled content fingerprints from version records to assert earlier migrations preserve all other fields."""

    return {
        resource_type: {
            resource_id: {
                **resource,
                "versions": [
                    {key: value for key, value in record.items() if key != VERSION_CONTENT_DIGEST}
                    for record in resource["versions"]
                ],
            }
            for resource_id, resource in bucket.items()
        }
        for resource_type, bucket in versions.items()
    }


def advance_project_schema(project_dir: Path, *, to_version: int) -> None:
    """Advance a project stepwise along the migration chain from current ``schema_version`` to ``to_version``."""

    version = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["schema_version"]
    while version < to_version:
        MIGRATORS[version](project_dir)
        version += 1
    actual = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["schema_version"]
    if actual != to_version:
        raise AssertionError(f"schema advanced to {actual}, expected {to_version}")


__all__ = [
    "ScriptPlanVariantName",
    "add_legacy_manual_uploads",
    "advance_project_schema",
    "bind_episode_script_to_filename",
    "legacy_transition_presentation_basis",
    "refer_to_asset_in_episode",
    "repoint_registered_asset_sheet",
    "share_asset_sheets",
    "share_reference_files",
    "without_version_content_digests",
    "write_legacy_ad_reference_video_project",
    "write_legacy_drama_storyboard_project",
    "write_legacy_episode_id_remnants_project",
    "write_legacy_item_id_reuse_project",
    "write_legacy_named_reference_bases_project",
    "write_legacy_presentation_project",
    "write_legacy_reference_video_project",
    "write_legacy_retired_flash_lite_project",
    "write_legacy_retired_model_references_project",
    "write_legacy_script_plan_project",
    "write_legacy_storyboard_project",
    "write_legacy_style_project",
    "write_legacy_timeline_agent_turn_project",
    "write_legacy_tts_narration_project",
    "write_undescribed_style_bases_project",
]


def reuse_item_id_across_episodes(project_dir: Path, item_id: str) -> None:
    """Legacy script ids were deduplicated only within episodes: copied scripts could share IDs/media across episodes.

    Register episode 2 copied from episode 1; set both first items to ``item_id``, point storyboard references
    to its canonical path, and place one storyboard image there.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    first_path = project_dir / _ledger_entry(project, 1)["script_file"]
    first = _load_json_object(first_path)
    second = {**json.loads(json.dumps(first)), "episode": 2, "title": "第2集"}
    for script in (first, second):
        segment = script["segments"][0]
        segment["segment_id"] = item_id
        segment["generated_assets"]["storyboard_image"] = f"storyboards/scene_{item_id}.png"
    _write_json(first_path, first)
    _write_json(project_dir / "scripts" / "episode_2.json", second)
    (project_dir / "storyboards").mkdir(exist_ok=True)
    (project_dir / "storyboards" / f"scene_{item_id}.png").write_bytes(b"shared storyboard")
    project["episodes"].append({"episode": 2, "title": "第2集", "script_file": "scripts/episode_2.json"})
    _write_json(project_path, project)


def cross_bind_episode_scripts(project_dir: Path, filenames: dict[int, str], *, ledger_order: list[int]) -> None:
    """Legacy SSE sync bound self-reported episode numbers, allowing crossed filenames (e.g. episode 1 named ``episode_2.json``).

    Register episode 2 copied from episode 1, renumbering items and omitting media; rename scripts to
    ``scripts/<filenames[集号]>`` and rebind, ordering the ledger by ``ledger_order``. Episode 1's manifest
    script registration follows its new filename.
    """

    project_path = project_dir / "project.json"
    project = _load_json_object(project_path)
    first_path = project_dir / _ledger_entry(project, 1)["script_file"]
    first = _load_json_object(first_path)
    second = {**json.loads(json.dumps(first)), "episode": 2, "title": "第2集"}
    for segment in second["segments"]:
        segment["segment_id"] = segment["segment_id"].replace("E1", "E2", 1)
        segment["generated_assets"] = {}
    first_path.unlink()
    for episode, script in ((1, first), (2, second)):
        _write_json(project_dir / "scripts" / filenames[episode], script)
    entries = {entry["episode"]: entry for entry in project["episodes"]}
    entries.setdefault(2, {"episode": 2, "title": "第2集"})
    for episode in (1, 2):
        entries[episode]["script_file"] = f"scripts/{filenames[episode]}"
    project["episodes"] = [entries[episode] for episode in ledger_order]
    _write_json(project_path, project)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = ArtifactKey.episode_script(1)
    entry = adapter.get_entry(key)
    if entry is not None:
        adapter.put_entry(
            key, ArtifactManifestEntry(artifact_path=f"scripts/{filenames[1]}", basis_digest=entry.basis_digest)
        )
