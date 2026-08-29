from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lib.asset_inventory import complete_asset_inventory
from lib.batch_admission import BatchAdmission, UnitAdmissionTicket
from lib.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints, discover_sources
from lib.generation_batch import GenerationBatchRequestedItem, GenerationBatchRequestSnapshot
from lib.generation_queue import GenerationQueue
from lib.generation_queue_client import TaskSpec
from lib.generation_result import GenerationSelectionMode
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.narration_delivery import POST_PRODUCTION
from lib.project_manager import ProjectManager
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.source_revision import SourceScope, compute_source_revision
from lib.workflow_plan import WorkflowPlanRequest, WorkflowStepState
from lib.workflow_state import (
    WorkflowActionType,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowStatus,
    WorkflowTarget,
)
from server.services import video_batch_admission, workflow_planner


def _status(*, state: str = "VIDEO", action: str = "generate_videos") -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "source_revision": "sha256-v1:source",
            "project": WorkflowProject(
                content_mode="narration",
                generation_mode="storyboard",
                grid_storyboard=False,
            ),
            "target": WorkflowTarget(
                episode=1,
                script="scripts/episode_1.json",
                script_filename="episode_1.json",
                source="source/episode_1.txt",
            ),
            "state": state,
            "blockers": [],
            "gates": {"script_plan_review": {"state": "confirmed", "revision": "script_plan"}},
            "artifacts": {
                "asset_inventory": {"state": "current"},
                "asset_sheets": {},
                "script_plan": {"state": "current"},
                "script": {"state": "current", "path": "scripts/episode_1.json"},
                "storyboards": {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []},
                "videos": {"current_ids": [], "stale_ids": [], "missing_ids": ["E1S01"]},
                "audio": {"current_ids": [], "stale_ids": [], "missing_ids": ["E1S01"]},
            },
            "next_action": WorkflowNextAction(
                type=WorkflowActionType(action),
                requested_ids=["E1S01"],
                reason="next",
            ),
        }
    )


class _ProjectManager:
    def __init__(self, project_path: Path, script: dict[str, Any]):
        self.project_path = project_path
        self.script = script

    def load_project_readonly(self, project_name: str) -> dict[str, Any]:
        assert project_name == "demo"
        return {
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        }

    def load_script_readonly(self, project_name: str, script_file: str) -> dict[str, Any]:
        assert project_name == "demo"
        assert script_file == "scripts/episode_1.json"
        return self.script

    def get_project_path(self, project_name: str) -> Path:
        assert project_name == "demo"
        return self.project_path


