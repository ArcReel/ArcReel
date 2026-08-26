"""Host-neutral tool for narration audio (TTS) generation.

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。

missing-only 选择只服务于用户显式要求生成旁白配音的这个入口：后期配音的视频请求
不经过这里，缺少 TTS 在那条路径上既不自动补齐，也不算工作流缺口。
"""

from __future__ import annotations

from typing import Any

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactKey
from lib.generation_queue_client import (
    TaskSpec,
    batch_enqueue_and_wait,
)
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.narration_delivery import canonical_narration_text
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton
from lib.speech_composition import SpeechAdmission, SpeechMode, admit_script_unit
from server.media_tools.context import (
    ToolContext,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
    tool_services,
    validate_script_filename,
)
from server.media_tools.definition import tool
from server.tool_runtime import ToolOutcome, submit_media_generation

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


async def handle_generate_narration_audio(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome[Any]:
    try:
        script_filename = validate_script_filename(args["script"])
        segment_ids = normalize_requested_ids(args.get("segment_ids"), field="segment_ids")

        script = ctx.pm.load_script(ctx.project_name, script_filename)

        project = ctx.pm.load_project(ctx.project_name)
        content_mode = resolve_content_mode(script, project)
        ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
        items, id_field, kind = resolve_items(script)
        if not items:
            raise ValueError("剧本没有可配音的单元")
        resolver = active_artifact_currency_resolver(ctx.project_path, project)
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
                source=ctx.caller.source,
            )
            for state in targets
        ]

        submitted = await submit_media_generation(
            scope=ctx.scope,
            caller=ctx.caller,
            services=tool_services(ctx),
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=[state.unit_id for state in targets],
            specs=specs,
            states=by_id,
            embedded_waiter=batch_enqueue_and_wait,
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
    except Exception as exc:  # noqa: BLE001
        return tool_error(_OPERATION, exc)


def generate_narration_audio_tool(ctx: ToolContext):
    @tool(
        _OPERATION,
        "为任意生成模式中由 narrator 拥有发声内容的单元显式生成旁白配音（TTS）。embedded 调用入队并等待完成；"
        "remote MCP 调用返回 durable generation_batch，须按 poll_after_seconds 查询 get_generation_batch 直到 done=true。"
        "script 为剧本文件名（如 episode_1.json）；segment_ids 接受当前骨架的 unit ID 列表"
        "（不传则只选缺旁白配音的 narrator 单元；已失效但可用的旧配音不会被自动重生）。"
        "终态返回 requested / succeeded / failed / blocked 的逐 ID 结果，"
        "每个失败项带稳定 code 与下一步动作。"
        "合成文本在 worker 开始时从最新剧本的规范 narrator utterances 读取，不依赖分镜图或视频。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "segment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "当前剧本骨架的单元 ID 列表；不传则只选缺旁白配音的 narrator 单元",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> ToolOutcome[Any]:
        return await handle_generate_narration_audio(ctx, args)

    return _handler


__all__ = ["generate_narration_audio_tool"]
