"""generate_grid / split_grids handler behavior through declaration entries, asserting ``ToolOutcome``."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.artifact_manifest import ArtifactKey
from lib.generation.generation_queue_client import BatchTaskResult, is_interrupted_wait_error
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.media_tools.grid import GridPlanPreview
from server.tool_runtime import CallerContext
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    read_generation_result,
    run_declared_tool,
    use_fake_caps,
)


def _fake_grid_waiter(enqueue, wait=None):
    """Match ``batch_enqueue_and_wait`` ordering: enqueue each, call ``on_enqueued`` after submission completes, then await each."""

    async def _waiter(*, project_name, specs, on_enqueued=None, **_kwargs):
        successes: list[BatchTaskResult] = []
        failures: list[BatchTaskResult] = []
        queued: list[tuple[Any, dict[str, Any]]] = []
        for spec in specs:
            try:
                task = await enqueue(
                    project_name=project_name,
                    task_type=spec.task_type,
                    media_type=spec.media_type,
                    resource_id=spec.resource_id,
                    payload=spec.payload,
                    script_file=spec.script_file,
                    source=spec.source,
                )
            except Exception as exc:
                failures.append(
                    BatchTaskResult(resource_id=spec.resource_id, task_id="", status="failed", error=str(exc))
                )
                continue
            queued.append((spec, task))
        if on_enqueued is not None:
            on_enqueued()
        for spec, queued_task in queued:
            try:
                task = await wait(queued_task["task_id"])
            except Exception as exc:
                failures.append(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id=queued_task["task_id"],
                        status="interrupted" if is_interrupted_wait_error(exc) else "failed",
                        error=str(exc),
                    )
                )
                continue
            result = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=queued_task["task_id"],
                status=str(task.get("status")),
                result=task.get("result") or {},
                error=task.get("error_message"),
                task=task,
            )
            (successes if result.status == "succeeded" else failures).append(result)
        return successes, failures

    return _waiter


async def test_generate_grid_list_only(fake_ctx: ToolHarness) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    # Need enough segments to form a group with valid layout
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})
    assert isinstance(out.value, GridPlanPreview)
    assert "分组" in out.value.plan


@pytest.mark.parametrize(
    ("image_resolution", "expected", "forbidden"),
    [("4K", "grid_16 (4×4)", "grid_9"), ("2K", "grid_9 (3×3)", "grid_16")],
)
async def test_generate_grid_list_only_respects_4k_gate(
    fake_ctx: ToolHarness,
    image_resolution: str,
    expected: str,
    forbidden: str,
) -> None:
    # Non-4K Agent previews omit 4×4 / 5×5; derive resolution from project T2I slots.
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]
    use_fake_caps(fake_ctx, image_resolution=image_resolution)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})
    assert isinstance(out.value, GridPlanPreview)
    text = out.value.plan
    assert expected in text
    assert forbidden not in text


async def test_generate_grid_list_only_shows_split_for_oversized_group(fake_ctx: ToolHarness) -> None:
    # Oversized groups preview chunked image counts/tiers, sharing enqueue planning.
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})
    assert isinstance(out.value, GridPlanPreview)
    text = out.value.plan
    assert "2 张宫格: grid_9 (3×3) + grid_4 (2×2)" in text


async def test_generate_grid_falls_back_on_null_aspect_ratio(fake_ctx: ToolHarness) -> None:
    # Explicit project.json aspect_ratio=null requires SDK enqueue fallback to defaults,
    # preventing None in grid plans, task payloads, and frozen record ratios.
    from lib.script.grid.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["aspect_ratio"] = None
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        payloads.append(payload)
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)
    assert read_generation_result(out).ok

    assert [p["video_aspect_ratio"] for p in payloads] == ["9:16"]
    assert [g.video_aspect_ratio for g in GridManager(fake_ctx.project_path).list_all()] == ["9:16"]


async def test_generate_grid_explicit_failure_preserves_the_old_artifact_path(fake_ctx: ToolHarness) -> None:
    """Failed explicit regeneration must retain registered old-image paths in reports, distinguishing
    failed replacements with usable old outputs from absent reusable artifacts for correct next-step guidance."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {
            "segment_id": f"E1S0{i}",
            "image_prompt": "p",
            "segment_break": False,
            "generated_assets": {"storyboard_image": f"storyboards/E1S0{i}.png"},
        }
        for i in range(1, 5)
    ]

    async def failing_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        raise RuntimeError("queue is down")

    batch_waiter = _fake_grid_waiter(failing_enqueue)

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json", "scene_ids": ["E1S01"]}, batch_waiter=batch_waiter
    )

    result = read_generation_result(out)
    assert not result.ok
    assert result.failed == ["E1S01"]
    item = result.items[0]
    assert item.artifact_path == "storyboards/E1S01.png"


