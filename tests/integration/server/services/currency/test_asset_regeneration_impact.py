"""单张重生一张资产图的连带影响：只数现在是现行、且生成输入里带着这张资产图的产物。

产物文件、输入依据与版本记录均落盘，现行和过期由真实产物清单判定。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.artifacts.artifact_currency import resolve_current_artifact_target
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.artifacts.visual_artifact_provenance import build_reference_video_artifact_visual_basis
from lib.project.asset_derivatives import derivative_artifact_key
from lib.project.project_manager import ProjectManager
from lib.script.reference_video.request_projection import (
    FilesystemReferenceAssets,
    hydrate_reference_assets,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit
from server.services.currency.asset_regeneration_impact import AssetRegenerationImpact, asset_regeneration_impact

PROJECT = "demo"


@pytest.fixture
def cast_projects(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project(PROJECT)
    manager.create_project_metadata(PROJECT, "Demo")
    manager.add_character(PROJECT, "Alice", "勇敢的少女")
    manager.add_character(PROJECT, "Bob", "沉默的剑客")

    def _derivatives(project: dict) -> None:
        project["characters"]["Alice"]["derivatives"] = {
            "战损": {"description": "衣服破损", "character_sheet": "characters/derivatives/Alice/战损.png"},
            "雨夜": {"description": "浑身湿透", "character_sheet": "characters/derivatives/Alice/雨夜.png"},
            "盛装": {"description": "礼服"},
        }

    manager.update_project(PROJECT, _derivatives)
    return manager


def _write_episode(cast_projects: ProjectManager, episode: int, script: dict) -> None:
    scripts = cast_projects.get_project_path(PROJECT) / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / f"episode_{episode}.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    cast_projects.add_episode(PROJECT, episode, f"第{episode}集", f"scripts/episode_{episode}.json")


def _segment(segment_id: str, cast: list[str], *, generated: bool = True) -> dict:
    assets = {"storyboard_image": f"storyboards/scene_{segment_id}.png"} if generated else {}
    return {
        "segment_id": segment_id,
        "novel_text": "庭院中行走",
        "image_prompt": "庭院中行走",
        "characters_in_segment": cast,
        "generated_assets": assets,
    }


def _unit(unit_id: str, text: str) -> dict:
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": 4,
        "generated_assets": {"video_clip": f"reference_videos/{unit_id}.mp4"},
    }


def _write_artifact(project_dir: Path, relative_path: str) -> Path:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    return path


def _claim_current(project_dir: Path, key: ArtifactKey) -> None:
    entry = resolve_current_artifact_target(project_dir, key)
    assert entry is not None
    ProjectArtifactManifestAdapter(project_dir).put_entry(key, entry)


def _claim_reference_video(cast_projects: ProjectManager, unit: dict) -> None:
    project_dir = cast_projects.get_project_path(PROJECT)
    project = cast_projects.load_project(PROJECT)
    hydration = hydrate_reference_assets(
        unit_reference_declarations(project, unit),
        resolve_reference_assets(project, project_dir, unit),
        FilesystemReferenceAssets(project_dir),
    )
    assert not hydration.missing
    visual = build_reference_video_artifact_visual_basis(
        unit=unit,
        request_assets=hydration.available,
        style=project.get("style"),
        style_description=project.get("style_description", ""),
        aspect_ratio=resolve_video_aspect_ratio(project),
    )
    speech = build_video_speech_basis(admit_script_unit("video_units", unit).preparation)
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=2,
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
    path = _write_artifact(project_dir, unit["generated_assets"]["video_clip"])
    VersionManager(project_dir).add_version(
        "reference_videos",
        unit["unit_id"],
        unit["text"],
        source_file=path,
        artifact_video_currency=currency.to_dict(),
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file="scripts/episode_2.json",
        execution_provider_media=[],
        execution_narration={"delivery": "post_production"},
    )
    _claim_current(project_dir, ArtifactKey.episode_video(2, unit["unit_id"]))


@pytest.fixture
def current_cast_artifacts(cast_projects: ProjectManager) -> ProjectManager:
    _write_episode(
        cast_projects,
        1,
        {
            "episode": 1,
            "segments": [
                _segment("E1S01", ["Alice"]),
                _segment("E1S02", ["Alice"]),
                _segment("E1S03", ["Bob"]),
                _segment("E1S04", ["Alice/战损"]),
                _segment("E1S05", ["Alice"], generated=False),
            ],
        },
    )
    project_dir = cast_projects.get_project_path(PROJECT)
    for name in ("Alice", "Bob"):
        relative_path = f"characters/{name}.png"
        _write_artifact(project_dir, relative_path)
        cast_projects.update_project_character_sheet(PROJECT, name, relative_path)
        _claim_current(project_dir, ArtifactKey.asset_sheet("character", name))
    for derivative in ("战损", "雨夜"):
        relative_path = f"characters/derivatives/Alice/{derivative}.png"
        _write_artifact(project_dir, relative_path)
        _claim_current(project_dir, derivative_artifact_key("Alice", derivative))
    for resource_id in ("E1S01", "E1S02", "E1S03", "E1S04"):
        _write_artifact(project_dir, f"storyboards/scene_{resource_id}.png")
        _claim_current(project_dir, ArtifactKey.episode_storyboard(1, resource_id))
    adapter = ProjectArtifactManifestAdapter(project_dir)
    for key, relative_path in (
        (ArtifactKey.episode_storyboard(1, "E1S02"), "storyboards/scene_E1S02.png"),
        (derivative_artifact_key("Alice", "雨夜"), "characters/derivatives/Alice/雨夜.png"),
    ):
        adapter.put_entry(key, ArtifactManifestEntry(artifact_path=relative_path, basis_digest=f"sha256-v1:{'a' * 64}"))
    units = [_unit("E2U1", "@[Alice] 推门"), _unit("E2U2", "@[Bob] 等候"), _unit("E2U3", "@[Alice/战损] 行走")]
    _write_episode(cast_projects, 2, {"episode": 2, "video_units": units})
    for unit in units:
        _claim_reference_video(cast_projects, unit)
    return cast_projects


def test_counts_current_storyboards_videos_and_derivatives(current_cast_artifacts: ProjectManager) -> None:
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice")

    assert impact == AssetRegenerationImpact(stale=False, storyboards=1, videos=1, derivatives=1)


def test_an_unknown_asset_raises_key_error(cast_projects: ProjectManager) -> None:
    with pytest.raises(KeyError):
        asset_regeneration_impact(cast_projects, PROJECT, "scene", "不存在")


def test_derivative_impact_counts_only_its_compound_references(current_cast_artifacts: ProjectManager) -> None:
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice", derivative_name="战损")

    assert impact == AssetRegenerationImpact(stale=False, storyboards=1, videos=1, derivatives=0)


def test_reports_whether_the_sheet_itself_is_stale_now(current_cast_artifacts: ProjectManager) -> None:
    """界面据此决定是否先确认连带影响，不依赖可能滞后的本地状态行。"""
    impact = asset_regeneration_impact(current_cast_artifacts, PROJECT, "character", "Alice", derivative_name="雨夜")

    assert impact.stale is True
