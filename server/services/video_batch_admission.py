"""Project-state adapter for the all-or-nothing batch video admission.

Web and Agent batch entries share this seam. It resolves the current TTS,
request-projection, quote and queue state for every target of one request, then
folds the per-unit verdicts with :mod:`lib.batch_admission`. Durable request
facts stay in ``lib.narration_delivery`` and ``lib.reference_video``; this module
only adapts server state onto them and never submits a task.

Both routes evaluate the same request options once — the narration delivery
choice and any confirmed request tiers — so the two entries cannot reach
different conclusions about the same project.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifact_manifest import ArtifactKey
from lib.batch_admission import (
    BatchAdmission,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.config.resolver import video_bucket_for_generation_mode
from lib.db import async_session_factory
from lib.generation_queue_client import TaskSpec, get_active_tasks_for_resources
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationSelection,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_state_problem,
    observe_artifact_status,
    select_generation_targets,
)
from lib.narration_delivery import (
    USE_TTS,
    NarratedVideoDurationPreparation,
    NarrationDeliveryProblem,
    VideoRequestCostFacts,
    video_request_cost_unavailable_problem,
    video_request_requires_exact_quote,
    video_request_reuses_current_visual,
)
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    is_structured_video_prompt,
    strip_voice_profiles,
    video_prompt_to_yaml,
)
from lib.reference_video.request_projection import (
    ProjectionProblem,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjection,
    project_reference_unit_request,
)
from lib.script_models import get_generated_assets
from lib.speech_composition import SpeechAdmission, SpeechAdmissionError, require_script_unit_admitted
from lib.storyboard_sequence import StoryboardImageUnavailable
from lib.version_manager import VersionManager
from server.services.cost_estimation import quote_video_request
from server.services.narration_delivery_tasks import (
    active_tts_resource_ids,
    prepare_current_reference_video_request_options,
    prepare_current_storyboard_narrated_video_duration,
)
from server.services.video_caps import assert_audio_switch_supported, resolve_project_is_silent


def video_target_states(
    units: Sequence[Any],
    id_field: str,
    *,
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> dict[str, GenerationTargetState]:
    """Observe each unit's video artifact standing once, for the whole request.

    Every video entry reports the artifact axis from this one observation, so a
    task result and the artifact's current/stale standing never come from two
    different reads.
    """

    states: dict[str, GenerationTargetState] = {}
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(storyboard_item_id(unit, id_field) or "")
        if not unit_id or unit_id in states:
            continue
        artifact_path = get_generated_assets(unit).get("video_clip")
        key = ArtifactKey.episode_video(episode, unit_id)
        status, blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=artifact_path)
        states[unit_id] = GenerationTargetState(
            candidate=GenerationCandidate(
                unit_id=unit_id,
                artifact_key=key,
                artifact_path=artifact_path if isinstance(artifact_path, str) else None,
            ),
            status=status,
            blocker=blocker,
        )
    return states


def artifact_state_tickets(states: Sequence[GenerationTargetState]) -> list[UnitAdmissionTicket]:
    """把产物状态不可读的目标折成准入票，让它们参加同一场全有或全无的判定。

    这一态既不能判为可复用、也不能当作缺失去重生。只记进结果契约而不带进准入的话，
    同批健康的目标仍会入队并计费——那正是这道门要防的部分成批。
    """

    return [UnitAdmissionTicket(unit_id=state.unit_id, problems=(artifact_state_problem(state),)) for state in states]


def storyboard_item_id(item: dict[str, Any], id_field: str) -> object:
    """条目自报的 id，按各入口共用的回退顺序取。

    只在规范字段缺失或写成空串时才退到别名：规范字段写成 ``0`` / ``[]`` 这类非字符串时也退，
    等于让别名替它蒙混过筛查，条目按别名入队计费，而执行期只按规范字段定位，谁也做不出来。
    """

    canonical = item.get(id_field)
    if canonical is None or (isinstance(canonical, str) and not canonical.strip()):
        return item.get("scene_id") or item.get("segment_id")
    return canonical


def diagnostic_unit_id(base: str, taken: Collection[str]) -> str:
    """成不了目标的条目按位置记名，并保证这个名字不与剧本里的合法 id 相同。

    结果契约按 unit_id 记名一次：诊断名与某个真实 id 撞上时，两条不同的条目会被当作
    同一个，写第二遍时 fail loud，用户拿到的是一句通用报错而不是逐目标的结论。
    """

    unit_id = base
    while unit_id in taken:
        unit_id += "*"
    return unit_id


def screen_script_entries(
    entries: object,
    *,
    requested_ids: Collection[str] | None,
) -> tuple[list[dict[str, Any]], list[UnitAdmissionTicket]]:
    """把 video_units 分成「能当目标的」与「成不了目标的」两份，后者按位置记名。

    非对象条目、unit_id 不是字符串、unit_id 为空、以及同一个 unit_id 出现多次，都会让后面
    按 id 索引的每一步失手：条目被静默滤掉时同批健康的 unit 独自入队计费，撞上集合查询时又
    把逐目标的拒绝契约打成一句通用报错。数字、布尔与对象 id 还能一路混过 ``str()`` 进队列，
    执行期按原值比对时找不到 unit 才失败，那时兄弟条目可能已经在跑、已经计费。

    缺失即生成把整个剧本当作目标集合，剧本里任何一处脏条目都参与判定；点名生成的目标集合由
    调用方给定，只有点到的 id 上的脏（同一个 id 的副本）才参与，否则别处的脏数据会否决一次
    精确点名的重做。

    容器本身不是数组时同样在这里记名：遍历它会把请求打成 500，而假值（如 ``false``）会被
    当作空批次报成通过。记名用带方括号的位置写法，与合法 id 不共用命名空间。
    """

    if not isinstance(entries, list):
        return [], [
            refused_ticket(
                "video_units",
                code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                detail=f"video_units 必须是数组，当前为 {type(entries).__name__}",
                action=GenerationAction.FIX_INPUT,
            )
        ]

    clean: list[dict[str, Any]] = []
    tickets: list[UnitAdmissionTicket] = []
    seen: set[str] = set()
    taken = {
        entry["unit_id"].strip()
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str)
    }
    named = set(requested_ids) if requested_ids is not None else None
    if named is not None:
        # 点名的 ID 也占着记名空间：调用方点了一个剧本里没有的名字时，上游还会为它记一条
        # 「不存在」，诊断名与它同名同样会把两条并成一条。
        taken |= named
    for index, entry in enumerate(entries):
        detail: str | None = None
        unit_id = ""
        if not isinstance(entry, dict):
            detail = f"该条目不是对象，当前为 {type(entry).__name__}"
        else:
            raw_id = entry.get("unit_id")
            if raw_id is not None and not isinstance(raw_id, str):
                detail = f"该 unit 的 ID 不是字符串，当前为 {type(raw_id).__name__}"
            else:
                unit_id = (raw_id or "").strip()
                if not unit_id:
                    detail = "该 unit 没有可用的 unit_id"
        if detail is not None:
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"video_units[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=detail,
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            continue
        entry_dict = cast("dict[str, Any]", entry)
        if named is not None and unit_id not in named:
            clean.append(entry_dict)
            continue
        if unit_id in seen:
            tickets.append(
                refused_ticket(
                    diagnostic_unit_id(f"{unit_id}#{index}", taken),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"unit {unit_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen.add(unit_id)
        clean.append(entry_dict)
    return clean, tickets


def resolve_reference_batch_targets(
    *,
    units: Sequence[Any],
    requested_ids: Sequence[str] | None,
    project: dict[str, Any],
    project_path: Path,
    episode: int,
) -> tuple[list[dict[str, Any]], GenerationSelection, dict[str, GenerationTargetState]]:
    """Resolve one request's stable target set before anything is evaluated.

    ``requested_ids`` of ``None`` means missing-only: a unit whose paid video is
    still usable — current *or* stale — stays out of the request, because a stale
    artifact is the user's to replace by naming it, not the batch's to silently
    re-bill. Named ids are taken as given and always regenerate.
    """

    resolver = active_artifact_currency_resolver(project_path, project)
    versions = VersionManager(project_path)
    states = video_target_states(units, "unit_id", episode=episode, resolver=resolver)
    selection = select_generation_targets(
        candidates=[state.candidate for state in states.values()],
        requested_ids=requested_ids,
        resolver=resolver,
        reusable_override=lambda candidate: (
            artifact_is_usable(
                resolver,
                ArtifactKey.episode_video(episode, candidate.unit_id),
                candidate.artifact_path,
            )
            or versions.selected_manual_upload_matches_current_file(
                "reference_videos",
                candidate.unit_id,
                candidate.artifact_path,
            )
        ),
    )
    target_ids = set(selection.target_ids)
    targets = [unit for unit in units if isinstance(unit, dict) and str(unit.get("unit_id") or "") in target_ids]
    return targets, selection, states


def reference_unit_task_spec(unit: object, script_file: str) -> TaskSpec:
    """单 unit 的 TaskSpec 构造，供整批准入判定、批量入队与时长预检共用同一份结构校验
    （见 ADR-0001）——``TaskSpec.from_request`` 是「是否可入队」的唯一真相源，几处判断
    不能各自维护一份、由此产生分歧（如预检放行了入队会拒绝的空提示词 unit）。
    """

    # 用 .get 归一化：缺失 unit_id 的坏数据（Agent 可裸写 script JSON）会被 from_request
    # 当作空 resource_id 拒绝，而不是在此抛 KeyError 中断整批。
    if not isinstance(unit, dict):
        raise ValueError("unit 必须是对象")
    unit_id = str(unit.get("unit_id") or "")
    if unit.get("needs_replan") is True:
        require_script_unit_admitted("video_units", unit)
    text = unit.get("text")
    if not isinstance(text, str):
        # 容器校验落在入队校验这一处：脏值（导入 / Agent 裸写 script 产生的 dict、数字）
        # 不拦就会在下游拼接提示词时抛出 TypeError，把整批打成 500，而不是让这个 unit
        # 带着自己的问题码进入准入结论。
        raise ValueError(f"text 必须是字符串，当前为 {type(text).__name__}")
    if not text.strip():
        raise ValueError("正文为空")
    spec = TaskSpec.from_request(
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        prompt=text,
        script_file=script_file,
    )
    require_script_unit_admitted("video_units", unit)
    return spec


_SPEECH_ACTIONS: dict[str, GenerationAction] = {
    "replan_unit": GenerationAction.REPLAN_UNIT,
    "assign_speaker": GenerationAction.FIX_INPUT,
    "fix_input": GenerationAction.FIX_INPUT,
}

_PROBLEM_ACTIONS: dict[str, GenerationAction] = {
    "generate_tts": GenerationAction.GENERATE_TTS,
    "regenerate_tts": GenerationAction.REGENERATE_TTS,
    "wait_for_tts": GenerationAction.WAIT_FOR_TASK,
    "replan_unit": GenerationAction.REPLAN_UNIT,
    "confirm_request_duration": GenerationAction.CONFIRM_REQUEST_DURATION,
    "retry_cost_estimate": GenerationAction.RETRY,
    "configure_provider": GenerationAction.CONFIGURE_PROVIDER,
    "fix_input": GenerationAction.FIX_INPUT,
    "assign_speaker": GenerationAction.FIX_INPUT,
    "confirm_duration": GenerationAction.CONFIRM_REQUEST_DURATION,
    "configure_video_model": GenerationAction.CONFIGURE_PROVIDER,
    "enable_model_audio": GenerationAction.CONFIGURE_PROVIDER,
    "repair_reference_assets": GenerationAction.GENERATE_DEPENDENCY,
    "review_reference_selection": GenerationAction.FIX_INPUT,
    "review_request_configuration": GenerationAction.FIX_INPUT,
}


def _action_for(raw: object) -> GenerationAction:
    """Map a request-planning action onto the generation-result action set.

    Anything the planning modules add later degrades to ``FIX_INPUT``: telling a
    caller to look at its own request is the only next step that is safe when the
    verdict's meaning is not yet known here.
    """

    return _PROBLEM_ACTIONS.get(str(raw), GenerationAction.FIX_INPUT)


def _generation_problem(problem: ProjectionProblem | NarrationDeliveryProblem, *, unit_id: str) -> GenerationProblem:
    payload = problem.to_payload(unit_id=unit_id)
    params = payload.get("params")
    return GenerationProblem(
        code=problem.code,
        detail=str(payload.get("reason") or problem.code),
        action=_action_for(payload.get("action")),
        params=params if isinstance(params, dict) else {},
    )


def speech_admission_problems(admission: SpeechAdmission) -> tuple[GenerationProblem, ...]:
    """Lift every speech blocker into the shared generation problem contract."""

    payload = admission.to_dict()
    return tuple(
        GenerationProblem(
            code=problem.code.value,
            detail=problem.reason.value,
            action=_SPEECH_ACTIONS.get(problem.action.value, GenerationAction.FIX_INPUT),
            params={"unit_id": admission.unit_id, "speech_admission": payload},
        )
        for problem in admission.problems
    )


def _speech_problem(exc: SpeechAdmissionError) -> GenerationProblem:
    return speech_admission_problems(exc.admission)[0]


def active_task_problem(task: Mapping[str, Any]) -> GenerationProblem:
    return GenerationProblem(
        code=GenerationProblemCode.ACTIVE_TASK_CONFLICT,
        detail=f"该 unit 已有在途任务（状态：{task.get('status')}），等待其结束后再提交本次批量请求",
        action=GenerationAction.WAIT_FOR_TASK,
        params={"task_id": str(task.get("id") or task.get("task_id") or ""), "status": str(task.get("status") or "")},
    )


async def _quote_for_display(
    cost: VideoRequestCostFacts | None,
    *,
    reuses_current_visual: bool,
) -> dict[str, object] | None:
    if cost is None:
        return None
    quote = await quote_video_request(cost, async_session_factory)
    if quote is None:
        return None
    if reuses_current_visual:
        quote = quote.without_new_video_charge()
    return quote.to_payload()


async def _active_conflicts(
    *,
    project_name: str,
    task_type: str,
    script_file: str | None,
    unit_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Map unit id → the active task that already occupies it, if any."""

    if not unit_ids:
        return {}
    active = await get_active_tasks_for_resources(
        project_name=project_name,
        task_type=task_type,
        resource_ids=list(unit_ids),
        script_file=script_file,
    )
    return {str(task["resource_id"]): task for task in active if task.get("resource_id")}


