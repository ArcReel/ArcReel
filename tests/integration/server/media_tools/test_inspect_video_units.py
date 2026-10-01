"""inspect_video_units：经声明入口调用，断言结果信封里的结构化结果与联系表图片。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.project.project_manager import ProjectManager
from server.agent_toolset.envelope import ToolEnvelope, encode_outcome
from server.agent_toolset.video_review import INSPECT_VIDEO_UNITS
from server.media_tools.video_review import MAX_FRAMES_PER_CALL
from tests.factories import install_current_video, make_signal_clip, run_bundled_ffmpeg
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool


def _harness(tmp_path: Path, unit_ids: list[str]) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "segments": [{"segment_id": unit_id, "novel_text": "旁白"} for unit_id in unit_ids],
    }
    pm.save_script("demo", script, "episode_1.json", validate=False)
    return ToolHarness("demo", tmp_path, pm)


def _solid_video(path: Path, color: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_bundled_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color={color}:size=160x90:rate=25:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )
    return path


def _install(ctx: ToolHarness, unit_id: str, color: str) -> int:
    source = _solid_video(ctx.project_path / ".staging" / f"{unit_id}-{color}.mp4", color)
    return install_current_video(ctx.project_path, "videos", unit_id, source)


async def _inspect(ctx: ToolHarness, arguments: dict[str, Any]) -> tuple[Any, ToolEnvelope]:
    outcome = await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, arguments)
    return outcome, encode_outcome(INSPECT_VIDEO_UNITS, outcome)


def _dominant(jpeg: bytes) -> str:
    """纯色素材的联系表里画面占绝大部分面积：按整张图的平均色判断画面是红还是蓝。"""
    red, _green, blue = (
        Image.open(io.BytesIO(jpeg)).convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    )  # type: ignore[misc]
    return "red" if red > blue else "blue"


async def test_inspecting_current_versions_returns_one_contact_sheet_image_per_unit(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01", "E1S02"])
    _install(ctx, "E1S01", "red")
    _install(ctx, "E1S02", "blue")

    outcome, envelope = await _inspect(ctx, {"unit_ids": ["E1S01", "E1S02"], "frames": 4})

    assert outcome.problem is None, outcome.problem
    result = envelope.structured["inspect_video_units"]
    assert result["model_review"] is None
    assert result["frames_per_unit"] == 4
    assert [(unit["unit_id"], unit["version"], unit["status"]) for unit in result["units"]] == [
        ("E1S01", 1, "ok"),
        ("E1S02", 1, "ok"),
    ]
    assert len(envelope.images) == 2
    assert all(image.mime_type == "image/jpeg" for image in envelope.images)
    for unit, expected in zip(result["units"], ("red", "blue"), strict=True):
        (sheet,) = unit["sheets"]
        assert len(sheet["times"]) == 4
        assert _dominant(envelope.images[sheet["image"] - 1].data) == expected


async def test_naming_an_older_version_shows_that_versions_picture(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])
    _install(ctx, "E1S01", "red")
    assert _install(ctx, "E1S01", "blue") == 2

    _outcome, older = await _inspect(ctx, {"unit_ids": ["E1S01"], "version": 1, "frames": 2})
    _outcome, current = await _inspect(ctx, {"unit_ids": ["E1S01"], "frames": 2})

    for envelope, version, expected in ((older, 1, "red"), (current, 2, "blue")):
        (unit,) = envelope.structured["inspect_video_units"]["units"]
        assert unit["version"] == version
        assert unit["available_versions"] == [1, 2]
        assert _dominant(envelope.images[0].data) == expected


async def test_signals_are_returned_with_marked_frames_and_cached_per_version(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])
    clip = tmp_path / "signals.mp4"
    make_signal_clip(clip)
    install_current_video(ctx.project_path, "videos", "E1S01", clip)

    first, envelope = await _inspect(ctx, {"unit_ids": ["E1S01"], "frames": 12})
    cache_files = sorted((ctx.project_path / ".cache" / "video_signals").rglob("*.json"))
    cache_stamp = [file.stat().st_mtime_ns for file in cache_files]
    second, _envelope = await _inspect(ctx, {"unit_ids": ["E1S01"], "frames": 12})

    (unit,) = envelope.structured["inspect_video_units"]["units"]
    signals = unit["signals"]
    assert signals["cuts"] == pytest.approx([1.0, 2.0, 3.0])
    assert [(span["start"], span["end"]) for span in signals["black"]] == [pytest.approx((1.0, 2.0))]
    assert [(span["start"], span["end"]) for span in signals["freeze"]] == [pytest.approx((3.0, 5.0))]
    assert signals["shots"] == 4
    (sheet,) = unit["sheets"]
    assert len(sheet["times"]) >= signals["shots"]
    assert {tag for frame in sheet["marked_frames"] for tag in frame["tags"]} == {"CUT", "BLACK", "FREEZE"}
    assert [file.name for file in cache_files] == ["E1S01_v1.json"]
    assert (first.value.units[0].signals_cached, second.value.units[0].signals_cached) == (False, True)
    assert [file.stat().st_mtime_ns for file in cache_files] == cache_stamp


async def test_each_version_caches_its_own_signals(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])
    clip = tmp_path / "signals.mp4"
    make_signal_clip(clip)
    install_current_video(ctx.project_path, "videos", "E1S01", clip)
    assert _install(ctx, "E1S01", "red") == 2

    older, _envelope = await _inspect(ctx, {"unit_ids": ["E1S01"], "version": 1, "frames": 2})
    current, _envelope = await _inspect(ctx, {"unit_ids": ["E1S01"], "frames": 2})
    again, _envelope = await _inspect(ctx, {"unit_ids": ["E1S01"], "version": 1, "frames": 2})

    assert [unit.signals_cached for result in (older, current, again) for unit in result.value.units] == [
        False,
        False,
        True,
    ]
    assert older.value.units[0].signals.cuts != ()
    assert current.value.units[0].signals.cuts == ()
    cache_dir = ctx.project_path / ".cache" / "video_signals" / "videos"
    assert sorted(file.name for file in cache_dir.iterdir()) == ["E1S01_v1.json", "E1S01_v2.json"]


async def test_a_unit_without_video_is_reported_without_images(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01", "E1S02"])
    _install(ctx, "E1S01", "red")

    outcome, envelope = await _inspect(ctx, {"unit_ids": ["E1S01", "E1S02"], "frames": 2})

    assert outcome.problem is None, outcome.problem
    units = envelope.structured["inspect_video_units"]["units"]
    assert [(unit["unit_id"], unit["status"], unit["sheets"]) for unit in units][1] == ("E1S02", "video_missing", [])
    assert len(envelope.images) == 1


async def test_unknown_units_are_refused_before_any_extraction(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])

    problem = problem_of(await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, {"unit_ids": ["E1S01", "E9S99"]}))

    assert problem.code == "video_unit_not_found"
    assert problem.params == {"unit_ids": ["E9S99"]}


async def test_a_missing_version_lists_the_units_available_versions(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])
    _install(ctx, "E1S01", "red")

    problem = problem_of(await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, {"unit_ids": ["E1S01"], "version": 3}))

    assert problem.code == "version_not_found"
    assert problem.params == {"unit_id": "E1S01", "available_versions": [1]}


async def test_the_per_call_frame_budget_is_shared_across_units(tmp_path: Path) -> None:
    unit_ids = [f"E1S{index:02d}" for index in range(1, 14)]
    ctx = _harness(tmp_path, unit_ids)
    source = _solid_video(tmp_path / "red.mp4", "red")
    for unit_id in unit_ids:
        install_current_video(ctx.project_path, "videos", unit_id, source)

    outcome, envelope = await _inspect(ctx, {"unit_ids": unit_ids, "frames": 12})

    assert outcome.problem is None, outcome.problem
    result = envelope.structured["inspect_video_units"]
    assert result["frames_per_unit"] == MAX_FRAMES_PER_CALL // 13
    total = sum(len(sheet["times"]) for unit in result["units"] for sheet in unit["sheets"])
    assert total <= MAX_FRAMES_PER_CALL


async def test_more_units_than_the_frame_budget_is_refused(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, ["E1S01"])
    unit_ids = [f"E1S{index:03d}" for index in range(MAX_FRAMES_PER_CALL + 1)]

    problem = problem_of(await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, {"unit_ids": unit_ids, "frames": 1}))

    assert problem.code == "frame_budget_exceeded"
    assert problem.params == {"max_frames_per_call": MAX_FRAMES_PER_CALL}


@pytest.mark.parametrize("frames", [0, 25])
async def test_frames_outside_the_per_unit_range_are_invalid(tmp_path: Path, frames: int) -> None:
    ctx = _harness(tmp_path, ["E1S01"])

    problem = problem_of(await run_declared_tool(INSPECT_VIDEO_UNITS, ctx, {"unit_ids": ["E1S01"], "frames": frames}))

    assert problem.code == "invalid_request"
