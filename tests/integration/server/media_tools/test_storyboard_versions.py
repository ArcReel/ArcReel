"""A director can retain an ending guide while restoring the approved starting image."""

from pathlib import Path

import pytest
from PIL import Image

from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.visual_artifact_provenance import build_storyboard_image_visual_basis
from lib.project.project_manager import ProjectManager
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool


def _harness(root: Path, *, reference: bool = False) -> ToolHarness:
    pm = ProjectManager(root)
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo", "Demo", "Anime", "narration", extras={"generation_mode": "reference_video"} if reference else None
    )
    pm.add_episode("demo", 1, "One", "scripts/episode_1.json")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "novel_text": "A snack disappears.",
                    "image_prompt": "start",
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"},
                }
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return ToolHarness("demo", root, pm)


def _version(ctx: ToolHarness, color: str, prompt: str) -> bytes:
    current = ctx.project_path / "storyboards/scene_E1S01.png"
    current.parent.mkdir(exist_ok=True)
    Image.new("RGB", (16, 16), color).save(current)
    basis = build_storyboard_image_visual_basis(
        resource_id="E1S01", image_prompt=prompt, style="Anime", aspect_ratio="9:16"
    )
    VersionManager(ctx.project_path).add_version(
        "storyboards", "E1S01", prompt, source_file=current, artifact_image_basis=basis.to_evidence_dict()
    )
    return current.read_bytes()


async def test_restore_start_preserves_end_snapshot_versions_and_original_provenance(tmp_path: Path) -> None:
    ctx = _harness(tmp_path / "owned")
    other = _harness(tmp_path / "other")
    other_image = _version(other, "green", "other")
    start = _version(ctx, "red", "start")
    end = _version(ctx, "blue", "end")
    bound = await run_declared_tool(
        "set_video_end_frame",
        ctx,
        {"script": "episode_1.json", "shot_id": "E1S01", "source_path": "storyboards/scene_E1S01.png"},
    )
    assert bound.problem is None
    result = await run_declared_tool("select_storyboard_version", ctx, {"shot_id": "E1S01", "version": 1})
    assert result.problem is None
    assert result.value == {"shot_id": "E1S01", "current_version": 1, "file_path": "storyboards/scene_E1S01.png"}
    assert (ctx.project_path / result.value["file_path"]).read_bytes() == start
    assert (ctx.project_path / "end_frames/scene_E1S01.png").read_bytes() == end
    segment = ctx.pm.load_script("demo", "episode_1.json")["segments"][0]
    assert segment["generated_assets"]["storyboard_image"] == result.value["file_path"]
    assert segment["end_frame_image"] == "end_frames/scene_E1S01.png"
    versions = VersionManager(ctx.project_path).get_versions("storyboards", "E1S01")
    assert versions["current_version"] == 1
    assert len(versions["versions"]) == 2
    basis = build_storyboard_image_visual_basis(
        resource_id="E1S01", image_prompt="start", style="Anime", aspect_ratio="9:16"
    )
    claim = ProjectArtifactManifestAdapter(ctx.project_path).get_entry(ArtifactKey.episode_storyboard(1, "E1S01"))
    assert claim is not None
    assert claim.basis_digest == basis.digest
    assert (other.project_path / result.value["file_path"]).read_bytes() == other_image
    assert not (other.project_path / "end_frames/scene_E1S01.png").exists()


async def test_missing_version_reports_choices_without_changing_current(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    saved = _version(ctx, "red", "start")
    result = await run_declared_tool("select_storyboard_version", ctx, {"shot_id": "E1S01", "version": 9})
    assert problem_of(result).code == "version_not_found"
    assert problem_of(result).params == {"available_versions": [1]}
    assert (ctx.project_path / "storyboards/scene_E1S01.png").read_bytes() == saved
    assert VersionManager(ctx.project_path).get_current_version("storyboards", "E1S01") == 1


@pytest.mark.parametrize("shot_id", ["E1S99", "../../outside"])
async def test_unknown_shot_cannot_restore_orphan_version(tmp_path: Path, shot_id: str) -> None:
    ctx = _harness(tmp_path)
    result = await run_declared_tool("select_storyboard_version", ctx, {"shot_id": shot_id, "version": 1})
    assert problem_of(result).code == "segment_not_found"


async def test_reference_mode_does_not_select_unused_storyboard(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, reference=True)
    result = await run_declared_tool("select_storyboard_version", ctx, {"shot_id": "E1S01", "version": 1})
    assert problem_of(result).code == "segment_not_found"


@pytest.mark.parametrize("version", [0, -1, "1", True])
async def test_only_positive_integer_versions_are_accepted(tmp_path: Path, version: object) -> None:
    ctx = _harness(tmp_path)
    result = await run_declared_tool("select_storyboard_version", ctx, {"shot_id": "E1S01", "version": version})
    assert problem_of(result).code == "invalid_request"
