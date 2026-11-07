"""Grid-submission planning/application: target selection, in-flight reuse/conflicts, unsplit skipping and whole-batch admission."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.artifacts.artifact_activation import register_current_resource_artifact
from lib.generation.generation_result import GenerationProblemCode, GenerationSelectionMode
from lib.infra.api_errors import BadRequestError
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.models import GridGeneration
from server.services.grid.grid_submission import (
    ActiveGridTaskProbe,
    GridChunkAction,
    GridSubmissionSection,
    commit_grid_submission,
    grid_submission_section,
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
    """``groups`` groups of ``per_group`` storyboards each (default four, exactly one grid_4)."""
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


@pytest.fixture
async def section(project_path: Path) -> AsyncIterator[GridSubmissionSection]:
    """Planning and application share one critical section."""
    async with grid_submission_section(str(project_path)) as held:
        yield held


async def _no_large_grid(_project: dict[str, Any]) -> bool:
    return False


def _write_project(project_path: Path, project: dict[str, Any], script: dict[str, Any]) -> None:
    # Manifest evidence reads only canonical on-disk files.
    (project_path / "scripts").mkdir(exist_ok=True)
    (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text(json.dumps(script), encoding="utf-8")


async def _plan(
    project_path: Path,
    section: GridSubmissionSection,
    *,
    script: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    scene_ids: list[str] | None = None,
    orphaned: frozenset[str] = frozenset(),
    active_grid_tasks: ActiveGridTaskProbe | None = None,
):
    """``orphaned`` lists pending/generating grids without active tasks; all other records have queued tasks."""
    project = project or _project()
    script = script or _script()
    _write_project(project_path, project, script)

    async def all_but_orphaned(grid_ids: list[str]) -> list[str]:
        return [grid_id for grid_id in grid_ids if grid_id not in orphaned]

    return await plan_grid_submission(
        project=project,
        project_path=project_path,
        script=script,
        script_file="episode_1.json",
        episode=1,
        scene_ids=scene_ids,
        section=section,
        active_grid_tasks=active_grid_tasks or all_but_orphaned,
        large_grid_gate=_no_large_grid,
    )


def _complete(project_path: Path, grid: GridGeneration, *, split: bool = False, registered: bool = True) -> None:
    """Persist like successful executor output: write combined image, mark completed and register current artifact."""
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    Image.new("RGB", (8, 8)).save(project_path / "grids" / f"{grid.id}.png")
    grid.split_at = "2026-01-01T00:00:00+00:00" if split else None
    GridManager(project_path).save(grid)
    if registered:
        _write_project(project_path, _project(), _script())
        assert register_current_resource_artifact(project_path, resource_type="grids", resource_id=grid.id)


def _record(
    project_path: Path,
    scene_ids: list[str],
    *,
    status: str,
    split: bool = False,
    registered: bool = True,
) -> GridGeneration:
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
    if status == "completed":
        _complete(project_path, grid, split=split, registered=registered)
    else:
        grid.status = status
        GridManager(project_path).save(grid)
    return grid


GROUP_1 = ["E1S01", "E1S02", "E1S03", "E1S04"]
GROUP_2 = ["E1S05", "E1S06", "E1S07", "E1S08"]


async def test_missing_only_generates_every_group_without_storyboards(
    project_path: Path, section: GridSubmissionSection
) -> None:
    plan = await _plan(project_path, section)

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
    # Generation tasks produce combined images only; payloads no longer carry automatic per-task splitting scope.
    assert all("report_scene_ids" not in t.payload for t in tasks)
    assert all(t.payload["prompt"] == g.prompt for t, g in zip(tasks, records, strict=True))


async def test_regenerating_one_chunk_removes_a_record_spanning_it_but_keeps_the_untouched_chunk(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """Split a 16-cell group at non-4K into 9 + 7: redrawing the first invalidates an old 4×4 spanning both new groups, without affecting the second group's own old record."""
    ids = [f"E1S{i:02d}" for i in range(1, 17)]
    spanning = _record(project_path, ids, status="completed", split=True, registered=False)
    untouched = _record(project_path, ids[9:], status="completed", split=True, registered=False)

    plan = await _plan(project_path, section, script=_script(groups=1, per_group=16), scene_ids=["E1S01"])
    commit_grid_submission(plan, project_path)

    remaining = {g.id for g in GridManager(project_path).list_all()}
    assert spanning.id not in remaining
    assert untouched.id in remaining


