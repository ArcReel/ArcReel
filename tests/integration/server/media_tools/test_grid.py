"""Tests for enqueue_grid."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import ArtifactStatus
from lib.generation_queue_client import BatchTaskResult, is_interrupted_wait_error
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.media_tools.context import ToolContext
from server.media_tools.grid import generate_grid_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
    _generation_result,
)


def _fake_grid_waiter(enqueue, wait=None):
    async def _waiter(*, project_name, specs, **_kwargs):
        successes: list[BatchTaskResult] = []
        failures: list[BatchTaskResult] = []
        for spec in specs:
            queued = None
            try:
                queued = await enqueue(
                    project_name=project_name,
                    task_type=spec.task_type,
                    media_type=spec.media_type,
                    resource_id=spec.resource_id,
                    payload=spec.payload,
                    script_file=spec.script_file,
                    source=spec.source,
                )
                task = await wait(queued["task_id"])
            except Exception as exc:
                failures.append(
                    BatchTaskResult(
                        resource_id=spec.resource_id,
                        task_id=queued["task_id"] if queued is not None else "",
                        status="interrupted" if is_interrupted_wait_error(exc) else "failed",
                        error=str(exc),
                    )
                )
                continue
            result = BatchTaskResult(
                resource_id=spec.resource_id,
                task_id=queued["task_id"],
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
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    # Need enough segments to form a group with valid layout
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
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
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return allow_large_grid

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert expected in text
    assert forbidden not in text


async def test_generate_grid_list_only_shows_split_for_oversized_group(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 超过单张格数上限的分组，预览按切块后的张数与档位展示，与实际入队同源
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S{i:02d}", "image_prompt": "p", "segment_break": False} for i in range(1, 13)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    text = out["content"][0]["text"]
    assert "2 张宫格: grid_9 (3×3) + grid_4 (2×2)" in text


async def test_generate_grid_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # project.json 允许把 aspect_ratio 显式写为 null；SDK 入队路径须回退到默认比例，
    # 否则 None 会写进宫格规划、任务 payload 与记录上冻结的比例
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["aspect_ratio"] = None  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    async def fake_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        from server.services.grid_split import GridSplitResult

        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    assert [p["video_aspect_ratio"] for p in payloads] == ["9:16"]
    assert [g.video_aspect_ratio for g in GridManager(fake_ctx.project_path).list_all()] == ["9:16"]


async def test_generate_grid_split_failure_keeps_the_paid_image_and_fails_the_id(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """联合图已付费落盘、切分失败：该组每个分镜都记为 failed，不声称产物已就位。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded", "provider_id": "openai", "provider_job_id": "job-1"}

    async def failing_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        raise RuntimeError("cannot write the split cells")

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", failing_split)

    out = await _call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.succeeded == []
    # 逐分镜 ID 报告：一张宫格覆盖的四个分镜各自拿到自己的失败结论。
    assert result.failed == ["E1S01", "E1S02", "E1S03", "E1S04"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "generation_post_processing_failed"
    assert item.problem.detail == "联合图已生成，但切分落格失败（不要重新生成）"
    assert "cannot write the split cells" not in item.problem.detail
    assert item.problem.params["grid_id"].startswith("grid_")
    # 恢复路径只在宫格面板内可执行，不是本工具能派发的下一步：action 不能是
    # RETRY，否则按 action 派发的消费者会重跑 generate_grid，重新生成联合图
    # 并重复计费。
    assert item.problem.action == "none"
    # 任务与供应商提交都成功（钱已花），只有产物没有被标成就位。
    assert item.task_state.value == "succeeded"
    assert item.provider_checkpoint is not None
    assert item.provider_checkpoint.submitted is True
    assert item.artifact_status is not ArtifactStatus.CURRENT


async def test_generate_grid_explicit_failure_preserves_the_old_artifact_path(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名强制重生成失败时，报告仍要带上剧本里登记的旧图路径——否则下游分不清
    「这次替换失败、旧图还在」和「原本就没有可复用产物」，给不出正确的下一步建议。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    out = await _call(
        generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json", "scene_ids": ["E1S01"]}
    )

    assert out.get("is_error") is True
    result = _generation_result(out)
    assert result.failed == ["E1S01"]
    item = result.items[0]
    assert item.artifact_path == "storyboards/E1S01.png"


async def test_generate_grid_wait_timeout_is_reported_as_interrupted_not_failed(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宫格工具经共享 batch waiter 等待时，同样不能把等待被
    打断（任务可能仍在跑）报成终态失败——那会诱导调用方重试、造成重复付费提交。"""
    from lib.generation_queue_client import TaskWaitTimeoutError

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    out = await _call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert result.succeeded == []
    assert result.failed == ["E1S01", "E1S02", "E1S03", "E1S04"]
    item = result.items[0]
    assert item.task_state.value == "interrupted"
    assert item.problem is not None
    assert item.problem.code == "generation_task_interrupted"
    assert item.problem.action == "wait_for_task"


async def test_generate_grid_reports_each_scene_of_a_shared_grid(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同组分镜共享一张宫格，结果仍逐分镜 ID 报告：落格的成功、没落格的单独失败。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    async def fake_enqueue(
        *, project_name, task_type, media_type, resource_id, payload, script_file, source, **_kwargs
    ):
        return {"task_id": "t1"}

    async def fake_wait(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "succeeded", "provider_id": "openai", "provider_job_id": "job-1"}

    async def partial_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        from server.services.grid_split import GridSplitResult

        # 最后一格对应的分镜已不在剧本里，切分时被跳过。
        return GridSplitResult(
            updated_scene_ids=list(grid.scene_ids[:-1]),
            missing_scene_ids=[grid.scene_ids[-1]],
            asset_fingerprints={},
        )

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", partial_split)

    out = await _call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert result.succeeded == ["E1S01", "E1S02", "E1S03"]
    assert result.failed == ["E1S04"]
    assert set(result.requested) == set(result.succeeded) | set(result.failed) | set(result.blocked)
    done = next(item for item in result.items if item.unit_id == "E1S01")
    assert done.artifact_path == "storyboards/scene_E1S01.png"
    dropped = next(item for item in result.items if item.unit_id == "E1S04")
    assert dropped.problem is not None
    assert dropped.problem.code == "generation_post_processing_failed"
    # 联合图这一次是花了钱的，所以未落格的那一格也带着成功的任务与供应商提交。
    assert dropped.task_state.value == "succeeded"


async def test_generate_grid_blocks_the_whole_group_when_one_scene_state_is_unreadable(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """宫格整组共用一张联合图：组里一格产物状态不可读，其余格不能悄悄留空。

    状态不可读的那一格记 ``blocked`` 时，同组其余目标格必须一并有归属——不能既不入
    ``blocked``，也不进 ``succeeded``/``failed``，否则调用方拿不到结论，只能靠
    ``requested`` 减去已知集合去猜，违反 ``requested = succeeded ∪ failed ∪ blocked``
    不变式。
    """
    from lib.artifact_manifest import ArtifactComparison, ArtifactStatus

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    # E1S02 已有一张旧联合图，但其 Manifest 状态读取会炸——组内其它三格都还没图。
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S02.png"
    }

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
        "server.media_tools.grid.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await _call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = _generation_result(out)
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
    from lib.artifact_manifest import ArtifactComparison, ArtifactStatus

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_ctx.pm.script_payload["episode"] = 1  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    # E1S02 状态读取会炸；E1S03 已有旧图且状态 CURRENT（可复用）；E1S01/E1S04 缺图。
    fake_ctx.pm.script_payload["segments"][1]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S02.png"
    }
    fake_ctx.pm.script_payload["segments"][2]["generated_assets"] = {  # type: ignore[attr-defined]
        "storyboard_image": "storyboards/scene_E1S03.png"
    }

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
        "server.media_tools.grid.active_artifact_currency_resolver",
        lambda *_args: _Resolver(),
    )
    batch_waiter = _fake_grid_waiter(fake_enqueue)

    out = await _call(generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "episode_1.json"})

    result = _generation_result(out)
    assert enqueued == []
    assert sorted(result.blocked) == ["E1S01", "E1S02", "E1S04"]
    assert "E1S03" not in result.requested
    assert [s.unit_id for s in result.skipped] == ["E1S03"]


async def test_generate_grid_rejects_an_explicitly_empty_scene_selection(fake_ctx: ToolContext) -> None:
    """显式空集合不是「全部」：拒绝请求，而不是静默扫全集。"""
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]

    out = await _call(generate_grid_tool(fake_ctx), {"script": "episode_1.json", "scene_ids": []})

    assert out.get("is_error") is True
    assert "不能为空数组" in out["content"][0]["text"]


async def test_generate_grid_cleans_superseded_records(fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """重生成清理规则对 SDK 路径生效：旧记录不残留在前端列表。

    通过 generate_grid 重生成某组宫格后，该组旧的已完成记录（同脚本同集、
    scene_ids 是当前组子集）被清理；其它组/代与非在途无关的记录不得误删。
    """
    from lib.grid.models import GridGeneration
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    async def fake_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        from server.services.grid_split import GridSplitResult

        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)

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
    out = await _call(tool_obj, {"script": "episode_1.json"})
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
    from lib.grid.models import GridGeneration
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    # 前 9 个缺分镜图（要生成的一张 grid_9），后 4 个已有可复用旧图
    # （落在另一张 grid_4 里，整张都可复用、不产出替代品）。
    missing_ids = [f"E1S{i:02d}" for i in range(1, 10)]
    reusable_ids = [f"E1S{i:02d}" for i in range(10, 14)]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    async def fake_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        from server.services.grid_split import GridSplitResult

        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)

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
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True

    remaining_ids = {g.id for g in gm.list_all()}
    assert fully_reusable_chunk.id in remaining_ids, "chunk 没有缺口、没有生成替代品，其旧记录不得被清理规则误删"


async def test_generate_grid_list_only_falls_back_on_null_aspect_ratio(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 预览路径与入队路径同源，同样不能让 None 流进 plan_grid_chunks
    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["aspect_ratio"] = None  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]

    async def _gate(_project: dict) -> bool:
        return False

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is not True
    assert "grid_4 (2×2)" in out["content"][0]["text"]


async def test_generate_grid_splits_oversized_group_into_multiple_grids(
    fake_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 12 个分镜 + 非 4K（上限 9）：入队 2 张宫格，分镜不重不漏，每张 prompt 分镜数与格数一致
    from lib.grid_manager import GridManager

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    all_ids = [f"E1S{i:02d}" for i in range(1, 13)]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
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

    # 生成成功后工具会对每张宫格显式调用切分；此处替换为假实现，单独锁定入队分块行为
    split_calls: list[str] = []

    async def fake_split(project_name: str, grid: Any, *, only_scene_ids: Any = None) -> Any:
        from server.services.grid_split import GridSplitResult

        split_calls.append(grid.id)
        return GridSplitResult(updated_scene_ids=list(grid.scene_ids), missing_scene_ids=[], asset_fingerprints={})

    monkeypatch.setattr("server.media_tools.grid.resolve_large_grid_allowed", _gate)
    batch_waiter = _fake_grid_waiter(fake_enqueue, fake_wait)
    monkeypatch.setattr("server.media_tools.grid.apply_grid_split", fake_split)

    tool_obj = generate_grid_tool(fake_ctx, batch_waiter=batch_waiter)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is not True
    # 每张生成成功的宫格都被显式切分
    assert len(split_calls) == 2

    assert [(len(p["scene_ids"]), p["grid_size"]) for p in payloads] == [(9, "grid_9"), (3, "grid_4")]
    # 场景不重不漏且保持顺序
    assert [sid for p in payloads for sid in p["scene_ids"]] == all_ids
    # 每张的 prompt 按自身块与档位构建
    assert "3×3" in payloads[0]["prompt"]
    assert "2×2" in payloads[1]["prompt"]

    # 落盘的 grid 记录与 payload 一致，帧链长度等于格数
    grids = sorted(GridManager(fake_ctx.project_path).list_all(), key=lambda g: len(g.scene_ids), reverse=True)
    assert [(g.scene_ids, g.rows, g.cols) for g in grids] == [(all_ids[:9], 3, 3), (all_ids[9:], 2, 2)]
    assert all(len(g.frame_chain) == g.rows * g.cols for g in grids)


async def test_generate_grid_wrong_mode(fake_ctx: ToolContext) -> None:
    # 项目未开启 grid_storyboard → error
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json"})
    assert out.get("is_error") is True


async def test_generate_grid_rejected_on_reference_video_route(fake_ctx: ToolContext) -> None:
    # reference_video 生成模式无分镜图步骤：即使残留 grid_storyboard=true 也不适用宫格工具
    fake_ctx.pm.project_payload["generation_mode"] = "reference_video"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    tool_obj = generate_grid_tool(fake_ctx)
    out = await _call(tool_obj, {"script": "episode_1.json", "list_only": True})
    assert out.get("is_error") is True


async def test_generate_grid_legacy_unresolvable_episode_fails_before_enqueue(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.media_tools import grid as mod

    fake_ctx.pm.project_payload["generation_mode"] = "storyboard"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["grid_storyboard"] = True  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload.pop("episode")  # type: ignore[attr-defined]
    fake_ctx.pm.script_payload["segments"] = [  # type: ignore[attr-defined]
        {"segment_id": f"E1S0{i}", "image_prompt": "p", "segment_break": False} for i in range(1, 5)
    ]
    enqueue = AsyncMock(side_effect=AssertionError("must not enqueue"))
    batch_waiter = enqueue

    out = await _call(mod.generate_grid_tool(fake_ctx, batch_waiter=batch_waiter), {"script": "draft.json"})

    assert out.get("is_error") is True
    assert "无法确定集号" in out["content"][0]["text"]
    enqueue.assert_not_awaited()
