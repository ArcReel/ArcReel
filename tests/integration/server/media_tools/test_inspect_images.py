"""inspect_images：经声明入口调用，断言结果信封里的结构化结果与图片内容块。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import ProjectManager
from server.agent_toolset.envelope import ToolEnvelope, encode_outcome
from server.agent_toolset.image_review import INSPECT_IMAGES
from tests.integration.server.agent_tool_support import ToolHarness, problem_of, run_declared_tool


def _harness(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.upsert_assets(
        "demo",
        "characters",
        {"阿岚": {"description": "少女", "derivatives": {"战斗装": {"description": "重甲"}}}},
    )
    pm.upsert_assets("demo", "scenes", {"城门": {"description": "夜色中的城门"}})
    for episode, segment_ids in ((1, ["E1S01", "E1S02"]), (2, ["E2S01"])):
        script = {
            "episode": episode,
            "title": f"E{episode}",
            "content_mode": "narration",
            "segments": [{"segment_id": segment_id, "novel_text": "旁白"} for segment_id in segment_ids],
        }
        pm.save_script("demo", script, f"episode_{episode}.json", validate=False)
    return ToolHarness("demo", tmp_path, pm)


def _install(
    ctx: ToolHarness, resource_type: str, resource_id: str, color: str, *, size: tuple[int, int] = (160, 90)
) -> int:
    """把一张纯色 PNG 登记为资源的新 current 版本；返回版本号。"""
    staged = ctx.project_path / ".staging" / f"{resource_type}-{color}.png"
    staged.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(staged)
    return VersionManager(ctx.project_path).add_version(
        resource_type, resource_id, f"{color} 提示词", source_file=staged
    )


async def _inspect(ctx: ToolHarness, arguments: dict[str, Any]) -> tuple[Any, ToolEnvelope]:
    outcome = await run_declared_tool(INSPECT_IMAGES, ctx, arguments)
    return outcome, encode_outcome(INSPECT_IMAGES, outcome)


def _picture(jpeg: bytes) -> tuple[str, tuple[int, int]]:
    image = Image.open(io.BytesIO(jpeg))
    assert image.format == "JPEG"
    pixel = image.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    assert isinstance(pixel, tuple)
    red, green, blue = pixel
    color = max((("red", red), ("green", green), ("blue", blue)), key=lambda pair: pair[1])[0]
    return color, image.size


async def test_current_asset_sheets_come_back_as_image_blocks_in_request_order(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "characters", "阿岚", "red")
    _install(ctx, "characters", "阿岚", "blue")

    outcome, envelope = await _inspect(ctx, {"resource_type": "character", "ids": ["阿岚"]})

    assert outcome.problem is None, outcome.problem
    result = envelope.structured["inspect_images"]
    assert result["resource_type"] == "character"
    assert result["images"] == [
        {
            "id": "阿岚",
            "version": 2,
            "available_versions": [1, 2],
            "status": "ok",
            "image": 1,
            "prompt": "blue 提示词",
        }
    ]
    assert [image.mime_type for image in envelope.images] == ["image/jpeg"]
    assert _picture(envelope.images[0].data) == ("blue", (160, 90))


async def test_naming_a_version_shows_that_storyboard_from_any_episode(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "storyboards", "E2S01", "green")
    _install(ctx, "storyboards", "E2S01", "red")

    _outcome, envelope = await _inspect(ctx, {"resource_type": "storyboard", "ids": ["E2S01"], "version": 1})

    (entry,) = envelope.structured["inspect_images"]["images"]
    assert (entry["id"], entry["version"], entry["available_versions"]) == ("E2S01", 1, [1, 2])
    assert entry["prompt"] == "green 提示词"
    assert _picture(envelope.images[0].data)[0] == "green"


async def test_derivative_sheets_are_addressed_by_owner_and_derivative_name(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "character_derivatives", "阿岚/战斗装", "blue")

    _outcome, envelope = await _inspect(ctx, {"resource_type": "character_derivative", "ids": ["阿岚/战斗装"]})

    (entry,) = envelope.structured["inspect_images"]["images"]
    assert (entry["id"], entry["version"], entry["status"]) == ("阿岚/战斗装", 1, "ok")
    assert _picture(envelope.images[0].data)[0] == "blue"


async def test_items_without_a_viewable_image_report_their_status_without_an_image(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "storyboards", "E1S01", "red")
    _install(ctx, "storyboards", "E1S02", "blue")
    broken = VersionManager.resolve_snapshot_path(
        ctx.project_path,
        "storyboards",
        VersionManager(ctx.project_path).get_versions("storyboards", "E1S02")["versions"][0]["file"],
    )
    broken.write_bytes(b"not an image")

    outcome, envelope = await _inspect(ctx, {"resource_type": "storyboard", "ids": ["E2S01", "E1S02", "E1S01"]})

    assert outcome.problem is None, outcome.problem
    entries = envelope.structured["inspect_images"]["images"]
    assert [(entry["id"], entry["status"], entry.get("image")) for entry in entries] == [
        ("E2S01", "image_missing", None),
        ("E1S02", "image_unreadable", None),
        ("E1S01", "ok", 1),
    ]
    assert entries[0]["version"] is None
    assert entries[0]["available_versions"] == []
    assert entries[1]["detail"]
    assert len(envelope.images) == 1
    assert _picture(envelope.images[0].data)[0] == "red"
    assert "E2S01" in envelope.texts[0]
    assert "E1S02" in envelope.texts[0]


async def test_unknown_ids_are_rejected_with_the_missing_ids_listed(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)

    asset, _envelope = await _inspect(ctx, {"resource_type": "scene", "ids": ["城门", "渡口"]})
    storyboard, _envelope = await _inspect(ctx, {"resource_type": "storyboard", "ids": ["E1S01", "E9S01"]})

    assert (problem_of(asset).code, problem_of(asset).params) == (
        "resource_not_found",
        {"resource_type": "scene", "ids": ["渡口"]},
    )
    assert (problem_of(storyboard).code, problem_of(storyboard).params) == (
        "resource_not_found",
        {"resource_type": "storyboard", "ids": ["E9S01"]},
    )


async def test_derivative_ids_not_shaped_owner_slash_derivative_are_rejected_as_not_found(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)

    outcome, _envelope = await _inspect(
        ctx, {"resource_type": "character_derivative", "ids": ["阿岚/战斗装", "战斗装", "阿岚/战斗装/破损"]}
    )

    assert (problem_of(outcome).code, problem_of(outcome).params) == (
        "resource_not_found",
        {"resource_type": "character_derivative", "ids": ["战斗装", "阿岚/战斗装/破损"]},
    )


async def test_a_version_the_item_does_not_have_is_rejected_with_its_versions(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "scenes", "城门", "red")

    outcome, _envelope = await _inspect(ctx, {"resource_type": "scene", "ids": ["城门"], "version": 3})

    assert (problem_of(outcome).code, problem_of(outcome).params) == (
        "version_not_found",
        {"id": "城门", "available_versions": [1]},
    )


async def test_more_ids_than_one_call_may_carry_are_rejected_for_batching(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)

    outcome, _envelope = await _inspect(
        ctx, {"resource_type": "storyboard", "ids": [f"E1S{index:02d}" for index in range(1, 22)]}
    )

    assert (problem_of(outcome).code, problem_of(outcome).params) == (
        "image_limit_exceeded",
        {"max_images_per_call": 20},
    )


async def test_large_images_are_downscaled_to_the_long_edge_limit(tmp_path: Path) -> None:
    ctx = _harness(tmp_path)
    _install(ctx, "scenes", "城门", "green", size=(4000, 1000))

    _outcome, envelope = await _inspect(ctx, {"resource_type": "scene", "ids": ["城门"]})

    assert _picture(envelope.images[0].data) == ("green", (2000, 500))


@pytest.mark.parametrize("resource_type", ["character", "storyboard"])
async def test_current_images_without_history_remain_viewable_and_read_only(tmp_path: Path, resource_type: str) -> None:
    ctx = _harness(tmp_path)
    if resource_type == "character":
        item_id, pointer = "阿岚", "characters/阿岚.png"
        ctx.pm.update_project_character_sheet("demo", item_id, pointer)
    else:
        item_id, pointer = "E1S01", "storyboards/scene_E1S01.png"
        script = ctx.pm.load_script_readonly("demo", "episode_1.json")
        script["segments"][0]["generated_assets"] = {"storyboard_image": pointer}
        ctx.pm.save_script("demo", script, "episode_1.json", validate=False)
    image_path = ctx.project_path / pointer
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), "red").save(image_path)
    before = {
        path.relative_to(ctx.project_path): path.read_bytes() for path in ctx.project_path.rglob("*") if path.is_file()
    }

    outcome, envelope = await _inspect(ctx, {"resource_type": resource_type, "ids": [item_id]})
    explicit, _ = await _inspect(ctx, {"resource_type": resource_type, "ids": [item_id], "version": 1})

    assert outcome.problem is None
    assert envelope.structured["inspect_images"]["images"] == [
        {"id": item_id, "version": None, "available_versions": [], "status": "ok", "image": 1}
    ]
    assert _picture(envelope.images[0].data) == ("red", (160, 90))
    assert problem_of(explicit).code == "version_not_found"
    assert {
        path.relative_to(ctx.project_path): path.read_bytes() for path in ctx.project_path.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("pointer", [None, "../outside.png"])
async def test_untracked_images_require_a_registered_path_inside_the_project(
    tmp_path: Path, pointer: str | None
) -> None:
    ctx = _harness(tmp_path)
    for path in (ctx.project_path / "characters/阿岚.png", ctx.project_path.parent / "outside.png"):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 90), "red").save(path)
    project = ctx.pm.load_project("demo")
    project["characters"]["阿岚"]["character_sheet"] = pointer
    ctx.pm.save_project("demo", project)

    outcome, envelope = await _inspect(ctx, {"resource_type": "character", "ids": ["阿岚"]})

    assert outcome.problem is None
    assert envelope.structured["inspect_images"]["images"][0]["status"] == "image_missing"
    assert envelope.images == ()