async def test_remote_grid_failure_reports_the_prior_storyboard(fake_ctx: ToolHarness) -> None:
    """Remote postsubmission failures still include presubmission storyboard-image information in terminal reads."""
    _enable_grid(fake_ctx)
    for segment in fake_ctx.pm.script_payload["segments"]:
        segment["generated_assets"] = {"storyboard_image": f"storyboards/{segment['segment_id']}.png"}
    fake_ctx.caller = CallerContext(user_id=fake_ctx.caller.user_id, source="mcp")

    submitted = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "scene_ids": ["E1S01"]})
    assert submitted.value is not None
    batch_id = submitted.value.batch_id
    task = await fake_ctx.queue.claim_next_task("image")
    assert task is not None
    await fake_ctx.queue.mark_task_failed(task["task_id"], "provider failed")

    settled = await run_declared_tool("get_generation_batch", fake_ctx, {"batch_id": batch_id})
    assert settled.value is not None
    item = next(item for item in settled.value.generation_result.items if item.unit_id == "E1S01")
    assert item.artifact_key == ArtifactKey.episode_storyboard(1, "E1S01").encode()
    assert item.artifact_path == "storyboards/E1S01.png"


async def test_generate_grid_wait_timeout_is_reported_as_interrupted_not_failed(fake_ctx: ToolHarness) -> None:
    """Shared batch-wait interruption is not terminal task failure because tasks may still run;
    misreporting would encourage retries and duplicate paid submissions."""
    from lib.generation.generation_queue_client import TaskWaitTimeoutError

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        raise TaskWaitTimeoutError("wait timed out before a terminal state")

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)

    result = read_generation_result(out)
    assert result.succeeded == []
    assert result.failed == ["E1S01", "E1S02", "E1S03", "E1S04"]
    item = result.items[0]
    assert item.task_state.value == "interrupted"
    assert item.problem is not None
    assert item.problem.code == "generation_task_interrupted"
    assert item.problem.action == "wait_for_task"


_CLAMP_WARNING = {"key": "ref_too_many_images", "params": {"count": 9, "model": "gpt-image-2", "max_count": 8}}


@pytest.mark.parametrize(
    ("task", "succeeded", "failed"),
    [
        pytest.param(
            {
                "status": "succeeded",
                "provider_id": "openai",
                "provider_job_id": "job-1",
                "result": {"file_path": "grids/g1.png", "warnings": [_CLAMP_WARNING]},
            },
            ["E1S01", "E1S02"],
            [],
            id="联合图出图成功",
        ),
        pytest.param(
            {
                "status": "failed",
                "error_message": "provider rejected the request",
                "result": {"warnings": [_CLAMP_WARNING]},
            },
            [],
            ["E1S01", "E1S02"],
            id="宫格任务终态失败",
        ),
    ],
)
async def test_generate_grid_reports_the_grid_warnings_on_every_cell(
    fake_ctx: ToolHarness, task: dict[str, Any], succeeded: list[str], failed: list[str]
) -> None:
    """Reference-trimming warnings belong to whole combined grids; every reported storyboard carries them on success/failure so Agents know unsent images."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 3)
    ]

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return task

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)

    result = read_generation_result(out)
    assert (result.succeeded, result.failed) == (succeeded, failed)
    assert [[w.model_dump() for w in item.warnings] for item in result.items] == [[_CLAMP_WARNING], [_CLAMP_WARNING]]


async def test_generate_grid_blocks_every_scene_of_a_chunk_with_a_reference_gap(fake_ctx: ToolHarness) -> None:
    """One combined image covers each chunk; name and block every member whose references have gaps."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    fake_ctx.pm.script_payload["segments"][2]["scenes"] = ["未登记的场景"]

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("引用有缺口时不该走到入队")

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_waiter
    )

    result = read_generation_result(out)
    assert result.blocked == ["E1S01", "E1S02", "E1S03", "E1S04"]
    problem = next(item for item in result.items if item.unit_id == "E1S01").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("reference_asset_unregistered", "generate_dependency")
    assert problem.params["missing_text"] == "未登记的场景"


