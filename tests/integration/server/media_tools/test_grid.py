"""Tests for enqueue_grid."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.artifact_manifest import ArtifactKey
from lib.generation.generation_queue_client import BatchTaskResult, is_interrupted_wait_error
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.media_tools.context import ToolContext
from server.media_tools.grid import generate_grid_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    call,
    read_generation_result,
)


def _fake_grid_waiter(enqueue, wait=None):
    """与 ``batch_enqueue_and_wait`` 同序：先逐个入队，入队阶段结束调用 ``on_enqueued``，再逐个等待。"""

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


# ---------------------------------------------------------------------------
# enqueue_grid
# ---------------------------------------------------------------------------


async def test_generate_grid_list_only(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    # Need enough segments to form a group with valid layout
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "分组" in out["content"][0]["text"]


@pytest.mark.parametrize(
    ("allow_large_grid", "expected", "forbidden"),
    [(True, "grid_16 (4×4)", "grid_9"), (False, "grid_9 (3×3)", "grid_16")],
)
async def test_generate_grid_list_only_respects_4k_gate(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    allow_large_grid: bool,
    expected: str,
    forbidden: str,
) -> None:
    # 非 4K 时 4×4 / 5×5 不出现在面向 Agent 的分组预览里
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return allow_large_grid

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert expected in text
    assert forbidden not in text


async def test_generate_grid_list_only_shows_split_for_oversized_group(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 超过单张格数上限的分组，预览按切块后的张数与档位展示，与实际入队同源
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "2 张宫格: grid_9 (3×3) + grid_4 (2×2)" in text


async def test_generate_grid_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # project.json 允许把 aspect_ratio 显式写为 null；SDK 入队路径须回退到默认比例，
    # 否则 None 会写进宫格规划、任务 payload 与记录上冻结的比例
    from lib.script.grid.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["aspect_ratio"] = None
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        payloads.append(payload)
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    assert [p["video_aspect_ratio"] for p in payloads] == ["9:16"]
    assert [g.video_aspect_ratio for g in GridManager(fake_ctx.project_path).list_all()] == ["9:16"]


async def test_generate_grid_explicit_failure_preserves_the_old_artifact_path(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名强制重生成失败时，报告仍要带上剧本里登记的旧图路径——否则下游分不清
    「这次替换失败、旧图还在」和「原本就没有可复用产物」，给不出正确的下一步建议。"""
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

    async def _gate(_project: dict) -> bool:
        return False

    async def failing_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        raise RuntimeError("queue is down")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(failing_enqueue)

    out = await call(
        generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json", "scene_ids": ["E1S01"]}
    )

    assert out.get("is_error") is True
    result = read_generation_result(out)
    assert result.failed == ["E1S01"]
    item = result.items[0]
    assert item.artifact_path == "storyboards/E1S01.png"


async def test_generate_grid_wait_timeout_is_reported_as_interrupted_not_failed(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宫格工具经共享 batch waiter 等待时，同样不能把等待被
    打断（任务可能仍在跑）报成终态失败——那会诱导调用方重试、造成重复付费提交。"""
    from lib.generation.generation_queue_client import TaskWaitTimeoutError

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        raise TaskWaitTimeoutError("wait timed out before a terminal state")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert result.succeeded == []
    assert result.failed == ["E1S01", "E1S02", "E1S03", "E1S04"]
    item = result.items[0]
    assert item.task_state.value == "interrupted"
    assert item.problem is not None
    assert item.problem.code == "generation_task_interrupted"
    assert item.problem.action == "wait_for_task"


async def test_generate_grid_reports_the_grid_warnings_on_every_cell(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """联合图的参考图裁剪 warning 属于整张宫格：它报告的每个分镜都带着它，Agent 才知道哪些图没发出。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 3)
    ]
    clamp_warning = {"key": "ref_too_many_images", "params": {"count": 9, "model": "gpt-image-2", "max_count": 8}}

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "succeeded",
            "provider_id": "openai",
            "provider_job_id": "job-1",
            "result": {
                "file_path": "grids/g1.png",
                "warnings": [clamp_warning],
            },
        }

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert result.succeeded == ["E1S01", "E1S02"]
    assert [[w.model_dump() for w in item.warnings] for item in result.items] == [[clamp_warning], [clamp_warning]]
    # Agent 读到的文本摘要把 warning 按文案逐格写出，不直出 key
    assert out["content"][0]["text"].count("参考图数量 9 超出 gpt-image-2 上限 8，已取前 8 张") == 2