def request_options_for_unit(
    options: ReferenceRequestOptions,
    unit_id: str,
    confirmed: Mapping[str, int] | None,
) -> ReferenceRequestOptions:
    """Apply this request's per-unit tier consent, if the entry collected any.

    Admission and the task spec that follows it must read the same options, so
    both sides call this rather than re-deriving the consent from the request
    body — a second derivation is where the two can silently diverge.
    """

    if not confirmed or unit_id not in confirmed:
        return options
    return replace(options, confirmed_request_duration_seconds=confirmed[unit_id])


async def admit_reference_video_batch(
    *,
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    units: Sequence[dict[str, Any]],
    request_options: ReferenceRequestOptions,
    operation: str,
    selection: GenerationSelectionMode,
    confirmed_request_durations: Mapping[str, int] | None = None,
    spec_check: Callable[[dict[str, Any]], object] | None = None,
    extra_tickets: Sequence[UnitAdmissionTicket] = (),
) -> BatchAdmission:
    """Evaluate every reference unit of one request against the current state.

    ``spec_check`` is the caller's own "can this unit be requested at all" guard
    (structural enqueue validation and speech admission). Sharing it keeps the
    admission and the later spec construction on one verdict instead of two.

    ``extra_tickets`` are targets the caller already refused before reaching this
    seam (an unreadable artifact state, an id that is not in the script). They
    belong to the same request, so they take part in the same all-or-nothing fold
    rather than being reported next to a batch that went ahead without them.
    """

    unit_ids = [str(unit.get("unit_id") or "") for unit in units if str(unit.get("unit_id") or "")]
    conflicts = await _active_conflicts(
        project_name=project_name,
        task_type="reference_video",
        script_file=script_file,
        unit_ids=unit_ids,
    )
    active_tts = (
        await active_tts_resource_ids(
            project_name=project_name,
            resource_ids=unit_ids,
            script_file=script_file,
        )
        if request_options.narration_delivery == USE_TTS
        else frozenset()
    )

    tickets: list[UnitAdmissionTicket] = list(extra_tickets)
    seen_ids: set[str] = set()
    for index, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "")
        # 脏数据（缺 unit_id、同一个 id 出现多次）不能被悄悄丢出目标集合：剩下的健康 unit
        # 会独自入队并计费，正是这道门要防的部分成批。缺 id 的按诊断 id 记名报告，重复的
        # 拒收副本，两者都让整批停在这里由用户去修剧本。
        if not unit_id:
            tickets.append(
                refused_ticket(
                    f"video_units[{index}]",
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail="该 unit 没有可用的 unit_id",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        if unit_id in seen_ids:
            tickets.append(
                refused_ticket(
                    f"{unit_id}#{index}",
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"unit_id {unit_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen_ids.add(unit_id)
        problems: list[GenerationProblem] = []
        if unit_id in conflicts:
            problems.append(active_task_problem(conflicts[unit_id]))
        if spec_check is not None:
            try:
                spec_check(unit)
            except SpeechAdmissionError as exc:
                problems.append(_speech_problem(exc))
            except ValueError as exc:
                problems.append(
                    GenerationProblem(
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=f"入队校验未通过：{exc}",
                        action=GenerationAction.FIX_INPUT,
                    )
                )
        if problems:
            tickets.append(UnitAdmissionTicket(unit_id=unit_id, problems=tuple(problems)))
            continue

        unit_options = request_options_for_unit(request_options, unit_id, confirmed_request_durations)
        try:
            current_options = await prepare_current_reference_video_request_options(
                project=project,
                script=script,
                script_file=script_file,
                unit=unit,
                project_path=project_path,
                options=unit_options,
                project_name=project_name,
                tts_in_progress=unit_id in active_tts,
            )
            projection = await project_reference_unit_request(
                project=project,
                script=script,
                unit=unit,
                project_path=project_path,
                options=current_options,
                tts_in_progress=unit_id in active_tts,
                current_options_materialized=True,
            )
        except ValueError as exc:
            # 投影读的是剧本上的值（如 duration_seconds）：脏值在这里抛出去会让整个请求塌成
            # 一句通用错误，其余 unit 的结论无从得知。按逐 unit 的可入队性问题如实报告。
            tickets.append(
                refused_ticket(
                    unit_id,
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"请求投影失败：{exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        tickets.append(
            await _reference_ticket(
                projection=projection,
                current_options=current_options,
            )
        )

    return BatchAdmission(
        operation=operation,
        selection=selection,
        narration_delivery=request_options.narration_delivery,
        tickets=tuple(tickets),
    )


async def _reference_ticket(
    *,
    projection: ReferenceUnitRequestProjection,
    current_options: ReferenceRequestOptions,
) -> UnitAdmissionTicket:
    unit_id = projection.unit_id
    payload = projection.to_advisory_payload()
    request_duration = projection.request_duration.seconds if projection.request_duration is not None else None
    reuses = video_request_reuses_current_visual(
        request_duration_seconds=request_duration,
        current_reusable_visual_duration_seconds=current_options.current_reusable_visual_duration_seconds,
    )
    cost_payload = await _quote_for_display(projection.cost, reuses_current_visual=reuses)
    problems = [_generation_problem(problem, unit_id=unit_id) for problem in projection.blocking_problems]
    # A missing quote only fails the request closed when TTS is what moved the tier:
    # post-production takes its tier from the script alone, and any tier change there is
    # already held by the duration confirmation the user must answer.
    if (
        cost_payload is None
        and projection.cost is not None
        and current_options.narration_delivery == USE_TTS
        and video_request_requires_exact_quote(
            request_duration_seconds=projection.cost.duration_seconds,
            planned_duration_seconds=projection.planned_duration,
            current_visual_duration_seconds=current_options.current_visual_duration_seconds,
            current_reusable_visual_duration_seconds=current_options.current_reusable_visual_duration_seconds,
        )
    ):
        cost_problem = video_request_cost_unavailable_problem(projection.cost)
        payload["problems"] = [*_payload_problems(payload), cost_problem.to_payload(unit_id=unit_id)]
        payload["allowed"] = False
        problems.append(_generation_problem(cost_problem, unit_id=unit_id))
    if cost_payload is not None:
        payload["request_cost"] = cost_payload
    return UnitAdmissionTicket(
        unit_id=unit_id,
        problems=tuple(problems),
        request_duration_seconds=request_duration,
        current_duration_seconds=projection.current_visual_duration,
        request_cost=cost_payload,
        projection=payload,
    )


def _payload_problems(payload: Mapping[str, object]) -> list[object]:
    existing = payload.get("problems")
    if not isinstance(existing, list):
        raise RuntimeError("request projection problems payload must be a list")
    return existing


async def admit_storyboard_video_batch(
    *,
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    items: Sequence[tuple[str, dict[str, Any], object]],
    request_options: ReferenceRequestOptions,
    operation: str,
    selection: GenerationSelectionMode,
    confirmed_request_durations: Mapping[str, int] | None = None,
    extra_tickets: Sequence[UnitAdmissionTicket] = (),
) -> BatchAdmission:
    """Evaluate every storyboard unit of one request against the current state.

    ``items`` are ``(resource_id, script item, visual prompt)`` triples — the
    prompt participates in the visual basis that decides whether an already paid
    video still covers this request.

    Post-production delivery has no TTS or tier projection to consult on this
    route, so the only shared gate that still applies is the active-task
    conflict; each remaining unit is admitted as it would be on its own.
    """

    resource_ids = [resource_id for resource_id, _item, _prompt in items]
    conflicts = await _active_conflicts(
        project_name=project_name,
        task_type="video",
        script_file=script_file,
        unit_ids=resource_ids,
    )
    active_tts = (
        await active_tts_resource_ids(
            project_name=project_name,
            resource_ids=resource_ids,
            script_file=script_file,
        )
        if request_options.narration_delivery == USE_TTS
        else frozenset()
    )
    capability = video_bucket_for_generation_mode(project.get("generation_mode"))

    tickets: list[UnitAdmissionTicket] = list(extra_tickets)
    for resource_id, item, visual_prompt in items:
        if resource_id in conflicts:
            tickets.append(
                UnitAdmissionTicket(unit_id=resource_id, problems=(active_task_problem(conflicts[resource_id]),))
            )
            continue
        if request_options.narration_delivery != USE_TTS:
            tickets.append(UnitAdmissionTicket(unit_id=resource_id))
            continue
        unit_options = request_options_for_unit(request_options, resource_id, confirmed_request_durations)
        planned = item.get("duration_seconds")
        preparation = await prepare_current_storyboard_narrated_video_duration(
            project_name=project_name,
            project=project,
            project_path=project_path,
            script=script,
            script_file=script_file,
            item=item,
            visual_prompt=visual_prompt,
            seed=None,
            capability=capability,
            planned_duration_seconds=(
                planned if isinstance(planned, int) and not isinstance(planned, bool) and planned > 0 else None
            ),
            confirmed_request_duration_seconds=unit_options.confirmed_request_duration_seconds,
            tts_in_progress=resource_id in active_tts,
        )
        tickets.append(await _storyboard_ticket(resource_id=resource_id, preparation=preparation))

    return BatchAdmission(
        operation=operation,
        selection=selection,
        narration_delivery=request_options.narration_delivery,
        tickets=tuple(tickets),
    )


async def _storyboard_ticket(
    *,
    resource_id: str,
    preparation: NarratedVideoDurationPreparation,
) -> UnitAdmissionTicket:
    payload = preparation.to_payload()
    reuses = video_request_reuses_current_visual(
        request_duration_seconds=preparation.request_duration_seconds,
        current_reusable_visual_duration_seconds=preparation.current_reusable_visual_duration_seconds,
    )
    cost_payload = await _quote_for_display(preparation.cost, reuses_current_visual=reuses)
    problems = [
        _generation_problem(problem, unit_id=resource_id) for problem in preparation.problems if problem.blocking
    ]
    if (
        cost_payload is None
        and preparation.cost is not None
        and video_request_requires_exact_quote(
            request_duration_seconds=preparation.request_duration_seconds,
            planned_duration_seconds=preparation.planned_duration_seconds,
            current_visual_duration_seconds=preparation.current_visual_duration_seconds,
            current_reusable_visual_duration_seconds=preparation.current_reusable_visual_duration_seconds,
        )
    ):
        cost_problem = video_request_cost_unavailable_problem(preparation.cost)
        payload["problems"] = [*_payload_problems(payload), cost_problem.to_payload(unit_id=resource_id)]
        payload["allowed"] = False
        problems.append(_generation_problem(cost_problem, unit_id=resource_id))
    if cost_payload is not None:
        payload["request_cost"] = cost_payload
    return UnitAdmissionTicket(
        unit_id=resource_id,
        problems=tuple(problems),
        request_duration_seconds=preparation.request_duration_seconds,
        current_duration_seconds=preparation.current_visual_duration_seconds,
        request_cost=cost_payload,
        projection=payload,
    )


def speech_admission_ticket(unit_id: str, exc: SpeechAdmissionError) -> UnitAdmissionTicket:
    """Refuse a target on its speech admission, carrying that verdict through.

    The admission's own problem code and action are kept unchanged, so a caller
    never has to read the rendered text to decide what to do next.
    """

    problem = exc.admission.problems[0]
    return refused_ticket(
        unit_id or exc.admission.unit_id,
        code=problem.code.value,
        detail=f"发声准入未通过：{json.dumps(exc.admission.to_dict(), ensure_ascii=False)}",
        action=(GenerationAction.REPLAN_UNIT if problem.action.value == "replan_unit" else GenerationAction.FIX_INPUT),
        params={"speech_admission": exc.admission.to_dict()},
    )


def storyboard_video_prompt(
    item: dict[str, Any], *, content_mode: str, voice_characters: dict[str, Any] | None = None
) -> str:
    prompt = item.get("video_prompt")
    if not prompt:
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"分镜缺少 video_prompt 字段: {item_id}")
    if is_structured_video_prompt(prompt):
        # Voice_Profiles 声明段唯一来源是下方 build_drama_video_prompt 系的机械派生：剧本 JSON
        # 里残留的 voice_profiles 一律先剥离，不因门控不触发（narration/ad、或 drama 无
        # utterances 的条目）而绕过 C 类（真无声）门控直达 YAML。
        prompt = strip_voice_profiles(prompt)
        if content_mode == "drama":
            # drama 口型台词单一真相源在分镜级有序 utterances：取 dialogue-kind 注入 video YAML 的
            # dialogue 出口（drama video_prompt 已不带 dialogue）。utterances 迁移前的存量剧本
            # （load_script 按原始 JSON 读盘不过 pydantic，不会被 DramaScene._migrate_legacy
            # 自动补齐）台词仍留在 video_prompt.dialogue，改走 legacy 出口。
            if "utterances" in item:
                prompt = build_drama_video_prompt(prompt, item.get("utterances"), characters=voice_characters)
            else:
                prompt = build_drama_video_prompt_from_legacy_dialogue(prompt, characters=voice_characters)
        return video_prompt_to_yaml(prompt)
    if isinstance(prompt, dict):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"分镜 video_prompt 为对象但格式不符合结构化规范: {item_id}")
    if not isinstance(prompt, str):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise TypeError(f"分镜 video_prompt 类型无效（期望 str 或 dict）: {item_id}")
    return prompt


async def resolve_voice_context(project: dict[str, Any], content_mode: str) -> dict[str, Any] | None:
    """供 Voice_Profiles 注入的角色资产（``None`` 表示不注入）。

    非 drama 不注入；drama 按无声判据排除（C 类模型不产音、或本集关闭了音频，两条路径同口径）。
    台词不受影响、照常下发。
    """
    if content_mode != "drama":
        return None
    if await resolve_project_is_silent(project):
        return None
    return project.get("characters") or {}


async def audio_switch_conflict(project: dict[str, Any]) -> str | None:
    """分镜图生视频的音频闸门（``assert_audio_switch_supported``，与 WebUI 提交入口同一判据）。

    成片恒有声的模型收不到关闭音频的请求，放行只会让无声判据把音色约束整批裁掉。闸门与创作类型
    无关，narration/ad 同样受检。

    调用点固定在「确有目标要请求」之后：整集已完成、或条目全被 :func:`build_storyboard_video_specs`
    过滤时本就不会产生任何请求，此时拒绝等于把一次正常的空转变成报错。
    参考生视频由公共 request projection 给出同一音频能力判定。

    返回冲突说明文本；无冲突返回 ``None``。调用方把它折成逐目标的准入结论，与其它缺口
    一起在建任务之前一次报全。
    """
    try:
        await assert_audio_switch_supported(project, video_bucket_for_generation_mode(project.get("generation_mode")))
    except ValueError as exc:
        return str(exc)
    return None


def build_storyboard_video_specs(
    *,
    items: list[dict[str, Any]],
    id_field: str,
    content_mode: str,
    skeleton_kind: str,
    script_filename: str,
    project_dir: Path,
    skip_ids: list[str] | None,
    project: dict[str, Any] | None = None,
    episode: int = 1,
    resolver: ArtifactCurrencyResolver | None = None,
    voice_characters: dict[str, Any] | None = None,
) -> tuple[list[TaskSpec], dict[str, int], list[UnitAdmissionTicket]]:
    """Build the Storyboard-mode specs, refusing each unit that cannot be requested.

    A unit whose speech, inputs or prompt are unusable is refused with its own code
    and next action instead of being dropped into a log line, so one bad unit never
    silently shrinks the batch — and, because the refusal is a ticket rather than a
    recorded block, it can hold the whole batch back before any task exists.

    ``skeleton_kind`` 取生成模式闸门给出的剧本实际骨架种类，而不是按创作类型反推：族内的历史
    形态（narration 数据落 ``scenes`` 键）按反推值去适配，合法的旧剧本会被整批判成解析失败。

    发声准入在这里执行，与参考生视频由 ``reference_unit_task_spec`` 承担的位置对应：
    构造 TaskSpec 的这一步是「这个 unit 能不能被请求」的唯一口径，四个 storyboard 入口
    （整集 / 全部 / 点名 / 单条）与只读的工作流计划都经由它，混合发声与 needs_replan 因此
    在任何入口都会在建任务之前扣住整批，计划预告的准入结论也与真正提交时一致。
    """

    item_type = "分镜"
    skip_set = set(skip_ids or [])

    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    refused: list[UnitAdmissionTicket] = []
    project = project or {}
    if resolver is None:
        resolver = active_artifact_currency_resolver(project_dir, project)
    for idx, item in enumerate(items):
        item_id = str(storyboard_item_id(item, id_field))
        if item_id in skip_set:
            continue

        try:
            require_script_unit_admitted(skeleton_kind, item)
        except SpeechAdmissionError as exc:
            # 逐 ID 契约与既有 speech_admission 载荷并存：前者说「这个 ID 没做成、
            # 下一步做什么」，后者带着准入自己的完整定位信息。
            refused.append(speech_admission_ticket(str(item_id), exc))
            continue

        try:
            resolve_usable_storyboard_video_inputs(
                project_path=project_dir,
                project=project,
                episode=episode,
                resource_id=str(item_id),
                item=item,
                resolver=resolver,
            )
        except (OSError, ValueError) as exc:
            # 分镜图本身不可用是最常见的一种，给出下一步该跑什么，而不是只报路径。
            advice = "，请先运行 generate_storyboards" if isinstance(exc, StoryboardImageUnavailable) else ""
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_INPUT_UNUSABLE,
                    detail=f"{item_type} {item_id} 的视频输入不可用: {exc}{advice}",
                    action=GenerationAction.GENERATE_DEPENDENCY,
                )
            )
            continue

        try:
            prompt = storyboard_video_prompt(item, content_mode=content_mode, voice_characters=voice_characters)
        except Exception as exc:  # noqa: BLE001
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"{item_type} {item_id} 的 video_prompt 无效: {exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue

        # duration 是能力维度，留待执行层在 provider 解析后校验（见 ADR-0001）；
        # 原样透传调用方显式指定的值，不在入队侧做 int() 截断式归一化（否则会把
        # 本应被执行层拒绝的非法值静默修正）。缺省由执行层按 caps 收口默认。
        extra_payload: dict[str, Any] = {}
        duration = item.get("duration_seconds")
        if duration is not None:
            extra_payload["duration_seconds"] = duration

        # 构造 TaskSpec 本身也是可入队性判定（空提示词等，``TaskSpecValidationError``
        # 是 ValueError 子类）：不接住就会让一个坏条目把整批打成一句通用报错，
        # 其余条目的结论无从得知。
        try:
            spec = TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                prompt=prompt,
                script_file=script_filename,
                extra_payload=extra_payload or None,
            )
        except ValueError as exc:
            refused.append(
                refused_ticket(
                    str(item_id),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"{item_type} {item_id} 无法构造入队请求: {exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        specs.append(spec)
        order_map[item_id] = idx
    return specs, order_map, refused


async def admit_storyboard_video_request(
    *,
    project_name: str,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    items: Sequence[dict[str, Any]],
    id_field: str,
    specs: Sequence[TaskSpec],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: Sequence[UnitAdmissionTicket],
) -> BatchAdmission:
    """Admit one Storyboard-mode request from the specs it would actually enqueue.

    The visual prompt each spec already carries is what the admission compares
    against the paid artifact, so the triples are built from the specs rather than
    from the raw script items — a preview that skipped them would judge reuse on a
    different basis than the submission it predicts.

    音频开关冲突属于请求自身已知的配置缺口，在这里与投影侧的缺口折进同一批逐目标结论：
    留到提交前才检查，用户会先被问一遍跨档确认、同意之后才收到一句通用报错。
    """

    items_by_id = {str(storyboard_item_id(item, id_field) or ""): item for item in items}
    targets: list[tuple[str, dict[str, Any], object]] = []
    for spec in specs:
        item = items_by_id.get(spec.resource_id)
        if item is None:
            raise ValueError(f"找不到待生成条目: {spec.resource_id}")
        targets.append((spec.resource_id, item, (spec.payload or {}).get("prompt")))
    conflict_detail = await audio_switch_conflict(project) if specs else None
    admission = await admit_storyboard_video_batch(
        project_name=project_name,
        project=project,
        project_path=project_path,
        script=script,
        script_file=script_file,
        items=targets,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
    )
    if conflict_detail is None:
        return admission
    # 音频开关冲突与投影侧的缺口写进同一批票：短路返回只会报出这一条，用户改完配置
    # 重试才撞见下一个已知缺口，正是这道门要免掉的逐条试探。
    conflict = GenerationProblem(
        code="video_audio_switch_not_supported",
        detail=conflict_detail,
        action=GenerationAction.CONFIGURE_PROVIDER,
        params={},
    )
    target_ids = {spec.resource_id for spec in specs}
    return replace(
        admission,
        tickets=tuple(
            replace(ticket, problems=(*ticket.problems, conflict)) if ticket.unit_id in target_ids else ticket
            for ticket in admission.tickets
        ),
    )


__all__ = [
    "active_task_problem",
    "admit_storyboard_video_request",
    "audio_switch_conflict",
    "build_storyboard_video_specs",
    "resolve_voice_context",
    "speech_admission_ticket",
    "storyboard_video_prompt",
    "admit_reference_video_batch",
    "admit_storyboard_video_batch",
    "reference_unit_task_spec",
    "artifact_state_tickets",
    "request_options_for_unit",
    "resolve_reference_batch_targets",
    "speech_admission_problems",
    "video_target_states",
]