async def test_generate_grid_blocks_the_whole_group_when_one_scene_state_is_unreadable(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Groups share combined images; unreadable state in one cell must not silently omit others.

    Mark the unreadable cell ``blocked`` and account for every other target, never leaving them outside
    ``blocked`` and ``succeeded``/``failed``. Otherwise callers must subtract known sets from
    ``requested`` to guess, violating ``requested = succeeded ∪ failed ∪ blocked``.
    """
    from lib.artifacts.artifact_manifest import ArtifactComparison, ArtifactStatus

    fake_ctx.pm.project_payload.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    # E1S02 has an old combined image but unreadable manifest status; the other three lack images.
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02.png"}

    class _Resolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "storyboards/scene_E1S02.png":
                raise RuntimeError("manifest sidecar unreadable")
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

    enqueued: list[str] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    monkeypatch.setattr(
        "server.services.grid.grid_submission.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)

    result = read_generation_result(out)
    assert enqueued == []
    assert sorted(result.blocked) == ["E1S01", "E1S02", "E1S03", "E1S04"]
    assert result.succeeded == []
    assert result.failed == []
    for unit_id in ("E1S01", "E1S03", "E1S04"):
        item = next(entry for entry in result.items if entry.unit_id == unit_id)
        assert item.problem is not None
        assert item.problem.code == "generation_artifact_state_unavailable"


async def test_generate_grid_spares_an_already_reusable_sibling_when_one_scene_state_is_unreadable(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unreadable cell blocks whole-grid regeneration without implicating already verified reusable images.
    Those scenes have healthy statuses and merely share the combined image with the broken cell;
    reporting unreadable/repair-needed would be wrong. Record normal skipped reuse instead."""
    from lib.artifacts.artifact_manifest import ArtifactComparison, ArtifactStatus

    fake_ctx.pm.project_payload.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    # E1S02 status raises; E1S03 has reusable CURRENT image; E1S01/E1S04 are missing.
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02.png"}
    fake_ctx.pm.script_payload["segments"][2]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S03.png"}

    class _Resolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "storyboards/scene_E1S02.png":
                raise RuntimeError("manifest sidecar unreadable")
            if artifact_path == "storyboards/scene_E1S03.png":
                return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

    enqueued: list[str] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    monkeypatch.setattr(
        "server.services.grid.grid_submission.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)

    result = read_generation_result(out)
    assert enqueued == []
    assert sorted(result.blocked) == ["E1S01", "E1S02", "E1S04"]
    assert "E1S03" not in result.requested
    assert [s.unit_id for s in result.skipped] == ["E1S03"]


async def test_generate_grid_rejects_an_explicitly_empty_scene_selection(fake_ctx: ToolHarness) -> None:
    """Explicit empty sets do not mean all; reject without silently scanning whole episodes."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "scene_ids": []})

    assert out.problem is not None
    assert out.problem.code == "invalid_request"


async def test_generate_grid_cleans_superseded_records(fake_ctx: ToolHarness) -> None:
    """SDK regeneration cleanup removes old records from frontend lists.

    After generate_grid regenerates a group, remove its completed old records for the same script/episode
    whose scene_ids are subsets of the current group; preserve unrelated groups/generations and in-flight records.
    """
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.models import GridGeneration

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "video_prompt": "v", "segment_break": False}
        for i in range(1, 5)
    ]

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": f"t{resource_id}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    # Seed two old generations: current-group records to clean and other-group records to preserve.
    gm = GridManager(fake_ctx.project_path)
    superseded = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    superseded.status = "completed"
    gm.save(superseded)
    other_group = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S99"],
        rows=1,
        cols=1,
        grid_size="grid_1",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    other_group.status = "completed"
    gm.save(other_group)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)
    assert read_generation_result(out).ok

    remaining = gm.list_all()
    ids = [g.id for g in remaining]
    assert superseded.id not in ids, "superseded old record must be cleaned up"
    assert other_group.id in ids, "records of other groups must not be deleted"
    fresh = [g for g in remaining if g.id != other_group.id]
    assert [g.scene_ids for g in fresh] == [["E1S01", "E1S02", "E1S03", "E1S04"]]


async def test_generate_grid_cleanup_spares_a_fully_reusable_chunk_of_an_oversized_group(fake_ctx: ToolHarness) -> None:
    """Oversized groups split into multiple images must not clean by whole-group IDs; one entire image may contain
    reusable members without gaps and receive no replacement. Whole-group cleanup would delete its completed
    record without new output, losing artifact/manifest bookkeeping and leaving dangling occupancy."""
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.models import GridGeneration

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    # First 9 lack storyboards and need grid_9; last 4 already have reusable images
    # in another grid_4, wholly reusable without replacement.
    missing_ids = [f"E1S{i:02d}" for i in range(1, 10)]
    reusable_ids = [f"E1S{i:02d}" for i in range(10, 14)]
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": sid, "image_prompt": "p", "video_prompt": "v", "segment_break": False} for sid in missing_ids
    ] + [
        {
            "segment_id": sid,
            "image_prompt": "p",
            "video_prompt": "v",
            "segment_break": False,
            "generated_assets": {"storyboard_image": f"storyboards/{sid}.png"},
        }
        for sid in reusable_ids
    ]
    for sid in reusable_ids:
        (fake_ctx.project_path / "storyboards" / f"{sid}.png").write_bytes(b"")

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": f"t{resource_id}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    gm = GridManager(fake_ctx.project_path)
    fully_reusable_chunk = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=reusable_ids,
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    fully_reusable_chunk.status = "completed"
    gm.save(fully_reusable_chunk)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)
    assert read_generation_result(out).ok

    remaining_ids = {g.id for g in gm.list_all()}
    assert fully_reusable_chunk.id in remaining_ids, "chunk 没有缺口、没有生成替代品，其旧记录不得被清理规则误删"


async def test_generate_grid_list_only_falls_back_on_null_aspect_ratio(fake_ctx: ToolHarness) -> None:
    # Preview/enqueue share planning; neither permits None into plan_grid_chunks.
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["aspect_ratio"] = None
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})
    assert isinstance(out.value, GridPlanPreview)
    assert "grid_4 (2×2)" in out.value.plan


async def test_generate_grid_splits_oversized_group_into_multiple_grids(fake_ctx: ToolHarness) -> None:
    # 12 storyboards, non-4K maximum 9: enqueue 2 grids with exact coverage; each prompt shot count equals cell count.
    from lib.script.grid.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["style_description"] = "水墨晕染，留白构图"
    all_ids = [f"E1S{i:02d}" for i in range(1, 13)]
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": sid, "image_prompt": "p", "video_prompt": "v", "segment_break": False} for sid in all_ids
    ]

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        payloads.append(payload)
        return {"task_id": f"t{len(payloads)}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)
    assert read_generation_result(out).ok
    # Generate combined images only; both await user review before splitting.
    assert len(out.value["grid_ids_awaiting_split"]) == 2

    assert [(len(p["scene_ids"]), p["grid_size"]) for p in payloads] == [(9, "grid_9"), (3, "grid_4")]
    # Preserve exact scene coverage and order.
    assert [sid for p in payloads for sid in p["scene_ids"]] == all_ids
    # Build each prompt from its own chunk/tier.
    assert "3×3" in payloads[0]["prompt"]
    assert "2×2" in payloads[1]["prompt"]
    assert all("Visual style: 水墨晕染，留白构图" in p["prompt"].splitlines() for p in payloads)

    # Persisted records match payloads; frame-chain length equals cell count.
    grids = sorted(GridManager(fake_ctx.project_path).list_all(), key=lambda g: len(g.scene_ids), reverse=True)
    assert [(g.scene_ids, g.rows, g.cols) for g in grids] == [(all_ids[:9], 3, 3), (all_ids[9:], 2, 2)]
    assert all(len(g.frame_chain) == g.rows * g.cols for g in grids)


async def test_generate_grid_wrong_mode(fake_ctx: ToolHarness) -> None:
    # Disabled grid_storyboard → error.
    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"})
    assert out.problem is not None


async def test_generate_grid_rejected_on_reference_video_route(fake_ctx: ToolHarness) -> None:
    # reference_video has no storyboard-image step; leftover grid_storyboard=true cannot enable grid tools.
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})
    assert out.problem is not None


async def test_generate_grid_legacy_unresolvable_episode_fails_before_enqueue(
    fake_ctx: ToolHarness,
) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload.pop("episode")
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    enqueue = AsyncMock(side_effect=AssertionError("must not enqueue"))
    batch_waiter = enqueue

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "draft.json"}, batch_waiter=batch_waiter)

    assert out.problem is not None
    assert "无法确定集号" in out.problem.detail
    enqueue.assert_not_awaited()


async def test_generate_grid_blocks_the_whole_chunk_when_a_prompt_is_pending(fake_ctx: ToolHarness) -> None:
    """One missing image_prompt=None prevents combined-image generation; name/block every cell in its chunk."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    fake_ctx.pm.script_payload["segments"][2]["image_prompt"] = None

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("提示词待生成时不该走到入队")

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_waiter
    )

    result = read_generation_result(out)
    assert result.blocked == ["E1S01", "E1S02", "E1S03", "E1S04"]
    problem = next(item for item in result.items if item.unit_id == "E1S01").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("generation_unit_request_invalid", "fix_input")
    assert problem.params["pending_ids"] == ["E1S03"]


def _enable_grid(fake_ctx: ToolHarness, *, groups: int = 1, per_group: int = 4) -> list[str]:
    """Enable grid assembly and create ``groups`` storyboard groups; return IDs."""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    segments = [
        {
            "segment_id": f"E1S{g * per_group + i + 1:02d}",
            "image_prompt": "p",
            "segment_break": i == 0 and g > 0,
        }
        for g in range(groups)
        for i in range(per_group)
    ]
    fake_ctx.pm.script_payload["segments"] = segments
    return [segment["segment_id"] for segment in segments]


def _saved_grid(fake_ctx: ToolHarness, scene_ids: list[str], *, status: str) -> Any:
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.models import GridGeneration

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
    gm = GridManager(fake_ctx.project_path)
    if status == "completed":
        grid.grid_image_path = f"grids/{grid.id}.png"
        gm.image_path(grid.id).write_bytes(b"png")
    gm.save(grid)
    return grid


async def _queue_grid_task(fake_ctx: ToolHarness, grid: Any) -> None:
    """Give in-flight records corresponding active queue tasks; test workers ignore image lanes, leaving tasks queued."""
    await fake_ctx.queue.enqueue_task(
        project_name=fake_ctx.project_name,
        task_type="grid",
        media_type="image",
        resource_id=grid.id,
        payload={"scene_ids": grid.scene_ids},
        script_file=grid.script_file,
        source="webui",
        user_id=fake_ctx.caller.user_id,
    )


async def test_generate_grid_reports_the_ready_composite_and_leaves_the_split_to_the_user(
    fake_ctx: ToolHarness,
) -> None:
    """Generation yields combined images only; successful storyboards point there until user-confirmed split_grids writes cells."""
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx)

    async def fake_enqueue(**_kwargs: Any) -> dict[str, Any]:
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=_fake_grid_waiter(fake_enqueue, fake_wait)
    )

    result = read_generation_result(out)
    (grid,) = GridManager(fake_ctx.project_path).list_all()
    assert result.succeeded == scene_ids
    assert {item.artifact_path for item in result.items} == {f"grids/{grid.id}.png"}
    assert {item.artifact_key for item in result.items} == {ArtifactKey.episode_grid(1, grid.id).encode()}
    assert out.value["grid_ids_awaiting_split"] == [grid.id]
    assert "split_grids" in out.value["summary"]
    assert grid.split_at is None