async def test_generate_grid_keeps_the_grid_warnings_when_the_task_fails(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宫格任务终态失败时，任务已记录的裁剪 warning 仍随失败条目到达 Agent。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 3)
    ]
    clamp_warning = {"key": "ref_too_many_images", "params": {"count": 9, "model": "gpt-image-2", "max_count": 8}}

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "failed",
            "error_message": "provider rejected the request",
            "result": {"warnings": [clamp_warning]},
        }

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert result.failed == ["E1S01", "E1S02"]
    assert [[w.model_dump() for w in item.warnings] for item in result.items] == [[clamp_warning], [clamp_warning]]
    assert out["content"][0]["text"].count("参考图数量 9 超出 gpt-image-2 上限 8，已取前 8 张") == 2


async def test_generate_grid_blocks_every_scene_of_a_chunk_with_a_reference_gap(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一张联合图覆盖整个 chunk：任一分镜的引用有缺口，chunk 内每个缺口分镜都被记名阻断。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    fake_ctx.pm.script_payload["segments"][2]["scenes"] = ["未登记的场景"]

    async def _gate(_project: dict) -> bool:
        return False

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("引用有缺口时不该走到入队")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=unreachable_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert result.blocked == ["E1S01", "E1S02", "E1S03", "E1S04"]
    problem = next(item for item in result.items if item.unit_id == "E1S01").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("reference_asset_unregistered", "generate_dependency")
    assert problem.params["missing_text"] == "未登记的场景"


async def test_generate_grid_blocks_the_whole_group_when_one_scene_state_is_unreadable(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宫格整组共用一张联合图：组里一格产物状态不可读，其余格不能悄悄留空。

    状态不可读的那一格记 ``blocked`` 时，同组其余目标格必须一并有归属——不能既不入
    ``blocked``，也不进 ``succeeded``/``failed``，否则调用方拿不到结论，只能靠
    ``requested`` 减去已知集合去猜，违反 ``requested = succeeded ∪ failed ∪ blocked``
    不变式。
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
    # E1S02 已有一张旧联合图，但其 Manifest 状态读取会炸——组内其它三格都还没图。
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02.png"}

    class _Resolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "storyboards/scene_E1S02.png":
                raise RuntimeError("manifest sidecar unreadable")
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

    async def _gate(_project: dict) -> bool:
        return False

    enqueued: list[str] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr(
        "server.services.grid.grid_submission.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

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
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同组一格状态不可读会挡住整张宫格的重生成，但不牵连已确认可用的旧图：
    那些场景各自的产物状态是好的，只是恰好和坏的那格共享一张联合图。报它们
    "产物状态不可读、需要修复"是错误结论，仍应按正常复用记为 skipped。"""
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
    # E1S02 状态读取会炸；E1S03 已有旧图且状态 CURRENT（可复用）；E1S01/E1S04 缺图。
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S02.png"}
    fake_ctx.pm.script_payload["segments"][2]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S03.png"}

    class _Resolver:
        def compare(self, key, *, artifact_path):
            if artifact_path == "storyboards/scene_E1S02.png":
                raise RuntimeError("manifest sidecar unreadable")
            if artifact_path == "storyboards/scene_E1S03.png":
                return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

    async def _gate(_project: dict) -> bool:
        return False

    enqueued: list[str] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    monkeypatch.setattr(
        "server.services.grid.grid_submission.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert enqueued == []
    assert sorted(result.blocked) == ["E1S01", "E1S02", "E1S04"]
    assert "E1S03" not in result.requested
    assert [s.unit_id for s in result.skipped] == ["E1S03"]


async def test_generate_grid_rejects_an_explicitly_empty_scene_selection(fake_ctx: ToolContext) -> None:
    """显式空集合不是「全部」：拒绝请求，而不是静默扫全集。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True

    out = await call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "scene_ids": []})

    assert out.get("is_error") is True
    assert "不能为空数组" in out["content"][0]["text"]


async def test_generate_grid_cleans_superseded_records(fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """重生成清理规则对 SDK 路径生效：旧记录不残留在前端列表。

    通过 generate_grid 重生成某组宫格后，该组旧的已完成记录（同脚本同集、
    scene_ids 是当前组子集）被清理；其它组/代与非在途无关的记录不得误删。
    """
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.models import GridGeneration

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "video_prompt": "v", "segment_break": False}
        for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": f"t{resource_id}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    # 预置两代旧记录：一代属于本组（应被清理），一代属于其它组（不得误删）
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

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    remaining = gm.list_all()
    ids = [g.id for g in remaining]
    assert superseded.id not in ids, "superseded old record must be cleaned up"
    assert other_group.id in ids, "records of other groups must not be deleted"
    fresh = [g for g in remaining if g.id != other_group.id]
    assert [g.scene_ids for g in fresh] == [["E1S01", "E1S02", "E1S03", "E1S04"]]


async def test_generate_grid_cleanup_spares_a_fully_reusable_chunk_of_an_oversized_group(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超上限分组切成多张宫格时，清理范围不能按整组算——某一张可能整张都落在
    已复用成员上（该张没有缺口，不会被生成替代品）。若仍按整组 ID 清理，会删掉
    这张对应的旧完成记录却不产出新图，产物与 Manifest 记账双双丢失（悬空占用）。"""
    from lib.script.grid.grid_manager import GridManager
    from lib.script.grid.models import GridGeneration

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    # 前 9 个缺分镜图（要生成的一张 grid_9），后 4 个已有可复用旧图
    # （落在另一张 grid_4 里，整张都可复用、不产出替代品）。
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

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": f"t{resource_id}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
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

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    remaining_ids = {g.id for g in gm.list_all()}
    assert fully_reusable_chunk.id in remaining_ids, "chunk 没有缺口、没有生成替代品，其旧记录不得被清理规则误删"


async def test_generate_grid_list_only_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预览路径与入队路径同源，同样不能让 None 流进 plan_grid_chunks
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["aspect_ratio"] = None
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "grid_4 (2×2)" in out["content"][0]["text"]


async def test_generate_grid_splits_oversized_group_into_multiple_grids(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 12 个分镜 + 非 4K（上限 9）：入队 2 张宫格，分镜不重不漏，每张 prompt 分镜数与格数一致
    from lib.script.grid.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.project_payload["style_description"] = "水墨晕染，留白构图"
    all_ids = [f"E1S{i:02d}" for i in range(1, 13)]
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": sid, "image_prompt": "p", "video_prompt": "v", "segment_break": False} for sid in all_ids
    ]

    async def _gate(_project: dict) -> bool:
        return False

    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        payloads.append(payload)
        return {"task_id": f"t{len(payloads)}"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    # 生成只产出联合图：两张都等用户审阅后切分落格
    assert len(out["grid_ids_awaiting_split"]) == 2

    assert [(len(p["scene_ids"]), p["grid_size"]) for p in payloads] == [(9, "grid_9"), (3, "grid_4")]
    # 场景不重不漏且保持顺序
    assert [sid for p in payloads for sid in p["scene_ids"]] == all_ids
    # 每张的 prompt 按自身块与档位构建
    assert "3×3" in payloads[0]["prompt"]
    assert "2×2" in payloads[1]["prompt"]
    assert all("Visual style: 水墨晕染，留白构图" in p["prompt"].splitlines() for p in payloads)

    # 落盘的 grid 记录与 payload 一致，帧链长度等于格数
    grids = sorted(GridManager(fake_ctx.project_path).list_all(), key=lambda g: len(g.scene_ids), reverse=True)
    assert [(g.scene_ids, g.rows, g.cols) for g in grids] == [(all_ids[:9], 3, 3), (all_ids[9:], 2, 2)]
    assert all(len(g.frame_chain) == g.rows * g.cols for g in grids)


async def test_generate_grid_wrong_mode(fake_ctx: ToolContext) -> None:
    # 项目未开启 grid_storyboard → error
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_grid_rejected_on_reference_video_route(fake_ctx: ToolContext) -> None:
    # reference_video 生成模式无分镜图步骤：即使残留 grid_storyboard=true 也不适用宫格工具
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    tool_obj = generate_grid_tool(fake_ctx)
    out = await call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is True


async def test_generate_grid_legacy_unresolvable_episode_fails_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import grid as mod

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload.pop("episode")
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    enqueue = AsyncMock(side_effect=AssertionError("must not enqueue"))
    batch_waiter = enqueue

    out = await call(mod.generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "draft.json"})

    assert out.get("is_error") is True
    assert "无法确定集号" in out["content"][0]["text"]
    enqueue.assert_not_awaited()


async def test_generate_grid_blocks_the_whole_chunk_when_a_prompt_is_pending(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一格的 image_prompt 尚未填写（None）：整张联合图无从生成，chunk 内每一格都记名阻断。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"
    fake_ctx.pm.project_payload["grid_storyboard"] = True
    fake_ctx.pm.script_payload["segments"] = [
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    fake_ctx.pm.script_payload["segments"][2]["image_prompt"] = None

    async def _gate(_project: dict) -> bool:
        return False

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("提示词待生成时不该走到入队")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)

    out = await call(generate_grid_tool(fake_ctx, batch_waiter=unreachable_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert result.blocked == ["E1S01", "E1S02", "E1S03", "E1S04"]
    problem = next(item for item in result.items if item.unit_id == "E1S01").problem
    assert problem is not None
    assert (problem.code, problem.action) == ("generation_unit_request_invalid", "fix_input")
    assert problem.params["pending_ids"] == ["E1S03"]


def _enable_grid(fake_ctx: ToolContext, *, groups: int = 1, per_group: int = 4) -> list[str]:
    """开启宫格装配并铺 ``groups`` 个分组的分镜，返回分镜 ID。"""
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


async def _no_large_grid(_project: dict) -> bool:
    return False


def _saved_grid(fake_ctx: ToolContext, scene_ids: list[str], *, status: str) -> Any:
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


async def _queue_grid_task(fake_ctx: ToolContext, grid: Any) -> None:
    """让在途记录在队列里有对应的活动任务（测试 worker 不认领 image lane，任务一直 queued）。"""
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
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成只产出联合图：成功的分镜指向所在宫格的联合图，切分落格留给用户确认后的 split_grids。"""
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx)

    async def fake_enqueue(**_kwargs: Any) -> dict[str, Any]:
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(
        generate_grid_tool(fake_ctx, batch_waiter=_fake_grid_waiter(fake_enqueue, fake_wait)),
        {"script": "episode_1.json"},
    )

    result = read_generation_result(out)
    (grid,) = GridManager(fake_ctx.project_path).list_all()
    assert result.succeeded == scene_ids
    assert {item.artifact_path for item in result.items} == {f"grids/{grid.id}.png"}
    assert {item.artifact_key for item in result.items} == {ArtifactKey.episode_grid(1, grid.id).encode()}
    assert out["grid_ids_awaiting_split"] == [grid.id]
    assert "split_grids" in out["content"][0]["text"]
    assert grid.split_at is None


async def test_generate_grid_reuses_an_identical_in_flight_grid(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(
        generate_grid_tool(fake_ctx, batch_waiter=_fake_grid_waiter(fake_enqueue, fake_wait)),
        {"script": "episode_1.json", "scene_ids": ["E1S02"]},
    )

    assert read_generation_result(out).succeeded == ["E1S02"]
    assert "沿用已在生成中的任务（未重复提交）" in out["content"][0]["text"]
    assert enqueued == [in_flight.id]
    assert [g.id for g in GridManager(fake_ctx.project_path).list_all()] == [in_flight.id]


def _queue_backed_enqueue(fake_ctx: ToolContext, enqueued: list[str]):
    """入队落到测试队列（worker 不认领 image lane，任务一直 queued），规划时能探测到在途任务。"""

    async def enqueue(**kwargs: Any) -> dict[str, Any]:
        enqueued.append(kwargs["resource_id"])
        return await fake_ctx.queue.enqueue_task(**kwargs, user_id=fake_ctx.caller.user_id)

    return enqueue


async def test_concurrent_submissions_share_one_grid_instead_of_paying_twice(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两次提交同时替换同一条已无人处理的记录：后者等前者入队后再规划，沿用它的宫格。"""
    from lib.script.grid.grid_manager import GridManager

    scene_ids = _enable_grid(fake_ctx)
    abandoned = _saved_grid(fake_ctx, scene_ids, status="pending")
    enqueued: list[str] = []

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    tool_obj = generate_grid_tool(
        fake_ctx, batch_waiter=_fake_grid_waiter(_queue_backed_enqueue(fake_ctx, enqueued), fake_wait)
    )
    first, second = await asyncio.gather(
        call(tool_obj, {"script": "episode_1.json"}),
        call(tool_obj, {"script": "episode_1.json"}),
    )

    (grid,) = GridManager(fake_ctx.project_path).list_all()
    assert grid.id != abandoned.id
    assert enqueued == [grid.id, grid.id]
    assert read_generation_result(first).succeeded == scene_ids
    assert read_generation_result(second).succeeded == scene_ids
    assert "沿用已在生成中的任务（未重复提交）" in second["content"][0]["text"]


async def test_a_submission_does_not_hold_back_others_while_its_grid_generates(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """入队完成即离开提交临界区：前一张联合图还在生成，同一项目的下一次提交照常规划、沿用它。"""
    scene_ids = _enable_grid(fake_ctx)
    enqueued: list[str] = []
    first_enqueued = asyncio.Event()
    second_done = asyncio.Event()

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        if not first_enqueued.is_set():
            first_enqueued.set()
            await asyncio.wait_for(second_done.wait(), timeout=5)
        return {"status": "succeeded"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    tool_obj = generate_grid_tool(
        fake_ctx, batch_waiter=_fake_grid_waiter(_queue_backed_enqueue(fake_ctx, enqueued), fake_wait)
    )
    first = asyncio.create_task(call(tool_obj, {"script": "episode_1.json"}))
    await asyncio.wait_for(first_enqueued.wait(), timeout=5)
    second = await asyncio.wait_for(call(tool_obj, {"script": "episode_1.json"}), timeout=5)
    second_done.set()

    assert read_generation_result(second).succeeded == scene_ids
    assert "沿用已在生成中的任务（未重复提交）" in second["content"][0]["text"]
    assert read_generation_result(await first).succeeded == scene_ids
    assert len(set(enqueued)) == 1


async def test_generate_grid_withholds_healthy_groups_when_one_group_is_blocked(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整批准入：一组引用有缺口，另一组健康也不入队计费，逐分镜带「同批受阻」结论。"""
    _enable_grid(fake_ctx, groups=2)
    fake_ctx.pm.script_payload["segments"][6]["scenes"] = ["未登记的场景"]

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("整批受阻时不该走到入队")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(generate_grid_tool(fake_ctx, batch_waiter=unreachable_waiter), {"script": "episode_1.json"})

    result = read_generation_result(out)
    assert out.get("is_error") is True
    assert sorted(result.blocked) == [f"E1S0{i}" for i in range(1, 9)]
    by_id = {item.unit_id: item.problem for item in result.items}
    blocked = by_id["E1S05"]
    assert blocked is not None
    assert blocked.code == "reference_asset_unregistered"
    withheld = by_id["E1S01"]
    assert withheld is not None
    assert withheld.code == "generation_batch_admission_withheld"
    assert withheld.params["blocked_unit_ids"] == ["E1S05", "E1S06", "E1S07", "E1S08"]


async def test_generate_grid_refused_batch_still_reports_the_group_already_generating(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整批受阻时，已在生成中的那组照常跑完：逐分镜给「等在途任务」的结论，不从结果里消失。"""
    scene_ids = _enable_grid(fake_ctx, groups=2)
    fake_ctx.pm.script_payload["segments"][6]["scenes"] = ["未登记的场景"]
    in_flight = _saved_grid(fake_ctx, scene_ids[:4], status="generating")
    await _queue_grid_task(fake_ctx, in_flight)

    async def unreachable_waiter(**_kwargs: Any):
        raise AssertionError("整批受阻时不该走到入队")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(
        generate_grid_tool(fake_ctx, batch_waiter=unreachable_waiter),
        {"script": "episode_1.json", "scene_ids": scene_ids},
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


async def test_generate_grid_missing_only_waits_on_an_unsplit_composite(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺失即生成不为未切分的联合图再付一次钱：记为跳过，并提示用户审阅后切分。"""
    scene_ids = _enable_grid(fake_ctx)
    unsplit = _saved_grid(fake_ctx, scene_ids, status="completed")
    enqueued: list[str] = []

    async def fake_enqueue(*, resource_id: str, **_kwargs: Any) -> dict[str, Any]:
        enqueued.append(resource_id)
        return {"task_id": "t1"}

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(
        generate_grid_tool(fake_ctx, batch_waiter=_fake_grid_waiter(fake_enqueue)),
        {"script": "episode_1.json"},
    )

    result = read_generation_result(out)
    assert enqueued == []
    assert result.requested == []
    assert [(s.unit_id, s.artifact_path) for s in result.skipped] == [
        (scene_id, f"grids/{unsplit.id}.png") for scene_id in scene_ids
    ]
    assert out["grid_ids_awaiting_split"] == [unsplit.id]


async def test_generate_grid_list_only_shows_each_grid_record_and_action(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene_ids = _enable_grid(fake_ctx, groups=2)
    unsplit = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    in_flight = _saved_grid(fake_ctx, scene_ids[4:], status="pending")
    await _queue_grid_task(fake_ctx, in_flight)

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _no_large_grid)
    out = await call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "list_only": True})

    text = out["content"][0]["text"]
    assert f"等待切分落格，本次不重生成；记录 {unsplit.id} 联合图已就绪、未切分" in text
    assert f"正在生成，本次沿用、不重复提交；记录 {in_flight.id} 生成中" in text


async def test_generate_grid_refuses_ad_projects(fake_ctx: ToolContext) -> None:
    _enable_grid(fake_ctx)
    fake_ctx.pm.project_payload["content_mode"] = "ad"

    out = await call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "list_only": True})

    assert out.get("is_error") is True
    assert out["problem"]["code"] == "ad_grid_not_supported"