async def test_identical_in_flight_grid_is_reused_instead_of_created_again(
    project_path: Path, section: GridSubmissionSection
) -> None:
    in_flight = _record(project_path, GROUP_1, status="pending")

    plan = await _plan(project_path, section, scene_ids=GROUP_1)
    tasks = commit_grid_submission(plan, project_path)

    assert [c.action for c in plan.chunks] == [GridChunkAction.IN_FLIGHT]
    assert [(t.grid.id, t.reused) for t in tasks] == [(in_flight.id, True)]
    assert [g.id for g in GridManager(project_path).list_all()] == [in_flight.id]


async def test_partial_overlap_with_an_in_flight_grid_refuses_the_whole_batch(
    project_path: Path, section: GridSubmissionSection
) -> None:
    other = _record(project_path, ["E1S03", "E1S04", "E1S05"], status="generating")

    plan = await _plan(project_path, section)

    assert plan.refused
    assert [b.scene_id for b in plan.blocked] == GROUP_1 + GROUP_2
    assert {b.problem.code for b in plan.blocked} == {GenerationProblemCode.ACTIVE_TASK_CONFLICT}
    assert plan.blocked[0].problem.params == {"grid_ids": [other.id]}
    assert plan.withheld == ()
    with pytest.raises(ValueError, match="refused"):
        commit_grid_submission(plan, project_path)
    assert [g.id for g in GridManager(project_path).list_all()] == [other.id]


async def test_a_record_left_generating_without_an_active_task_does_not_block(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """Cancelled/restart-lost tasks may leave generating records, but they are no longer in flight; changed grouping can generate normally."""
    orphan = _record(project_path, ["E1S03", "E1S04", "E1S05"], status="generating")

    plan = await _plan(project_path, section, orphaned=frozenset({orphan.id}))

    assert not plan.refused
    assert [c.action for c in plan.chunks] == [GridChunkAction.GENERATE, GridChunkAction.GENERATE]


async def test_a_grid_finishing_while_its_task_is_probed_is_kept_not_paid_for_again(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """A record is generating when read but its task finishes before queue inspection: retain its newly ready combined image for splitting rather than deleting/redrawing as orphaned."""
    generating = _record(project_path, GROUP_1, status="generating")

    async def worker_finishes_first(_grid_ids: list[str]) -> list[str]:
        # Worker writes generated images in another thread.
        await asyncio.to_thread(_complete, project_path, generating)
        return []

    plan = await _plan(project_path, section, active_grid_tasks=worker_finishes_first)
    tasks = commit_grid_submission(plan, project_path)

    assert [(c.action, c.grid.id if c.grid else None) for c in plan.chunks] == [
        (GridChunkAction.UNSPLIT, generating.id),
        (GridChunkAction.GENERATE, None),
    ]
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_2]
    assert GridManager(project_path).get(generating.id) is not None


async def test_an_abandoned_record_of_the_same_chunk_is_replaced_not_left_beside(
    project_path: Path, section: GridSubmissionSection
) -> None:
    orphan = _record(project_path, GROUP_1, status="pending")

    plan = await _plan(project_path, section, script=_script(groups=1), orphaned=frozenset({orphan.id}))
    tasks = commit_grid_submission(plan, project_path)

    assert [(t.grid.id != orphan.id, t.reused) for t in tasks] == [(True, False)]
    assert [g.id for g in GridManager(project_path).list_all()] == [tasks[0].grid.id]


async def test_a_blocked_group_withholds_the_healthy_one(project_path: Path, section: GridSubmissionSection) -> None:
    script = _script()
    script["segments"][5]["image_prompt"] = None

    plan = await _plan(project_path, section, script=script)

    assert plan.refused
    assert [b.scene_id for b in plan.blocked] == GROUP_2
    assert plan.blocked[0].problem.params == {"pending_ids": ["E1S06"]}
    assert [b.scene_id for b in plan.withheld] == GROUP_1
    withheld = plan.withheld[0].problem
    assert withheld.code == GenerationProblemCode.BATCH_ADMISSION_WITHHELD
    assert withheld.params == {"blocked_unit_ids": GROUP_2}
    assert [item["segment_id"] for item in plan.admission_items] == GROUP_1 + GROUP_2


async def test_an_in_flight_group_is_not_reported_as_withheld(
    project_path: Path, section: GridSubmissionSection
) -> None:
    _record(project_path, GROUP_1, status="generating")
    script = _script()
    script["segments"][5]["image_prompt"] = None

    plan = await _plan(project_path, section, script=script)

    assert plan.refused
    assert [c.action for c in plan.chunks] == [GridChunkAction.IN_FLIGHT, GridChunkAction.BLOCKED]
    assert plan.withheld == ()


async def test_unknown_explicit_scene_refuses_the_batch(project_path: Path, section: GridSubmissionSection) -> None:
    plan = await _plan(project_path, section, scene_ids=["E1S01", "E9S99"])

    assert [(b.scene_id, b.problem.code) for b in plan.blocked] == [("E9S99", GenerationProblemCode.UNIT_NOT_FOUND)]
    assert [b.scene_id for b in plan.withheld] == ["E1S01"]


async def test_missing_only_waits_on_an_unsplit_composite_instead_of_paying_again(
    project_path: Path, section: GridSubmissionSection
) -> None:
    unsplit = _record(project_path, GROUP_1, status="completed")

    plan = await _plan(project_path, section)
    tasks = commit_grid_submission(plan, project_path)

    assert [(c.action, c.grid.id if c.grid else None) for c in plan.chunks] == [
        (GridChunkAction.UNSPLIT, unsplit.id),
        (GridChunkAction.GENERATE, None),
    ]
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_2]
    assert GridManager(project_path).get(unsplit.id) is not None