def _project_dir(tmp_path: Path) -> Path:
    """真实的 v8 项目目录：计划按产物清单读取产物，纸面路径不成立。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "",
        "narration",
        extras={"generation_mode": "storyboard", "grid_storyboard": False},
    )
    return pm.get_project_path("demo")


def _script(*, mixed: bool = False) -> dict[str, Any]:
    return {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": "E1S01",
                "novel_text": "旁白文本",
                "video_prompt": (
                    {"action": "人物交谈", "dialogue": [{"speaker": "角色A", "line": "台词"}]} if mixed else "镜头提示"
                ),
                "duration_seconds": 5,
                "generated_assets": {},
            }
        ],
    }


def _project_at_text_stage(tmp_path: Path, stage: str, content_mode: str, generation_mode: str) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata(
        "demo",
        "Demo",
        "",
        content_mode,
        extras={"generation_mode": generation_mode, "grid_storyboard": False},
        **({"target_duration": 30} if content_mode == "ad" else {}),
    )
    if stage == "final_script":
        return pm

    project_path = pm.get_project_path("demo")
    source = project_path / "source" / "novel.txt"
    source.write_text("完整原文", encoding="utf-8")
    revision = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", SourceScope(kind="all"), revision)
    if stage == "episode_plan":
        return pm

    (project_path / "source" / "episode_1.txt").write_text("完整原文", encoding="utf-8")

    def _plan(project: dict[str, Any]) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "planned",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 4},
            }
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": 4}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    pm.update_project("demo", _plan)
    return pm


@pytest.mark.parametrize(
    ("stage", "content_mode", "generation_mode", "task_type", "step_id", "resource_id"),
    [
        ("episode_plan", "narration", "storyboard", "text_episode_plan", "episode_plan", "episode-planning"),
        ("script_plan", "narration", "storyboard", "text_narration_script_plan", "script_plan_content", "episode-1"),
        ("script_plan", "drama", "storyboard", "text_drama_script_plan", "script_plan_content", "episode-1"),
        (
            "script_plan",
            "narration",
            "reference_video",
            "text_reference_script_plan",
            "script_plan_content",
            "episode-1",
        ),
        ("final_script", "ad", "storyboard", "text_episode_script", "final_script", "episode-1"),
    ],
)
async def test_recovered_plan_waits_for_active_text_task(
    tmp_path: Path,
    db_factory,
    stage: str,
    content_mode: str,
    generation_mode: str,
    task_type: str,
    step_id: str,
    resource_id: str,
) -> None:
    pm = _project_at_text_stage(tmp_path, stage, content_mode, generation_mode)
    queue = GenerationQueue(session_factory=db_factory, project_manager=pm)
    recovered_user = "recovered-user"
    batch_id = await queue.create_generation_batch(
        project_name="demo",
        operation="text-recovery",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id=resource_id)],
        ),
        blocked=[],
        source="mcp",
        user_id=recovered_user,
    )
    task = await queue.enqueue_task(
        project_name="demo",
        task_type=task_type,
        media_type="text",
        resource_id=resource_id,
        source="mcp",
        user_id=recovered_user,
        batch_id=batch_id,
        batch_unit_id=resource_id,
    )

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan(
        "demo",
        WorkflowPlanRequest(),
        user_id=recovered_user,
        queue=queue,
    )

    step = next(item for item in plan.steps if item.id == step_id)
    assert step.state is WorkflowStepState.ACTIVE
    assert [item.task_id for item in step.tasks] == [task["task_id"]]
    assert [item.batch_id for item in step.tasks] == [batch_id]
    assert plan.next_action.type == "wait_for_task"
    assert plan.next_action.args == {
        "task_ids": [task["task_id"]],
        "batch_ids": [batch_id],
        "poll_after_seconds": 10,
        "max_poll_attempts": 30,
    }


async def test_planner_uses_shared_admission_and_never_reads_the_real_task_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: _status())

    async def _active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    admission_calls: list[dict[str, Any]] = []

    async def _admit(**kwargs: Any) -> BatchAdmission:
        admission_calls.append(kwargs)
        return BatchAdmission(
            operation=kwargs["operation"],
            selection=kwargs["selection"],
            narration_delivery=kwargs["request_options"].narration_delivery,
            tickets=(UnitAdmissionTicket("E1S01"),),
        )

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)
    monkeypatch.setattr(workflow_planner, "admit_storyboard_video_request", _admit)

    request = WorkflowPlanRequest(narration_delivery=POST_PRODUCTION)
    first = await workflow_planner.WorkflowPlanner(pm).get_plan("demo", request)  # type: ignore[arg-type]
    second = await workflow_planner.WorkflowPlanner(pm).get_plan("demo", request)  # type: ignore[arg-type]

    assert first == second
    assert len(admission_calls) == 2
    assert admission_calls[0]["selection"] is GenerationSelectionMode.MISSING_ONLY
    assert first.next_action.type == "generate_videos"
    assert next(step for step in first.steps if step.id == "video").admission["decision"] == "admitted"


async def test_active_task_and_provider_checkpoint_are_reported_as_separate_axes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: _status())

    async def _active_tasks(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["task_type"] != "video":
            return []
        return [
            {
                "task_id": "task-1",
                "resource_id": "E1S01",
                "task_type": "video",
                "status": "running",
                "provider_id": "provider-a",
                "provider_job_id": "job-1",
                "execution_checkpoint_json": "{}",
            }
        ]

    async def _admit(**kwargs: Any) -> BatchAdmission:
        return BatchAdmission(
            operation=kwargs["operation"],
            selection=kwargs["selection"],
            narration_delivery=kwargs["request_options"].narration_delivery,
            tickets=(UnitAdmissionTicket("E1S01"),),
        )

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)
    monkeypatch.setattr(workflow_planner, "admit_storyboard_video_request", _admit)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan(  # type: ignore[arg-type]
        "demo", WorkflowPlanRequest(narration_delivery=POST_PRODUCTION)
    )

    video = next(step for step in plan.steps if step.id == "video")
    assert video.state is WorkflowStepState.ACTIVE
    assert video.artifacts["missing_ids"] == ["E1S01"]
    assert video.tasks[0].status == "running"
    assert video.tasks[0].provider_checkpoint is not None
    assert video.tasks[0].provider_checkpoint.submitted is True
    assert video.tasks[0].provider_checkpoint.provider_job_id == "job-1"
    assert plan.next_action.type == "wait_for_task"
    assert plan.next_action.args == {
        "task_ids": ["task-1"],
        "poll_after_seconds": 10,
        "max_poll_attempts": 30,
    }


async def test_grid_storyboard_plan_waits_for_active_grid_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_path = _project_dir(tmp_path)
    pm = _ProjectManager(project_path, _script())
    status = _status(state="STORYBOARD", action="generate_grid").model_copy(
        update={
            "project": WorkflowProject(
                content_mode="narration",
                generation_mode="storyboard",
                grid_storyboard=True,
            )
        }
    )
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: status)
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01"],
        rows=1,
        cols=1,
        grid_size="1x1",
        provider="",
        model="",
        video_aspect_ratio="9:16",
    )
    GridManager(project_path).save(grid)

    async def _active_tasks(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["task_type"] != "grid":
            return []
        assert kwargs["resource_ids"] == [grid.id]
        return [
            {
                "task_id": "grid-task",
                "resource_id": grid.id,
                "task_type": "grid",
                "status": "running",
            }
        ]

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan("demo", WorkflowPlanRequest())  # type: ignore[arg-type]

    assert next(step for step in plan.steps if step.id == "storyboard").state is WorkflowStepState.ACTIVE
    assert plan.next_action.type == "wait_for_task"
    assert plan.next_action.args["task_ids"] == ["grid-task"]


async def test_asset_sheet_plan_waits_for_active_asset_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(
        pm,
        "load_project_readonly",
        lambda _project: {
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "characters": {"张三": {"description": "黑衣剑客"}},
        },
    )
    monkeypatch.setattr(
        workflow_planner.WorkflowStateService,
        "get_status",
        lambda *_args: _status(state="ASSET_SHEETS", action="generate_asset_sheets"),
    )

    async def _active_tasks(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["task_type"] != "character":
            return []
        assert kwargs["resource_ids"] == ["张三"]
        return [
            {
                "task_id": "character-task",
                "batch_id": "asset-batch",
                "resource_id": "张三",
                "task_type": "character",
                "status": "running",
            }
        ]

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan("demo", WorkflowPlanRequest())  # type: ignore[arg-type]

    step = next(item for item in plan.steps if item.id == "asset_sheets")
    assert step.state is WorkflowStepState.ACTIVE
    assert [task.task_id for task in step.tasks] == ["character-task"]
    assert plan.next_action.type == "wait_for_task"
    assert plan.next_action.args["batch_ids"] == ["asset-batch"]


async def test_product_task_replanning_returns_its_durable_handle_without_crossing_callers(
    tmp_path: Path, db_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_path = _project_dir(tmp_path)
    queue_pm = ProjectManager(project_path.parent)
    planner_pm = _ProjectManager(project_path, _script())
    monkeypatch.setattr(
        planner_pm,
        "load_project_readonly",
        lambda _project: {
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "products": {"杯子": {"description": "透明杯"}},
        },
    )

    class ProductWorkflowStateService:
        def __init__(self, project_manager: object):
            assert project_manager is planner_pm

        def get_status(self, *_args: object) -> WorkflowStatus:
            return _status(state="STORYBOARD", action="generate_storyboards")

    monkeypatch.setattr(workflow_planner, "WorkflowStateService", ProductWorkflowStateService)
    queue = GenerationQueue(session_factory=db_factory, project_manager=queue_pm)
    caller = "caller-a"
    batch_id = await queue.create_generation_batch(
        project_name="demo",
        operation="generate_assets",
        requested=GenerationBatchRequestSnapshot(
            selection=GenerationSelectionMode.EXPLICIT,
            requested=[GenerationBatchRequestedItem(unit_id="product/杯子")],
        ),
        blocked=[],
        source="mcp",
        user_id=caller,
    )
    task = await queue.enqueue_task(
        project_name="demo",
        task_type="product",
        media_type="image",
        resource_id="杯子",
        source="mcp",
        user_id=caller,
        provider_id="test-provider",
        batch_id=batch_id,
        batch_unit_id="product/杯子",
    )
    claimed = await queue.claim_next_task("image")
    assert claimed is not None and claimed["task_id"] == task["task_id"]
    other_task = await queue.enqueue_task(
        project_name="demo",
        task_type="product",
        media_type="image",
        resource_id="杯子",
        source="mcp",
        user_id="caller-b",
        provider_id="test-provider",
    )

    planner = workflow_planner.WorkflowPlanner(planner_pm)  # type: ignore[arg-type]
    plan = await planner.get_plan("demo", WorkflowPlanRequest(), user_id=caller, queue=queue)
    other_plan = await planner.get_plan("demo", WorkflowPlanRequest(), user_id="caller-b", queue=queue)

    step = next(item for item in plan.steps if item.id == "asset_sheets")
    assert step.state is WorkflowStepState.ACTIVE
    assert [item.task_id for item in step.tasks] == [task["task_id"]]
    assert plan.next_action.type == "wait_for_task"
    assert plan.next_action.args == {
        "task_ids": [task["task_id"]],
        "batch_ids": [batch_id],
        "poll_after_seconds": 10,
        "max_poll_attempts": 30,
    }
    assert other_plan.next_action.type == "wait_for_task"
    assert other_plan.next_action.args["task_ids"] == [other_task["task_id"]]


async def test_recovery_checkpoint_without_provider_job_remains_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: _status())

    async def _active_tasks(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["task_type"] != "video":
            return []
        return [
            {
                "task_id": "task-recovery",
                "resource_id": "E1S01",
                "task_type": "video",
                "status": "running",
                "provider_id": "provider-a",
                "provider_job_id": None,
                "execution_checkpoint_json": '{"schema_version": 1}',
            }
        ]

    async def _admit(**kwargs: Any) -> BatchAdmission:
        return BatchAdmission(
            operation=kwargs["operation"],
            selection=kwargs["selection"],
            narration_delivery=kwargs["request_options"].narration_delivery,
            tickets=(UnitAdmissionTicket("E1S01"),),
        )

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)
    monkeypatch.setattr(workflow_planner, "admit_storyboard_video_request", _admit)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan(  # type: ignore[arg-type]
        "demo", WorkflowPlanRequest(narration_delivery=POST_PRODUCTION)
    )

    checkpoint = next(step for step in plan.steps if step.id == "video").tasks[0].provider_checkpoint
    assert checkpoint is not None
    assert checkpoint.submitted is False
    assert checkpoint.provider_id == "provider-a"
    assert checkpoint.provider_job_id is None


async def test_mixed_speech_blocks_before_storyboard_and_uses_atomic_script_edit_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _ProjectManager(_project_dir(tmp_path), _script(mixed=True))
    monkeypatch.setattr(
        workflow_planner.WorkflowStateService,
        "get_status",
        lambda *_args: _status(state="STORYBOARD", action="generate_storyboards"),
    )

    async def _active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _active_tasks)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan("demo", WorkflowPlanRequest())  # type: ignore[arg-type]

    structure = next(step for step in plan.steps if step.id == "script_structure")
    storyboard = next(step for step in plan.steps if step.id == "storyboard")
    assert structure.state is WorkflowStepState.BLOCKED
    assert structure.problems[0].code == "mixed_speech"
    assert structure.contracts.script_edit == "script_batch_edit/v1"
    assert storyboard.state is WorkflowStepState.PENDING
    assert plan.next_action.type == "patch_episode_script"
    assert plan.next_action.args["base_revision"].startswith("sha256-v1:")


async def test_status_read_is_idempotent_and_does_not_touch_project_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    pm = ProjectManager(projects_root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Ad", "", "ad", target_duration=30)

    async def _no_active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _no_active_tasks)

    def _snapshot() -> dict[str, tuple[bytes, int]]:
        project_path = pm.get_project_path("demo")
        return {
            path.relative_to(project_path).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in project_path.rglob("*")
            if path.is_file()
        }

    before = _snapshot()
    planner = workflow_planner.WorkflowPlanner(pm)
    first = await planner.get_plan("demo", WorkflowPlanRequest())
    middle = _snapshot()
    second = await planner.get_plan("demo", WorkflowPlanRequest())
    after = _snapshot()

    assert first == second
    assert before == middle == after


async def test_planner_refuses_a_unit_whose_video_input_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """计划走的是提交侧同一条 spec 构造缝：分镜图不可用的条目在计划里就被逐 ID 拒绝。"""

    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: _status())

    async def _no_active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _no_active_tasks)
    monkeypatch.setattr(video_batch_admission, "get_active_tasks_for_resources", _no_active_tasks)

    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    plan = await workflow_planner.WorkflowPlanner(pm).get_plan(  # type: ignore[arg-type]
        "demo", WorkflowPlanRequest(narration_delivery=POST_PRODUCTION)
    )

    # 走提交侧那条缝要读 Manifest 与分镜图，读到的一切仍不得在项目目录留下痕迹。
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before

    video = next(step for step in plan.steps if step.id == "video")
    assert video.admission is not None
    assert video.admission["decision"] != "admitted"
    codes = {problem["code"] for ticket in video.admission["units"] for problem in ticket["problems"]}
    assert "generation_unit_input_unusable" in codes


async def test_planner_reports_the_audio_switch_conflict_before_any_task_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """音频闸门与入队入口同一道：计划预告的准入结论包含它，用户不必提交后才撞见。"""

    pm = _ProjectManager(_project_dir(tmp_path), _script())
    monkeypatch.setattr(workflow_planner.WorkflowStateService, "get_status", lambda *_args: _status())

    async def _no_active_tasks(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def _reject(_project: dict[str, Any], _capability: Any) -> None:
        raise ValueError("成片恒有声")

    spec = TaskSpec.from_request(
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        prompt="镜头提示",
        script_file="episode_1.json",
    )

    def _specs(**_kwargs: Any):
        return [spec], []

    monkeypatch.setattr(workflow_planner, "get_active_tasks_for_resources", _no_active_tasks)
    monkeypatch.setattr(workflow_planner, "build_storyboard_video_specs", _specs)
    monkeypatch.setattr(video_batch_admission, "get_active_tasks_for_resources", _no_active_tasks)
    monkeypatch.setattr(video_batch_admission, "assert_audio_switch_supported", _reject)

    plan = await workflow_planner.WorkflowPlanner(pm).get_plan(  # type: ignore[arg-type]
        "demo", WorkflowPlanRequest(narration_delivery=POST_PRODUCTION)
    )

    video = next(step for step in plan.steps if step.id == "video")
    assert video.admission is not None
    codes = {problem["code"] for ticket in video.admission["units"] for problem in ticket["problems"]}
    assert "video_audio_switch_not_supported" in codes