async def test_generate_grid_reuses_an_identical_in_flight_grid(fake_ctx: ToolHarness) -> None:
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx)
    in_flight = _saved_grid(fake_ctx, scene_ids, status="generating")
    await _queue_grid_task(fake_ctx, in_flight)
    enqueued: list[str] = []

    async def fake_enqueue(*, resource_id: str, **_kwargs: Any) -> dict[str, Any]:
        enqueued.append(resource_id)
        return {"task_id": "t1", "deduped": True}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    out = await run_declared_tool(
        "generate_grid",
        fake_ctx,
        {"script": "episode_1.json", "scene_ids": ["E1S02"]},
        batch_waiter=_fake_grid_waiter(fake_enqueue, fake_wait),
    )

    assert read_generation_result(out).succeeded == ["E1S02"]
    assert "沿用已在生成中的任务（未重复提交）" in out.value["summary"]
    assert enqueued == [in_flight.id]
    assert [g.id for g in GridManager(fake_ctx.project_path).list_all()] == [in_flight.id]


def _queue_backed_enqueue(fake_ctx: ToolHarness, enqueued: list[str]):
    """Submit to test queues whose workers ignore image lanes; queued tasks remain discoverable during planning."""

    async def enqueue(**kwargs: Any) -> dict[str, Any]:
        enqueued.append(kwargs["resource_id"])
        return await fake_ctx.queue.enqueue_task(**kwargs, user_id=fake_ctx.caller.user_id)

    return enqueue


