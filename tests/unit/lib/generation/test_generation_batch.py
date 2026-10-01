"""``build_generation_batch_read_model``：持久化批次到共享结果契约的终态换算。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import ArtifactComparison, ArtifactKey, ArtifactStatus
from lib.generation.generation_batch import (
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
    build_generation_batch_admission,
    build_generation_batch_read_model,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationItemState,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    GenerationWarning,
)
from lib.generation.task_failure import encode_failure


def _batch(*unit_ids: str) -> dict[str, Any]:
    return {
        "batch_id": "batch-1",
        "project_name": "demo",
        "operation": "generate_storyboards",
        "created_at": "2026-01-01T00:00:00Z",
        "requested": GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id=unit_id) for unit_id in unit_ids],
        ).model_dump(mode="json"),
        "blocked": [],
    }


def _succeeded(unit_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "task_id": f"task-{unit_id}",
        "task_type": "storyboard",
        "status": "succeeded",
        "deduped": False,
        "result": result,
    }


def _replacement_batch() -> dict[str, Any]:
    batch = _batch("E1S01")
    batch["requested"] = GenerationBatchRequestSnapshot(
        selection=GenerationSelectionMode.EXPLICIT,
        requested=[
            GenerationBatchRequestedItem(
                unit_id="E1S01",
                artifact_key=ArtifactKey.episode_grid(1, "grid-1").encode(),
                artifact_path="grids/grid-1.png",
                prior_artifact_key=ArtifactKey.episode_storyboard(1, "E1S01").encode(),
                prior_artifact_path="storyboards/E1S01-old.png",
                prior_artifact_status=ArtifactStatus.STALE,
            )
        ],
    ).model_dump(mode="json")
    return batch


def test_admission_persists_the_prior_artifact_separately_from_the_candidate() -> None:
    storyboard_key = ArtifactKey.episode_storyboard(1, "E1S01")
    snapshot, _blocked = build_generation_batch_admission(
        preflight=GenerationResultBuilder("generate_grid", GenerationSelectionMode.EXPLICIT).build(),
        pending_ids=["E1S01"],
        states={
            "E1S01": GenerationTargetState(
                candidate=GenerationCandidate(
                    unit_id="E1S01",
                    artifact_key=ArtifactKey.episode_grid(1, "grid-1"),
                    artifact_path="grids/grid-1.png",
                ),
                prior_artifact_key=storyboard_key,
                prior_artifact_path="storyboards/E1S01-old.png",
                prior_artifact_status=ArtifactStatus.STALE,
            )
        },
    )

    requested = snapshot.requested[0]
    assert requested.artifact_key == ArtifactKey.episode_grid(1, "grid-1").encode()
    assert requested.artifact_path == "grids/grid-1.png"
    assert requested.prior_artifact_key == storyboard_key.encode()
    assert requested.prior_artifact_path == "storyboards/E1S01-old.png"
    assert requested.prior_artifact_status is ArtifactStatus.STALE


@pytest.mark.parametrize("task_status", ["failed", "cancelled", None])
def test_failed_or_unstarted_replacement_reports_the_prior_artifact(task_status: str | None) -> None:
    tasks = (
        []
        if task_status is None
        else [
            {
                "unit_id": "E1S01",
                "task_id": "task-E1S01",
                "task_type": "grid",
                "status": task_status,
                "deduped": False,
                "error_message": "provider failed",
            }
        ]
    )

    read = build_generation_batch_read_model(_replacement_batch(), tasks, queue_depth={})

    assert read.generation_result is not None
    item = read.generation_result.items[0]
    assert item.artifact_key == ArtifactKey.episode_storyboard(1, "E1S01").encode()
    assert item.artifact_path == "storyboards/E1S01-old.png"
    assert item.artifact_status is ArtifactStatus.STALE


def test_failed_replacement_reobserves_the_prior_artifact_with_a_resolver() -> None:
    storyboard_key = ArtifactKey.episode_storyboard(1, "E1S01")

    class _Resolver:
        def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
            assert key == storyboard_key
            assert artifact_path == "storyboards/E1S01-old.png"
            return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)

    read = build_generation_batch_read_model(
        _replacement_batch(),
        [
            {
                "unit_id": "E1S01",
                "task_id": "task-E1S01",
                "task_type": "grid",
                "status": "failed",
                "deduped": False,
                "error_message": "provider failed",
            }
        ],
        queue_depth={},
        resolver=_Resolver(),
    )

    assert read.generation_result is not None
    assert read.generation_result.items[0].artifact_status is ArtifactStatus.CURRENT


def test_successful_replacement_keeps_reporting_the_generated_candidate() -> None:
    grid_key = ArtifactKey.episode_grid(1, "grid-1")

    class _Resolver:
        def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
            assert key == grid_key
            assert artifact_path == "grids/grid-1.png"
            return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)

    read = build_generation_batch_read_model(
        _replacement_batch(),
        [_succeeded("E1S01", {"file_path": "grids/grid-1.png"})],
        queue_depth={},
        resolver=_Resolver(),
    )

    assert read.generation_result is not None
    item = read.generation_result.items[0]
    assert item.artifact_key == grid_key.encode()
    assert item.artifact_path == "grids/grid-1.png"
    assert item.artifact_status is ArtifactStatus.CURRENT


def test_terminal_result_carries_the_worker_warnings_of_succeeded_items() -> None:
    """MCP 调用方从持久化批次读到的终态结果与内嵌等待路径一样带 worker warnings。"""

    clamp = {"key": "ref_too_many_images", "params": {"count": 8, "model": "viduq2", "max_count": 7}}
    read = build_generation_batch_read_model(
        _batch("E1S01", "E1S02"),
        [
            _succeeded("E1S01", {"file_path": "storyboards/scene_E1S01.png", "warnings": [clamp]}),
            _succeeded("E1S02", {"file_path": "storyboards/scene_E1S02.png"}),
        ],
        queue_depth={},
    )

    assert read.done is True
    assert read.generation_result is not None
    items = {item.unit_id: item for item in read.generation_result.items}
    assert items["E1S01"].state is GenerationItemState.SUCCEEDED
    assert items["E1S01"].warnings == [GenerationWarning(key="ref_too_many_images", params=clamp["params"])]
    assert items["E1S02"].warnings == []


def test_terminal_result_keeps_the_worker_warnings_of_a_post_processing_failure() -> None:
    """任务成功、后处理失败的条目同样带 worker warnings：裁剪提示不随失败一起消失。"""

    clamp = {"key": "ref_too_many_images", "params": {"count": 8, "model": "viduq2", "max_count": 7}}
    read = build_generation_batch_read_model(
        _batch("E1S01"),
        [
            _succeeded(
                "E1S01",
                {
                    "warnings": [clamp],
                    "unit_results": {
                        "E1S01": {
                            "problem": {
                                "code": "generation_post_processing_failed",
                                "detail": "联合图已生成，但切分落格失败（不要重新生成）",
                                "action": "none",
                            }
                        }
                    },
                },
            )
        ],
        queue_depth={},
    )

    assert read.generation_result is not None
    item = read.generation_result.items[0]
    assert item.state is GenerationItemState.FAILED
    assert item.warnings == [GenerationWarning(key="ref_too_many_images", params=clamp["params"])]


def test_a_member_held_back_by_a_failed_in_batch_dependency_reports_that_dependency() -> None:
    """同批前置成员失败时，后继成员从未执行：报「依赖未成功」并点名前置成员，而不是泛化的级联失败。"""

    batch = _batch("character/张三", "character/张三/黑衣")
    batch["requested"] = GenerationBatchRequestSnapshot(
        selection=GenerationSelectionMode.MISSING_ONLY,
        requested=[
            GenerationBatchRequestedItem(unit_id="character/张三"),
            GenerationBatchRequestedItem(unit_id="character/张三/黑衣", depends_on="character/张三"),
        ],
    ).model_dump(mode="json")
    owner_failure = "[provider_rejected] " + json.dumps({"detail": "bad prompt"})
    read = build_generation_batch_read_model(
        batch,
        [
            {
                "unit_id": "character/张三",
                "task_id": "task-owner",
                "task_type": "character",
                "status": "failed",
                "deduped": False,
                "error_message": owner_failure,
            },
            {
                "unit_id": "character/张三/黑衣",
                "task_id": "task-derivative",
                "task_type": "character_derivative",
                "status": "failed",
                "deduped": False,
                "error_message": encode_failure(
                    "cascade_blocked_dependency", dependency_task_id="task-owner", reason=owner_failure
                ),
            },
        ],
        queue_depth={},
    )

    assert read.generation_result is not None
    items = {item.unit_id: item for item in read.generation_result.items}
    assert items["character/张三"].problem is not None
    assert items["character/张三"].problem.code == "provider_rejected"
    held = items["character/张三/黑衣"].problem
    assert held is not None
    assert held.code == GenerationProblemCode.DEPENDENCY_FAILED
    assert held.action is GenerationAction.GENERATE_DEPENDENCY
    assert held.params == {"dependency": "character/张三"}


def test_a_dependency_must_name_a_member_of_the_same_batch() -> None:
    with pytest.raises(ValueError, match="depends on a unit outside this batch"):
        GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.MISSING_ONLY,
            requested=[GenerationBatchRequestedItem(unit_id="character/张三/黑衣", depends_on="character/张三")],
        )