async def test_generate_grid_refuses_a_script_of_the_other_route(fake_ctx: ToolContext) -> None:
    """剧本骨架与生成模式失配是输入问题，不报成可重试的 internal_error。"""
    _enable_grid(fake_ctx)
    fake_ctx.pm.script_payload.pop("segments")
    fake_ctx.pm.script_payload["video_units"] = []

    out = await call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "list_only": True})

    assert out.get("is_error") is True
    assert out["problem"]["code"] == "grid_script_route_mismatch"


async def test_split_grids_splits_each_ready_grid_and_explains_the_rest(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.media_tools.grid import split_grids_tool
    from server.services.grid.grid_split import GridSplitResult

    scene_ids = _enable_grid(fake_ctx, groups=3)
    ready = _saved_grid(fake_ctx, scene_ids[:4], status="completed")
    in_flight = _saved_grid(fake_ctx, scene_ids[4:8], status="generating")
    broken = _saved_grid(fake_ctx, scene_ids[8:], status="completed")

    async def fake_split(project_name: str, grid: Any) -> GridSplitResult:
        if grid.id == broken.id:
            raise RuntimeError("disk full")
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)
    out = await call(
        split_grids_tool(fake_ctx),
        {"grid_ids": [ready.id, broken.id, in_flight.id, "grid_000000000000"]},
    )

    assert out.get("is_error") is not True
    results = {r["grid_id"]: r for r in out["split_grids"]["results"]}
    assert results[ready.id]["status"] == "split"
    assert results[ready.id]["updated_scene_ids"] == scene_ids[:4]
    assert results[broken.id]["status"] == "failed"
    assert results[in_flight.id]["status"] == "in_progress"
    assert results["grid_000000000000"]["status"] == "not_found"


async def test_split_grids_reports_a_problem_when_nothing_was_split(fake_ctx: ToolContext) -> None:
    from server.media_tools.grid import split_grids_tool

    _enable_grid(fake_ctx)

    out = await call(split_grids_tool(fake_ctx), {"grid_ids": ["not-a-grid-id"]})

    assert out.get("is_error") is True
    assert out["problem"]["params"]["results"] == [
        {"grid_id": "not-a-grid-id", "status": "not_found", "detail": "宫格不存在"}
    ]


async def test_split_grids_refuses_projects_without_grid_storyboard(fake_ctx: ToolContext) -> None:
    from server.media_tools.grid import split_grids_tool

    out = await call(split_grids_tool(fake_ctx), {"grid_ids": ["grid_000000000000"]})

    assert out.get("is_error") is True
    assert out["problem"]["code"] == "grid_storyboard_not_enabled"
