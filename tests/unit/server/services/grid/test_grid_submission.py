"""宫格提交的规划与落地：选目标、在途复用 / 冲突、未切分跳过、整批准入。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.generation.generation_result import GenerationProblemCode, GenerationSelectionMode
from lib.infra.api_errors import BadRequestError
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from lib.script.script_skeleton import SkeletonRouteMismatchError
from server.services.grid.grid_submission import (
    GridChunkAction,
    commit_grid_submission,
    plan_grid_submission,
)


def _segment(index: int, *, segment_break: bool = False) -> dict[str, Any]:
    return {
        "segment_id": f"E1S{index:02d}",
        "episode": 1,
        "segment_break": segment_break,
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "image_prompt": {"scene": f"scene{index}", "composition": {"shot_type": "medium"}},
        "video_prompt": {"action": f"action{index}", "camera_motion": "static"},
        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
    }


def _script(*, groups: int = 2, per_group: int = 4) -> dict[str, Any]:
    """``groups`` 个分组，每组 ``per_group`` 个分镜（默认 4，恰好一张 grid_4）。"""
    return {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            _segment(g * per_group + i + 1, segment_break=(i == 0 and g > 0))
            for g in range(groups)
            for i in range(per_group)
        ],
    }


def _project(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "demo",
        "title": "Demo",
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "aspect_ratio": "9:16",
        "style": "anime",
        "generation_mode": "storyboard",
        "grid_storyboard": True,
        "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        **overrides,
    }


@pytest.fixture
def project_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo"
    (path / "grids").mkdir(parents=True)
    return path


async def _no_large_grid(_project: dict[str, Any]) -> bool:
    return False


async def _plan(
    project_path: Path,
    *,
    script: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    scene_ids: list[str] | None = None,
):
    project = project or _project()
    script = script or _script()
    # 产物清单的取证只读磁盘上的规范文件
    (project_path / "scripts").mkdir(exist_ok=True)
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text(json.dumps(script), encoding="utf-8")
    return await plan_grid_submission(
        project=project,
        project_path=project_path,
        script=script,
        script_file="episode_1.json",
        episode=1,
        scene_ids=scene_ids,
        large_grid_gate=_no_large_grid,
    )


def _record(project_path: Path, scene_ids: list[str], *, status: str, split: bool = False) -> GridGeneration:
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=scene_ids,
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    grid.status = status
    if status == "completed":
        grid.grid_image_path = f"grids/{grid.id}.png"
        Image.new("RGB", (8, 8)).save(project_path / "grids" / f"{grid.id}.png")
        grid.split_at = "2026-01-01T00:00:00+00:00" if split else None
    GridManager(project_path).save(grid)
    return grid


GROUP_1 = ["E1S01", "E1S02", "E1S03", "E1S04"]
GROUP_2 = ["E1S05", "E1S06", "E1S07", "E1S08"]


async def test_missing_only_generates_every_group_without_storyboards(project_path: Path) -> None:
    plan = await _plan(project_path)

    assert plan.selection is GenerationSelectionMode.MISSING_ONLY
    assert not plan.refused
    assert [(c.scene_ids, c.report_ids, c.action) for c in plan.chunks] == [
        (tuple(GROUP_1), tuple(GROUP_1), GridChunkAction.GENERATE),
        (tuple(GROUP_2), tuple(GROUP_2), GridChunkAction.GENERATE),
    ]

    tasks = commit_grid_submission(plan, project_path)

    records = GridManager(project_path).list_all()
    assert sorted(g.id for g in records) == sorted(t.grid.id for t in tasks)
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_1, GROUP_2]
    # 生成任务只产出联合图：payload 不再携带随任务落格的范围
    assert all("report_scene_ids" not in t.payload for t in tasks)
    assert all(t.payload["prompt"] == g.prompt for t, g in zip(tasks, records, strict=True))


async def test_identical_in_flight_grid_is_reused_instead_of_created_again(project_path: Path) -> None:
    in_flight = _record(project_path, GROUP_1, status="pending")

    plan = await _plan(project_path, scene_ids=GROUP_1)
    tasks = commit_grid_submission(plan, project_path)

    assert [c.action for c in plan.chunks] == [GridChunkAction.IN_FLIGHT]
    assert [(t.grid.id, t.reused) for t in tasks] == [(in_flight.id, True)]
    assert [g.id for g in GridManager(project_path).list_all()] == [in_flight.id]


async def test_partial_overlap_with_an_in_flight_grid_refuses_the_whole_batch(project_path: Path) -> None:
    other = _record(project_path, ["E1S03", "E1S04", "E1S05"], status="generating")

    plan = await _plan(project_path)

    assert plan.refused
    assert [b.scene_id for b in plan.blocked] == GROUP_1 + GROUP_2
    assert {b.problem.code for b in plan.blocked} == {GenerationProblemCode.ACTIVE_TASK_CONFLICT}
    assert plan.blocked[0].problem.params == {"grid_ids": [other.id]}
    assert plan.withheld == ()
    with pytest.raises(ValueError, match="refused"):
        commit_grid_submission(plan, project_path)
    assert [g.id for g in GridManager(project_path).list_all()] == [other.id]


async def test_a_blocked_group_withholds_the_healthy_one(project_path: Path) -> None:
    script = _script()
    script["segments"][5]["image_prompt"] = None

    plan = await _plan(project_path, script=script)

    assert plan.refused
    assert [b.scene_id for b in plan.blocked] == GROUP_2
    assert plan.blocked[0].problem.params == {"pending_ids": ["E1S06"]}
    assert [b.scene_id for b in plan.withheld] == GROUP_1
    withheld = plan.withheld[0].problem
    assert withheld.code == GenerationProblemCode.BATCH_ADMISSION_WITHHELD
    assert withheld.params == {"blocked_unit_ids": GROUP_2}
    assert [item["segment_id"] for item in plan.admission_items] == GROUP_1 + GROUP_2


async def test_an_in_flight_group_is_not_reported_as_withheld(project_path: Path) -> None:
    _record(project_path, GROUP_1, status="generating")
    script = _script()
    script["segments"][5]["image_prompt"] = None

    plan = await _plan(project_path, script=script)

    assert plan.refused
    assert [c.action for c in plan.chunks] == [GridChunkAction.IN_FLIGHT, GridChunkAction.BLOCKED]
    assert plan.withheld == ()


async def test_unknown_explicit_scene_refuses_the_batch(project_path: Path) -> None:
    plan = await _plan(project_path, scene_ids=["E1S01", "E9S99"])

    assert [(b.scene_id, b.problem.code) for b in plan.blocked] == [("E9S99", GenerationProblemCode.UNIT_NOT_FOUND)]
    assert [b.scene_id for b in plan.withheld] == ["E1S01"]


async def test_missing_only_waits_on_an_unsplit_composite_instead_of_paying_again(project_path: Path) -> None:
    unsplit = _record(project_path, GROUP_1, status="completed")

    plan = await _plan(project_path)
    tasks = commit_grid_submission(plan, project_path)

    assert [(c.action, c.grid.id if c.grid else None) for c in plan.chunks] == [
        (GridChunkAction.UNSPLIT, unsplit.id),
        (GridChunkAction.GENERATE, None),
    ]
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_2]
    assert GridManager(project_path).get(unsplit.id) is not None


async def test_explicit_request_regenerates_and_supersedes_an_unsplit_composite(project_path: Path) -> None:
    unsplit = _record(project_path, GROUP_1, status="completed")

    plan = await _plan(project_path, scene_ids=["E1S02"])
    tasks = commit_grid_submission(plan, project_path)

    assert plan.selection is GenerationSelectionMode.EXPLICIT
    assert [(c.report_ids, c.action) for c in plan.chunks] == [(("E1S02",), GridChunkAction.GENERATE)]
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_1]
    assert GridManager(project_path).get(unsplit.id) is None


async def test_a_split_composite_does_not_stop_missing_storyboards_from_regenerating(project_path: Path) -> None:
    """已切分过的联合图不是「未切分」：分镜图仍缺时照常重新出图。"""
    _record(project_path, GROUP_1, status="completed", split=True)

    plan = await _plan(project_path, script=_script(groups=1))

    assert [c.action for c in plan.chunks] == [GridChunkAction.GENERATE]


async def test_ad_projects_are_refused_before_planning(project_path: Path) -> None:
    with pytest.raises(BadRequestError) as excinfo:
        await _plan(project_path, project=_project(content_mode="ad"))
    assert excinfo.value.key == "ad_grid_not_supported"


async def test_a_script_of_the_other_route_is_refused(project_path: Path) -> None:
    with pytest.raises(SkeletonRouteMismatchError):
        await _plan(project_path, script={"episode": 1, "content_mode": "narration", "video_units": []})