async def test_concurrent_submissions_share_one_grid_instead_of_paying_twice(fake_ctx: ToolHarness) -> None:
    """Concurrent replacements of abandoned records serialize planning until the first enqueues, letting the second reuse its grid."""
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx)
    abandoned = _saved_grid(fake_ctx, scene_ids, status="pending")
    enqueued: list[str] = []

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(_queue_backed_enqueue(fake_ctx, enqueued), fake_wait)
    outs = await asyncio.gather(
        run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter),
        run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter),
    )

    (grid,) = GridManager(fake_ctx.project_path).list_all()
    assert grid.id != abandoned.id
    assert enqueued == [grid.id, grid.id]
    assert [read_generation_result(out).succeeded for out in outs] == [scene_ids, scene_ids]
    # Scheduling chooses the first entrant; exactly one submission reuses the other grid.
    assert sum("沿用已在生成中的任务（未重复提交）" in out.value["summary"] for out in outs) == 1


async def test_a_submission_does_not_hold_back_others_while_its_grid_generates(fake_ctx: ToolHarness) -> None:
    """Exit submission critical sections after enqueue; while prior images generate, later same-project submissions plan/reuse normally."""
    scene_ids = _enable_grid(fake_ctx)
    enqueued: list[str] = []
    first_enqueued = asyncio.Event()
    second_done = asyncio.Event()

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        if not first_enqueued.is_set():
            first_enqueued.set()
            await asyncio.wait_for(second_done.wait(), timeout=5)
        return {"status": "succeeded"}

    batch_waiter = _fake_grid_waiter(_queue_backed_enqueue(fake_ctx, enqueued), fake_wait)
    first = asyncio.create_task(
        run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter)
    )
    await asyncio.wait_for(first_enqueued.wait(), timeout=5)
    second = await asyncio.wait_for(
        run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=batch_waiter),
        timeout=5,
    )
    second_done.set()

    assert read_generation_result(second).succeeded == scene_ids
    assert "沿用已在生成中的任务（未重复提交）" in second.value["summary"]
    assert read_generation_result(await first).succeeded == scene_ids
    assert len(set(enqueued)) == 1


