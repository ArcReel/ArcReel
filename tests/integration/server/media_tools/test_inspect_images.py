"""inspect_images：经声明入口调用，断言结果信封里的结构化结果与图片内容块。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

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
