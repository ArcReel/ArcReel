"""The native tool must persist real, scoped snapshots without generating video."""

from pathlib import Path

import pytest
from PIL import Image

from lib.project.project_manager import ProjectManager
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool


def _harness(root: Path, *, reference: bool = False) -> ToolHarness:
    pm = ProjectManager(root)
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "Anime",
        "narration",
        extras={"generation_mode": "reference_video"} if reference else None,
    )
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "novel_text": "A snack disappears.", "duration_seconds": 4}],
        },
        "episode_1.json",
        validate=False,
    )
    return ToolHarness("demo", root, pm)


async def test_tool_snapshots_and_clears_in_injected_project_scope(tmp_path: Path) -> None:
    ctx = _harness(tmp_path / "owned")
    other = _harness(tmp_path / "other")
    source = ctx.project_path / "storyboards" / "end.jpg"
    source.parent.mkdir(exist_ok=True)
    Image.new("RGB", (16, 16), "red").save(source)
    args = {"script": "episode_1.json", "shot_id": "E1S01", "source_path": "storyboards/end.jpg"}

    result = await run_declared_tool("set_video_end_frame", ctx, args)
    assert result.problem is None
    assert result.value["end_frame_image"] == "end_frames/scene_E1S01.png"
    target = ctx.project_path / result.value["end_frame_image"]
    saved = target.read_bytes()
    assert saved.startswith(b"\x89PNG")
    source.unlink()
    assert target.read_bytes() == saved
    assert (
        ctx.pm.load_script("demo", "episode_1.json")["segments"][0]["end_frame_image"]
        == result.value["end_frame_image"]
    )
    assert not (other.project_path / "end_frames/scene_E1S01.png").exists()
    assert not other.pm.load_script("demo", "episode_1.json")["segments"][0].get("end_frame_image")

    cleared = await run_declared_tool("set_video_end_frame", ctx, {**args, "source_path": None})
    assert cleared.problem is None
    assert cleared.value["end_frame_image"] is None
    assert not target.exists()
    assert ctx.pm.load_script("demo", "episode_1.json")["segments"][0].get("end_frame_image") is None


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("../../outside.png", "invalid_end_frame_source"),
        ("missing.png", "end_frame_source_not_found"),
        ("", "invalid_end_frame_source"),
        ("invalid.png", "invalid_image_file"),
    ],
)
async def test_invalid_sources_do_not_change_shot(tmp_path: Path, source: str, code: str) -> None:
    ctx = _harness(tmp_path)
    (ctx.project_path / "invalid.png").write_bytes(b"not an image")
    result = await run_declared_tool(
        "set_video_end_frame",
        ctx,
        {
            "script": "episode_1.json",
            "shot_id": "E1S01",
            "source_path": source,
        },
    )
    assert problem_of(result).code == code
    assert not ctx.pm.load_script("demo", "episode_1.json")["segments"][0].get("end_frame_image")


async def test_omitting_source_is_not_a_clear_command(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    result = await run_declared_tool("set_video_end_frame", ctx, {"script": "episode_1.json", "shot_id": "E1S01"})
    assert problem_of(result).code == "invalid_request"


async def test_reference_video_does_not_accept_unused_ending_frame(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, reference=True)
    result = await run_declared_tool(
        "set_video_end_frame",
        ctx,
        {
            "script": "episode_1.json",
            "shot_id": "E1S01",
            "source_path": None,
        },
    )
    assert problem_of(result).code == "end_frame_reference_video_unsupported"


async def test_unknown_shot_cannot_create_orphan_snapshot(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    result = await run_declared_tool(
        "set_video_end_frame",
        ctx,
        {
            "script": "episode_1.json",
            "shot_id": "E1S99",
            "source_path": None,
        },
    )
    assert problem_of(result).code == "segment_not_found"