async def test_generate_grid_withholds_healthy_groups_when_one_group_is_blocked(fake_ctx: ToolHarness) -> None:
    """Whole-batch admission blocks healthy groups too when another has reference gaps; no paid enqueue, with per-shot same-batch-blocked outcomes."""
    _enable_grid(fake_ctx, groups=2)
    fake_ctx.pm.script_payload["segments"][6]["scenes"] = ["未登记的场景"]

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("整批受阻时不该走到入队")

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_waiter
    )

    result = read_generation_result(out)
    assert not result.ok
    assert sorted(result.blocked) == [f"E1S0{i}" for i in range(1, 9)]
    by_id = {item.unit_id: item.problem for item in result.items}
    blocked = by_id["E1S05"]
    assert blocked is not None
    assert blocked.code == "reference_asset_unregistered"
    withheld = by_id["E1S01"]
    assert withheld is not None
    assert withheld.code == "generation_batch_admission_withheld"
    assert withheld.params["blocked_unit_ids"] == ["E1S05", "E1S06", "E1S07", "E1S08"]


async def test_generate_grid_refused_batch_still_reports_the_group_already_generating(fake_ctx: ToolHarness) -> None:
    """Whole-batch blocking leaves already-generating groups running; every shot reports await-in-flight instead of disappearing."""
    scene_ids = _enable_grid(fake_ctx, groups=2)
    fake_ctx.pm.script_payload["segments"][6]["scenes"] = ["未登记的场景"]
    in_flight = _saved_grid(fake_ctx, scene_ids[:4], status="generating")
    await _queue_grid_task(fake_ctx, in_flight)

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("整批受阻时不该走到入队")

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json", "scene_ids": scene_ids}, batch_waiter=unreachable_waiter
    )

    result = read_generation_result(out)
    assert sorted(result.blocked) == scene_ids
    items = {item.unit_id: item for item in result.items}
    running = items["E1S01"]
    assert running.problem is not None
    assert (running.problem.code, running.problem.action) == ("generation_active_task_conflict", "wait_for_task")
    assert running.problem.params == {"grid_ids": [in_flight.id]}
    assert running.artifact_path == f"grids/{in_flight.id}.png"
    assert items["E1S05"].problem is not None
    assert items["E1S05"].problem.code == "reference_asset_unregistered"


