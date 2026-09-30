"""select_video_version 的 handler 行为：经声明入口调用，断言 ``ToolOutcome`` 与落盘的 current。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import ProjectManager
from server.services.project.project_events import ProjectEventService
from tests.factories import add_typed_video_version
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool

_STORYBOARD_SCRIPT = {
    "episode": 1,
    "title": "E1",
    "content_mode": "narration",
    "segments": [
        {"segment_id": "E1S01", "novel_text": "旁白", "generated_assets": {"video_clip": "videos/scene_E1S01.mp4"}}
    ],
}
_REFERENCE_SCRIPT = {
    "episode": 1,
    "title": "E1",
    "content_mode": "narration",
    "generation_mode": "reference_video",
    "video_units": [{"unit_id": "E1U1", "generated_assets": {"video_clip": "reference_videos/E1U1.mp4"}}],
}


def _harness(tmp_path: Path, *, reference: bool) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    extras = {"generation_mode": "reference_video"} if reference else None
    pm.create_project_metadata("demo", "Demo", "Anime", "narration", extras=extras)
    pm.save_script("demo", _REFERENCE_SCRIPT if reference else _STORYBOARD_SCRIPT, "episode_1.json", validate=False)
    return ToolHarness("demo", tmp_path, pm)


@pytest.mark.parametrize(
    ("reference", "resource_type", "unit_id", "media"),
    [
        (False, "videos", "E1S01", Path("videos/scene_E1S01.mp4")),
        (True, "reference_videos", "E1U1", Path("reference_videos/E1U1.mp4")),
    ],
)
async def test_selecting_an_older_version_makes_it_current(
    tmp_path: Path, reference: bool, resource_type: str, unit_id: str, media: Path
) -> None:
    ctx = _harness(tmp_path, reference=reference)
    add_typed_video_version(ctx.project_path, resource_type, unit_id, content=b"first")
    add_typed_video_version(ctx.project_path, resource_type, unit_id, content=b"second")
    versions = VersionManager(ctx.project_path)
    assert versions.get_current_version(resource_type, unit_id) == 2

    assert (await run_declared_tool("select_video_version", ctx, {"unit_id": unit_id, "version": 2})).problem is None
    thumbnail = (
        Path(f"reference_videos/thumbnails/{unit_id}.jpg") if reference else Path(f"thumbnails/scene_{unit_id}.jpg")
    )
    (ctx.project_path / thumbnail).parent.mkdir(parents=True, exist_ok=True)
    (ctx.project_path / thumbnail).write_bytes(b"old thumbnail")
    events = ProjectEventService(data_root=ctx.data_root)
    await events.start()
    try:
        async with events.stream_events(ctx.project_name, idle_timeout=10) as stream:
            assert (await anext(stream))[0] == "snapshot"
            outcome = await run_declared_tool("select_video_version", ctx, {"unit_id": unit_id, "version": 1})
            async with asyncio.timeout(5):
                event_name, payload = await anext(stream)
            assert event_name == "changes"
            change = next(change for change in payload["changes"] if change["entity_id"] == unit_id)
            assert change["entity_type"] == ("reference_unit" if reference else "segment")
            assert change["asset_fingerprints"] == {
                media.as_posix(): (ctx.project_path / media).stat().st_mtime_ns,
                thumbnail.as_posix(): 0,
            }
    finally:
        await events.shutdown()

    assert outcome.problem is None, outcome.problem
    assert outcome.value["current_version"] == 1
    assert outcome.value["file_path"] == media.as_posix()
    assert versions.get_current_version(resource_type, unit_id) == 1
    assert (ctx.project_path / media).read_bytes() == b"first"


async def test_unknown_version_reports_available_versions(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, reference=False)
    add_typed_video_version(ctx.project_path, "videos", "E1S01")

    problem = problem_of(await run_declared_tool("select_video_version", ctx, {"unit_id": "E1S01", "version": 7}))

    assert problem.code == "version_not_found"
    assert problem.params == {"available_versions": [1]}
    assert VersionManager(ctx.project_path).get_current_version("videos", "E1S01") == 1


async def test_unknown_unit_is_reported_as_missing_unit(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, reference=False)
    add_typed_video_version(ctx.project_path, "videos", "E1S01")

    problem = problem_of(await run_declared_tool("select_video_version", ctx, {"unit_id": "E1S99", "version": 1}))

    assert problem.code == "video_unit_not_found"


async def test_version_without_restore_descriptor_is_rejected(tmp_path: Path) -> None:
    ctx = _harness(tmp_path, reference=False)
    media = ctx.project_path / "videos" / "scene_E1S01.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"manual")
    VersionManager(ctx.project_path).add_version("videos", "E1S01", "manual", source_file=media, source="manual_upload")

    problem = problem_of(await run_declared_tool("select_video_version", ctx, {"unit_id": "E1S01", "version": 1}))

    assert problem.code == "request_invalid"


@pytest.mark.parametrize("version", [0, -1, "1", True])
async def test_version_must_be_a_positive_integer(tmp_path: Path, version: object) -> None:
    ctx = _harness(tmp_path, reference=False)

    problem = problem_of(await run_declared_tool("select_video_version", ctx, {"unit_id": "E1S01", "version": version}))

    assert problem.code == "invalid_request"
