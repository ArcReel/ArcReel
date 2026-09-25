"""Host-neutral tool for narration audio (TTS) generation.

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。

missing-only 选择只服务于用户显式要求生成旁白配音的这个入口：后期配音的视频请求
不经过这里，缺少 TTS 在那条路径上既不自动补齐，也不算工作流缺口。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.generation.generation_queue_client import TaskSpec
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.project.resource_paths import resource_relative_path
from lib.script.script_editor import resolve_items
from lib.script.script_models import get_generated_assets, resolve_content_mode
from lib.script.script_skeleton import ensure_route_skeleton
from lib.speech.narration_delivery import canonical_narration_text
from lib.speech.speech_composition import SpeechAdmission, SpeechMode, admit_script_unit
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
)
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest, submit_media_generation

_OPERATION = "generate_narration_audio"


def _tts_admission_problem(admission: SpeechAdmission) -> GenerationProblem | None:
    """Classify why one unit cannot receive narrator TTS, if it cannot."""

    if not admission.allowed:
        first = admission.problems[0]
        return GenerationProblem(
            code=first.code.value,
            detail=f"unit {admission.unit_id} 发声准入未通过：{first.reason.value}",
            action=(
                GenerationAction.REPLAN_UNIT if first.action.value == "replan_unit" else GenerationAction.FIX_INPUT
            ),
            params={"speech_action": first.action.value},
        )
    if admission.mode is not SpeechMode.NARRATOR_VOICEOVER:
        return GenerationProblem(
            code="tts_not_applicable",
            detail=f"unit {admission.unit_id} 的发声不属于叙述旁白，不产出 TTS",
            action=GenerationAction.NONE,
        )
    if not canonical_narration_text(admission.preparation):
        return GenerationProblem(
            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
            detail=f"unit {admission.unit_id} 没有可合成的旁白文本",
            action=GenerationAction.FIX_INPUT,
        )
    return None


def _candidates(
    items: list[dict[str, Any]],
    id_field: str,
    kind: str,
    *,
    episode: int,
    resolver: ArtifactCurrencyResolver,
    explicit: bool,
) -> tuple[list[GenerationCandidate], dict[str, GenerationProblem]]:
    """Build the addressable TTS units plus the reasons some cannot be generated.

    Units whose speech is not narrator-owned are candidates only when the caller
    named them: a missing-only sweep must not present them as gaps.
    """

    candidates: list[GenerationCandidate] = []
    problems: dict[str, GenerationProblem] = {}
    for item in items:
        resource_id = item.get(id_field)
        if not isinstance(resource_id, str) or not resource_id:
            continue
        problem = _tts_admission_problem(admit_script_unit(kind, item))
        if problem is not None:
            if not explicit:
                continue
            problems[resource_id] = problem
        candidates.append(
            GenerationCandidate(
                unit_id=resource_id,
                artifact_key=ArtifactKey.episode_audio(episode, resource_id),
                artifact_path=get_generated_assets(item).get("narration_audio"),
            )
        )
    return candidates, problems


class GenerateNarrationAudioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="剧本纯文件名（不含目录），如 episode_1.json")
    segment_ids: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description="当前剧本骨架的单元 ID 列表；省略则只选缺旁白配音的 narrator 单元",
    )


async def generate_narration_audio(
    request: ToolRequest[GenerateNarrationAudioRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    try:
        script_filename = request.value.script
        segment_ids = normalize_requested_ids(request.value.segment_ids, field="segment_ids")

        script = services.projects.load_script(scope.project_name, script_filename)

        project = services.projects.load_project(scope.project_name)
        content_mode = resolve_content_mode(script, project)
        ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
        items, id_field, kind = resolve_items(script)
        if not items:
            raise ValueError("剧本没有可配音的单元")
        resolver = active_artifact_currency_resolver(services.projects.get_project_path(scope.project_name), project)
        episode = (
            resolve_artifact_episode(
                project=project,
                script=script,
                script_filename=script_filename,
            )
            or 1
        )

        candidates, admission_problems = _candidates(
            items,
            id_field,
            kind,
            episode=episode,
            resolver=resolver,
            explicit=segment_ids is not None,
        )
        selection = select_generation_targets(
            candidates=candidates,
            requested_ids=segment_ids,
            resolver=resolver,
        )
        builder = GenerationResultBuilder.from_selection(_OPERATION, selection)

        targets = []
        for state in selection.targets:
            problem = admission_problems.get(state.unit_id)
            if problem is not None:
                builder.block(
                    state.unit_id,
                    problem=problem,
                    artifact_key=state.artifact_key,
                    artifact_path=state.artifact_path,
                    artifact_status=state.status,
                )
                continue
            targets.append(state)

        by_id = {state.unit_id: state for state in targets}
        specs = [
            TaskSpec.from_request(
                task_type="tts",
                media_type="audio",
                resource_id=state.unit_id,
                prompt=None,
                script_file=script_filename,
                unit_id=state.unit_id,
                source=caller.source,
            )
            for state in targets
        ]

        submitted = await submit_media_generation(
            scope=scope,
            caller=caller,
            services=services,
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=[state.unit_id for state in targets],
            specs=specs,
            states=by_id,
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        if specs:
            record_batch_outcomes(
                builder,
                successes=submitted.successes,
                failures=submitted.failures,
                states=by_id,
                resolver=resolver,
                fallback_path=lambda rid: resource_relative_path("audio", rid),
            )

        return generation_result_outcome(builder.build(), batch_id=submitted.batch.batch_id)
    except Exception as exc:
        return tool_error(_OPERATION, exc)


__all__ = ["GenerateNarrationAudioRequest", "generate_narration_audio"]
