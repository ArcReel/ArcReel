"""Async current-state adapter for the side-effect-free workflow planner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.generation_queue_client import get_active_tasks_for_resources
from lib.generation_result import (
    GenerationProblem,
    GenerationSelectionMode,
    migration_problem,
    provider_checkpoint_from_task,
)
from lib.narration_delivery import USE_TTS
from lib.project_manager import ProjectManager, get_project_manager
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MigrationFailureRecord,
    load_migration_failure,
)
from lib.reference_video.request_projection import ReferenceRequestOptions
from lib.script_batch_edit import script_revision
from lib.script_skeleton import ensure_route_skeleton, resolve_kind_items
from lib.speech_composition import admit_script_unit
from lib.workflow_plan import (
    WorkflowPlan,
    WorkflowPlanRequest,
    WorkflowTaskObservation,
    build_workflow_plan,
)
from lib.workflow_state import WorkflowBlocker, WorkflowStateService, WorkflowStatus
from server.services.video_batch_admission import (
    active_task_problem,
    admit_reference_video_batch,
    admit_storyboard_video_request,
    artifact_state_tickets,
    build_storyboard_video_specs,
    reference_unit_task_spec,
    resolve_reference_batch_targets,
    resolve_voice_context,
    screen_script_entries,
    speech_admission_problems,
)


@dataclass(frozen=True, slots=True)
class _ScriptFacts:
    project: dict[str, Any]
    project_path: Path
    script: dict[str, Any]
    script_file: str
    kind: str
    id_field: str
    items: tuple[dict[str, Any], ...]
    revision: str


class WorkflowPlanner:
    """Compose durable workflow status with transient request and queue facts."""

    def __init__(self, project_manager: ProjectManager):
        self._pm = project_manager

    async def get_plan(
        self,
        project_name: str,
        request: WorkflowPlanRequest,
    ) -> WorkflowPlan:
        status = await asyncio.to_thread(
            WorkflowStateService(self._pm).get_status,
            project_name,
            request.episode,
        )
        blocked = next((b for b in status.blockers if b.code == MIGRATION_FAILURE_CODE), None)
        if blocked is not None:
            # The migration verdict is the whole plan: nothing downstream can be
            # planned off inputs the migration itself refused.
            return build_workflow_plan(
                status,
                narration_delivery=request.narration_delivery,
                structure_problems=[await self._migration_problem(project_name, blocked)],
                script_revision=None,
                task_observations=[],
                admission=None,
            )
        facts = await self._script_facts(project_name, status)
        structure_problems = self._structure_problems(facts)
        tasks = await self._active_tasks(project_name, status, facts, request)
        admission = None
        if (
            facts is not None
            and not structure_problems
            and request.narration_delivery is not None
            and status.state == "VIDEO"
            and status.next_action.type == "generate_videos"
        ):
            admission = await self._video_admission(project_name, status, facts, request)
        return build_workflow_plan(
            status,
            narration_delivery=request.narration_delivery,
            structure_problems=structure_problems,
            script_revision=facts.revision if facts is not None else None,
            task_observations=tasks,
            admission=admission,
        )

    async def _migration_problem(self, project_name: str, blocker: WorkflowBlocker) -> GenerationProblem:
        """Re-read the verdict for the structured locations the blocker cannot carry.

        The blocker already holds the code and the reason; only the record names
        the offending episode and file. A verdict cleared between the two reads
        means a concurrent retry succeeded — the plan still reports the blocker
        it was built from, just without the per-location detail.
        """

        project_path = self._pm.get_project_path(project_name)
        failure = await asyncio.to_thread(load_migration_failure, project_path)
        if failure is None:
            failure = MigrationFailureRecord(schema_version=-1, failed_at="", reason=blocker.reason)
        return migration_problem(failure)

    async def _script_facts(self, project_name: str, status: WorkflowStatus) -> _ScriptFacts | None:
        target = status.target
        script_state = status.artifacts.get("script", {}).get("state")
        if target is None or script_state not in {"current", "stale"}:
            return None

        def _read() -> _ScriptFacts:
            project = self._pm.load_project_readonly(project_name)
            script = self._pm.load_script_readonly(project_name, target.script)
            kind = ensure_route_skeleton(
                script,
                status.project.content_mode,
                status.project.generation_mode,
            )
            raw_items, id_field, _kind = resolve_kind_items(script, kind=kind)
            if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
                raise ValueError(f"{kind} must be an array of objects")
            return _ScriptFacts(
                project=project,
                project_path=self._pm.get_project_path(project_name),
                script=script,
                script_file=target.script_filename,
                kind=kind,
                id_field=id_field,
                items=tuple(raw_items),
                revision=script_revision(script),
            )

        return await asyncio.to_thread(_read)

    @staticmethod
    def _structure_problems(facts: _ScriptFacts | None) -> list[GenerationProblem]:
        if facts is None:
            return []
        problems = []
        for item in facts.items:
            admission = admit_script_unit(facts.kind, item)
            problems.extend(speech_admission_problems(admission))
        return problems

    @staticmethod
    async def _active_tasks(
        project_name: str,
        status: WorkflowStatus,
        facts: _ScriptFacts | None,
        request: WorkflowPlanRequest,
    ) -> list[WorkflowTaskObservation]:
        if facts is None:
            return []
        id_field = facts.id_field
        unit_ids = [
            str(item[id_field]) for item in facts.items if isinstance(item.get(id_field), str) and str(item[id_field])
        ]
        if not unit_ids:
            return []
        task_types = ["reference_video" if status.project.generation_mode == "reference_video" else "video"]
        if status.project.generation_mode == "storyboard" and not status.project.grid_storyboard:
            task_types.append("storyboard")
        if request.narration_delivery == USE_TTS:
            task_types.append("tts")

        rows: list[dict[str, Any]] = []
        for task_type in task_types:
            rows.extend(
                await get_active_tasks_for_resources(
                    project_name=project_name,
                    task_type=task_type,
                    resource_ids=unit_ids,
                    script_file=facts.script_file,
                )
            )
        seen: set[str] = set()
        observations: list[WorkflowTaskObservation] = []
        for task in rows:
            task_id = str(task.get("task_id") or "")
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            observations.append(
                WorkflowTaskObservation(
                    unit_id=str(task.get("resource_id") or ""),
                    task_id=task_id,
                    task_type=str(task.get("task_type") or ""),
                    status=str(task.get("status") or ""),
                    provider_checkpoint=provider_checkpoint_from_task(task),
                    problem=active_task_problem(task),
                )
            )
        return observations

    @staticmethod
    async def _video_admission(
        project_name: str,
        status: WorkflowStatus,
        facts: _ScriptFacts,
        request: WorkflowPlanRequest,
    ) -> dict[str, Any]:
        options = ReferenceRequestOptions(narration_delivery=request.narration_delivery or "post_production")
        if status.project.generation_mode == "reference_video":
            screened, malformed = screen_script_entries(facts.script.get("video_units"), requested_ids=None)
            targets, selection, _states = resolve_reference_batch_targets(
                units=screened,
                requested_ids=None,
                project=facts.project,
                project_path=facts.project_path,
                episode=status.target.episode if status.target is not None else 1,
            )
            extra = [*malformed, *artifact_state_tickets(selection.unavailable)]
            admission = await admit_reference_video_batch(
                project_name=project_name,
                project=facts.project,
                project_path=facts.project_path,
                script=facts.script,
                script_file=facts.script_file,
                units=targets,
                request_options=options,
                operation=status.next_action.type,
                selection=selection.mode,
                confirmed_request_durations=request.confirmed_request_durations,
                spec_check=lambda unit: reference_unit_task_spec(unit, facts.script_file),
                extra_tickets=extra,
            )
            return admission.to_payload()

        # 计划要预告的是「这一批真提交时会得到什么结论」，所以走的是提交侧同一条缝：
        # spec 由 build_storyboard_video_specs 构造（发声准入、输入可用性、prompt 组装
        # 与逐 ID 拒绝票都在其中），准入由 admit_storyboard_video_request 给出（音频闸门
        # 与投影缺口折在同一批票里）。自行挑目标并把视觉提示词留空，会让计划按另一套
        # 视觉基准判断已付费产物能否复用，与真正提交时的结论分叉。
        requested = set(status.next_action.requested_ids)
        id_field = facts.id_field
        items = [
            item for item in facts.items if isinstance(item.get(id_field), str) and str(item[id_field]) in requested
        ]
        voice_characters = await resolve_voice_context(facts.project, status.project.content_mode)
        specs, refused = await asyncio.to_thread(
            build_storyboard_video_specs,
            items=items,
            id_field=id_field,
            content_mode=status.project.content_mode,
            skeleton_kind=facts.kind,
            script_filename=facts.script_file,
            project_dir=facts.project_path,
            skip_ids=None,
            project=facts.project,
            episode=status.target.episode if status.target is not None else 1,
            voice_characters=voice_characters,
        )
        admission = await admit_storyboard_video_request(
            project_name=project_name,
            project=facts.project,
            project_path=facts.project_path,
            script=facts.script,
            script_file=facts.script_file,
            items=items,
            id_field=id_field,
            specs=specs,
            request_options=options,
            operation=status.next_action.type,
            selection=GenerationSelectionMode.MISSING_ONLY,
            confirmed_request_durations=request.confirmed_request_durations,
            extra_tickets=refused,
        )
        return admission.to_payload()


def get_workflow_planner(project_manager: ProjectManager | None = None) -> WorkflowPlanner:
    return WorkflowPlanner(project_manager or get_project_manager())


__all__ = ["WorkflowPlanner", "get_workflow_planner"]