async def test_generate_grid_missing_only_waits_on_an_unsplit_composite(fake_ctx: ToolHarness) -> None:
    """Missing-only generation skips unsplit combined images instead of paying again, guiding review then splitting."""
    scene_ids = _enable_grid(fake_ctx)
    unsplit = _saved_grid(fake_ctx, scene_ids, status="completed")
    enqueued: list[str] = []

    async def fake_enqueue(*, resource_id: str, **_kwargs: Any) -> dict[str, Any]:
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=_fake_grid_waiter(fake_enqueue)
    )

    result = read_generation_result(out)
    assert enqueued == []
    assert result.requested == []
    assert [(s.unit_id, s.artifact_path) for s in result.skipped] == [
        (scene_id, f"grids/{unsplit.id}.png") for scene_id in scene_ids
    ]
    assert out.value["grid_ids_awaiting_split"] == [unsplit.id]


async def test_generate_grid_judges_a_composite_finished_during_the_batch_against_the_settled_state(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Previously observed unsplit grids still evaluate newly generated combined images against postgeneration targets as current."""
    from lib.artifacts.artifact_manifest import ArtifactComparison, ArtifactStatus
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx, groups=2)
    unsplit = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    gm = GridManager(fake_ctx.project_path)

    class _SnapshotResolver:
        """Like real resolvers, plan target states from the first compared grid record, without later rereads."""

        def __init__(self) -> None:
            self._completed: set[str] | None = None

        def compare(self, key, *, artifact_path):
            if self._completed is None:
                self._completed = {g.id for g in gm.list_all() if g.status == "completed"}
            planned = any(artifact_path == f"grids/{grid_id}.png" for grid_id in self._completed)
            return ArtifactComparison(
                status=ArtifactStatus.CURRENT if planned else ArtifactStatus.STALE, artifact_path=artifact_path
            )

    async def fake_enqueue(*, resource_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"task_id": resource_id}

    def _complete_grid(task_id: str) -> None:
        grid = gm.get(task_id)
        assert grid is not None
        grid.status = "completed"
        grid.grid_image_path = f"grids/{grid.id}.png"
        gm.save(grid)

    async def worker_finishes(task_id: str, **_kwargs: Any) -> dict[str, Any]:
        # Workers persist elsewhere, outside tool event loops.
        await asyncio.to_thread(_complete_grid, task_id)
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.active_artifact_currency_resolver", lambda *_args: _SnapshotResolver())
    out = await run_declared_tool(
        "generate_grid",
        fake_ctx,
        {"script": "episode_1.json"},
        batch_waiter=_fake_grid_waiter(fake_enqueue, worker_finishes),
    )

    result = read_generation_result(out)
    items = {item.unit_id: item for item in result.items}
    assert [(s.unit_id, s.artifact_path) for s in result.skipped] == [
        (scene_id, f"grids/{unsplit.id}.png") for scene_id in scene_ids[:4]
    ]
    assert result.succeeded == scene_ids[4:]
    assert {items[scene_id].artifact_status for scene_id in scene_ids[4:]} == {ArtifactStatus.CURRENT}


async def test_generate_grid_refused_batch_still_lists_the_unsplit_composite(fake_ctx: ToolHarness) -> None:
    """Other blocked groups do not exclude unsplit images from grid_ids_awaiting_split; review/split need not wait for repairs."""
    scene_ids = _enable_grid(fake_ctx, groups=2)
    fake_ctx.pm.script_payload["segments"][6]["scenes"] = ["未登记的场景"]
    unsplit = _saved_grid(fake_ctx, scene_ids[:4], status="completed")

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("整批受阻时不该走到入队")

    out = await run_declared_tool(
        "generate_grid", fake_ctx, {"script": "episode_1.json"}, batch_waiter=unreachable_waiter
    )

    result = read_generation_result(out)
    assert sorted(result.blocked) == scene_ids[4:]
    assert [s.unit_id for s in result.skipped] == scene_ids[:4]
    assert out.value["grid_ids_awaiting_split"] == [unsplit.id]


async def test_generate_grid_list_only_shows_each_grid_record_and_action(fake_ctx: ToolHarness) -> None:
    scene_ids = _enable_grid(fake_ctx, groups=2)
    unsplit = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    in_flight = _saved_grid(fake_ctx, scene_ids[4:], status="pending")
    await _queue_grid_task(fake_ctx, in_flight)

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})

    assert isinstance(out.value, GridPlanPreview)
    text = out.value.plan
    assert f"等待切分落格，本次不重生成；记录 {unsplit.id} 联合图已就绪、未切分" in text
    assert f"正在生成，本次沿用、不重复提交；记录 {in_flight.id} 生成中" in text


async def test_generate_grid_refuses_ad_projects(fake_ctx: ToolHarness) -> None:
    _enable_grid(fake_ctx)
    fake_ctx.pm.project_payload["content_mode"] = "ad"

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})

    assert out.problem is not None
    assert out.problem.code == "ad_grid_not_supported"


async def test_generate_grid_refuses_a_script_of_the_other_route(fake_ctx: ToolHarness) -> None:
    """Skeleton/generation-mode mismatches are input problems, not retryable internal_error."""
    _enable_grid(fake_ctx)
    fake_ctx.pm.script_payload.pop("segments")
    fake_ctx.pm.script_payload["video_units"] = []

    out = await run_declared_tool("generate_grid", fake_ctx, {"script": "episode_1.json", "list_only": True})

    assert out.problem is not None
    assert out.problem.code == "grid_script_route_mismatch"


async def test_split_grids_splits_each_ready_grid_and_explains_the_rest(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.services.grid.grid_split import GridSplitResult

    scene_ids = _enable_grid(fake_ctx, groups=3)
    ready = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    in_flight = _saved_grid(fake_ctx, scene_ids[4:8], status="generating")
    broken = _saved_grid(fake_ctx, scene_ids[8:], status="completed")
    await _queue_grid_task(fake_ctx, in_flight)

    async def fake_split(project_name: str, grid: Any) -> GridSplitResult:
        if grid.id == broken.id:
            raise RuntimeError("disk full")
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)
    out = await run_declared_tool(
        "split_grids", fake_ctx, {"grid_ids": [ready.id, broken.id, in_flight.id, "grid_000000000000"]}
    )

    assert out.problem is None
    results = {r["grid_id"]: r for r in out.value["results"]}
    assert results[ready.id]["status"] == "split"
    assert results[ready.id]["updated_scene_ids"] == scene_ids[:4]
    assert results[broken.id]["status"] == "failed"
    assert results[in_flight.id]["status"] == "in_progress"
    assert results["grid_000000000000"]["status"] == "not_found"


async def test_split_grids_allows_a_record_left_generating_without_an_active_task(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orphan generating records after cancellation/restart without queue tasks permit Agent splitting."""
    from server.services.grid.grid_split import GridSplitResult

    scene_ids = _enable_grid(fake_ctx, groups=1)
    orphan = _saved_grid(fake_ctx, scene_ids[:4], status="generating")

    async def fake_split(project_name: str, grid: Any) -> GridSplitResult:
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)
    out = await run_declared_tool("split_grids", fake_ctx, {"grid_ids": [orphan.id]})

    assert out.problem is None
    assert out.value["results"][0]["status"] == "split"