async def test_missing_only_regenerates_a_composite_the_split_would_refuse(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """On-disk but unregistered combined images cannot be split; missing-only mode regenerates normally."""
    _record(project_path, GROUP_1, status="completed", registered=False)

    plan = await _plan(project_path, section, script=_script(groups=1))

    assert [c.action for c in plan.chunks] == [GridChunkAction.GENERATE]
    assert plan.unsplit == ()


async def test_explicit_request_regenerates_and_supersedes_an_unsplit_composite(
    project_path: Path, section: GridSubmissionSection
) -> None:
    unsplit = _record(project_path, GROUP_1, status="completed")

    plan = await _plan(project_path, section, scene_ids=["E1S02"])
    tasks = commit_grid_submission(plan, project_path)

    assert plan.selection is GenerationSelectionMode.EXPLICIT
    assert [(c.report_ids, c.action) for c in plan.chunks] == [(("E1S02",), GridChunkAction.GENERATE)]
    assert [t.payload["scene_ids"] for t in tasks] == [GROUP_1]
    assert GridManager(project_path).get(unsplit.id) is None


async def test_a_split_composite_does_not_stop_missing_storyboards_from_regenerating(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """Previously split combined images are not unsplit; regenerate normally if storyboard images remain missing."""
    _record(project_path, GROUP_1, status="completed", split=True)

    plan = await _plan(project_path, section, script=_script(groups=1))

    assert [c.action for c in plan.chunks] == [GridChunkAction.GENERATE]


async def test_ad_projects_are_refused_before_planning(project_path: Path, section: GridSubmissionSection) -> None:
    with pytest.raises(BadRequestError) as excinfo:
        await _plan(project_path, section, project=_project(content_mode="ad"))
    assert excinfo.value.key == "ad_grid_not_supported"


async def test_a_script_of_the_other_route_is_refused(project_path: Path, section: GridSubmissionSection) -> None:
    with pytest.raises(BadRequestError) as excinfo:
        await _plan(project_path, section, script={"episode": 1, "content_mode": "narration", "video_units": []})
    assert excinfo.value.key == "grid_script_route_mismatch"


async def test_a_plan_cannot_be_committed_once_its_section_has_ended(
    project_path: Path, section: GridSubmissionSection
) -> None:
    """After the critical section ends, other submissions may change records/queue; planned in-flight and abandonment decisions are no longer reliable."""
    plan = await _plan(project_path, section)
    section.end()

    with pytest.raises(ValueError, match="inside the section"):
        commit_grid_submission(plan, project_path)
    assert GridManager(project_path).list_all() == []


async def test_planning_outside_a_held_section_is_refused(project_path: Path) -> None:
    with pytest.raises(ValueError, match="inside its submission section"):
        await _plan(project_path, grid_submission_section(str(project_path)))


async def test_submissions_of_one_project_take_turns(tmp_path: Path) -> None:
    project = str(tmp_path / "turns")
    entered: list[str] = []

    async def submit(name: str, inside: asyncio.Event, release: asyncio.Event) -> None:
        async with grid_submission_section(project):
            entered.append(name)
            inside.set()
            await release.wait()

    first_inside, first_release = asyncio.Event(), asyncio.Event()
    second_inside, second_release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(submit("first", first_inside, first_release))
    await asyncio.wait_for(first_inside.wait(), timeout=5)
    second = asyncio.create_task(submit("second", second_inside, second_release))
    await asyncio.sleep(0)
    assert entered == ["first"]

    # This critical section does not affect another project.
    async with grid_submission_section(str(tmp_path / "other")):
        pass

    first_release.set()
    await asyncio.wait_for(second_inside.wait(), timeout=5)
    assert entered == ["first", "second"]
    second_release.set()
    await asyncio.gather(first, second)