@pytest.mark.parametrize("content", ['{{"id": "{grid_id}"}}', '{{"id": "{grid_id}", '], ids=["缺字段", "JSON 截断"])
async def test_split_grids_reports_an_unreadable_record_without_abandoning_the_rest(
    fake_ctx: ToolHarness, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Unreadable records fail only that image, not nonexistent; later grids still split normally."""
    from server.services.grid.grid_split import GridSplitResult

    scene_ids = _enable_grid(fake_ctx, groups=2)
    damaged = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    (fake_ctx.project_path / "grids" / f"{damaged.id}.json").write_text(
        content.format(grid_id=damaged.id), encoding="utf-8"
    )
    ready = _saved_grid(fake_ctx, scene_ids[4:], status="completed")

    async def fake_split(project_name: str, grid: Any) -> GridSplitResult:
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)
    out = await run_declared_tool("split_grids", fake_ctx, {"grid_ids": [damaged.id, ready.id]})

    assert out.problem is None
    results = {r["grid_id"]: r for r in out.value["results"]}
    assert results[damaged.id]["status"] == "failed"
    assert results[ready.id]["status"] == "split"


async def test_split_grids_reports_a_problem_when_nothing_was_split(fake_ctx: ToolHarness) -> None:

    _enable_grid(fake_ctx)

    out = await run_declared_tool("split_grids", fake_ctx, {"grid_ids": ["not-a-grid-id"]})

    assert out.problem is not None
    assert out.problem.params["results"] == [
        {"grid_id": "not-a-grid-id", "status": "not_found", "detail": "宫格不存在"}
    ]


async def test_split_grids_refuses_projects_without_grid_storyboard(fake_ctx: ToolHarness) -> None:

    out = await run_declared_tool("split_grids", fake_ctx, {"grid_ids": ["grid_000000000000"]})

    assert out.problem is not None
    assert out.problem.code == "grid_storyboard_not_enabled"
