"""Host-neutral tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.episode.episode_ids import describe_episode_for_agent
from lib.generation.batch_admission import (
    BatchAdmission,
    BatchAdmissionDecision,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.generation.generation_batch import GenerationBatchReadModel
from lib.generation.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_is_reusable,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.script.reference_video.request_projection import ReferenceRequestOptions
from lib.script.script_models import get_generated_assets, resolve_content_mode
from lib.script.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.script.storyboard_sequence import get_storyboard_items
from lib.speech.speech_composition import (
    SpeechAdmissionError,
    video_unit_replan_problems,
)
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_result_outcome,
    tool_error,
)
from server.services.admission.video_batch_admission import (
    admit_reference_video_batch,
    admit_storyboard_video_request,
    artifact_state_tickets,
    build_storyboard_video_specs,
    reference_unit_task_spec,
    request_options_for_unit,
    resolve_voice_context,
    screen_script_entries,
    screen_storyboard_items,
    speech_admission_ticket,
    storyboard_item_aliases,
    storyboard_item_id,
    video_target_states,
)
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    Services,
    ToolOutcome,
    ToolProblem,
    ToolRequest,
    submit_media_generation,
)

logger = logging.getLogger(__name__)

#: 已退役的入参名 → 该怎么写。键都是曾经真实存在、或与视频单元旧结构同名的写法：
#: 前三个是点名目标的旧 id 参数，``shots`` 与参考清单是视频单元已删除的字段，
#: ``narration_delivery`` 是已删除的按请求交付方式。视频单元现在只持有 ``text`` 与
#: ``duration_seconds``，参考图在执行期从正文的 ``@[名称]`` 首次提及顺序派生；
#: 旁白交付方式是项目配置，不影响视频请求。
_RETIRED_PARAMS: dict[str, str] = {
    "resume": "查询 durable batch，并用 selected scope、force=false 只重发未成功的 ID",
    "shot_ids": "改用 target.ids，并选择 scene（单个）或 selected（批量）scope",
    "unit_id": "改用 target.ids——参考生视频项目直接传 unit_id，并选择 scene scope",
    "unit_ids": "改用 target.ids——参考生视频项目直接传 unit_id 列表，并选择 selected scope",
    "shots": "视频单元不再有 shots 数组；正文写在剧本的 text 字段里，经 patch_episode_script 修改",
    "references": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "reference_images": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "narration_delivery": "视频生成不再按请求选择旁白交付方式；视频一律按剧本计划时长请求，旁白交付方式是项目配置",
}


def _reject_retired_params(args: Mapping[str, Any]) -> None:
    """入参里出现已退役的参数名时 fail loud，并指明当下该怎么写。

    Raises:
        ValueError: 命中 :data:`_RETIRED_PARAMS`。
    """
    for name, guidance in _RETIRED_PARAMS.items():
        if name in args:
            raise ValueError(f"参数 {name!r} 已不存在：{guidance}")


_PositiveInt = Annotated[StrictInt, Field(ge=1)]


class EpisodeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["episode"] = Field(description="整集：只补缺视频的单元，已有可用成片一律复用")
    episode_id: _PositiveInt = Field(description="集 ID，须与 script 所属那一集的集 ID 一致")


class AllTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["all"] = Field(description="剧本内全部单元：只补缺视频的单元，已有可用成片一律复用")


class SceneTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["scene"] = Field(description="单个单元")
    ids: list[str] = Field(
        min_length=1,
        max_length=1,
        description="恰好一个目标 ID：分镜图生视频为 segment_id / scene_id，参考生视频为 unit_id",
    )


class SelectedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["selected"] = Field(description="点名的一批单元")
    ids: RequestedIds = Field(description="目标 ID 列表：分镜图生视频为 segment_id / scene_id，参考生视频为 unit_id")


VideoTarget = Annotated[
    EpisodeTarget | AllTarget | SceneTarget | SelectedTarget,
    Field(discriminator="scope"),
]


class GenerateVideosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="剧本纯文件名（不含目录），如 episode_1.json")
    target: VideoTarget = Field(description="目标选择器；scope 取 episode / all / scene / selected")
    force: StrictBool = Field(
        default=False,
        description="是否强制重生已有可用成片；默认复用 current / stale 成片，只允许用于 scene / selected",
    )
    confirmed_request_duration_seconds: _PositiveInt | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "用户明确接受的本次视频请求秒数档位；仅参考生视频在返回跨档确认后填写。"
            "它不冻结正文、引用或供应商，当前投影改到其它档位时必须重新确认。"
        ),
    )
    confirmed_request_durations: dict[str, _PositiveInt] | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            '按 unit_id 记的档位确认（{"E1U1": 8}）；一次请求里多个 unit 档位不同时用它，'
            "让原目标集合仍作为一批重发——拆成几次调用会让先入队的那一档先花掉钱。"
            "与 confirmed_request_duration_seconds 同时给出时，本字段按 unit 覆盖。"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_retired_params(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            _reject_retired_params(data)
        return data

    @model_validator(mode="after")
    def _force_needs_explicit_ids(self) -> Self:
        if self.force and self.target.scope not in ("scene", "selected"):
            raise ValueError("force=true 只允许用于带显式 ID 的 scene/selected scope")
        return self


@dataclass(frozen=True, slots=True)
class _VideoCall:
    """一次视频工具调用：目标项目、调用方与协作者。"""

    scope: ProjectScope
    caller: CallerContext
    services: Services

    @property
    def project_name(self) -> str:
        return self.scope.project_name

    @property
    def projects(self) -> ProjectManager:
        return self.services.projects

    @property
    def project_path(self) -> Path:
        return self.services.projects.get_project_path(self.scope.project_name)

    def stop_on_failure_caller(self) -> CallerContext:
        """等待器在批次内任一任务失败即停止等待其余任务的调用方。"""
        return self.caller.waiting_with(stop_on_failure=True)


_OPERATION = "generate_videos"


def _batch_video_is_reusable(
    *,
    currency: ArtifactCurrencyResolver,
    episode: int,
    resource_id: str,
    artifact_path: object,
) -> bool:
    """Admit a batch skip from verified currency; uploads are registered like any other video."""

    return artifact_is_usable(
        currency,
        ArtifactKey.episode_video(episode, resource_id),
        artifact_path,
    )


def _state_for(states: dict[str, GenerationTargetState], unit_id: str) -> GenerationTargetState:
    return states.get(unit_id) or GenerationTargetState(candidate=GenerationCandidate(unit_id=unit_id))


def _missing_only_reusable_ids(states: dict[str, GenerationTargetState]) -> list[str]:
    """Missing-only 下原样保留的分镜：Manifest 认定 current / stale（含已登记的上传视频）。"""

    return [unit_id for unit_id, state in states.items() if artifact_is_reusable(state)]


def _sole_speech_admission(result: GenerationBatchResult) -> dict[str, Any]:
    """整个请求只卡在一个单元的发声准入上时，把准入载荷原样带回响应顶层。

    单元级的定位信息（哪一句台词、哪条路径）比逐 ID 契约细一层，点名单个单元的调用方
    需要它才能一步定位到要改的地方；批量请求没有「这一个」单元可指，就不带。
    """

    admissions = [
        item.problem.params["speech_admission"]
        for item in result.items
        if item.problem is not None and "speech_admission" in item.problem.params
    ]
    if len(result.requested) == 1 and len(admissions) == 1:
        return {"speech_admission": admissions[0]}
    return {}


def _reference_request_options(request: GenerateVideosRequest) -> ReferenceRequestOptions:
    """把工具入参折成一次参考生视频请求的投影选项。"""

    return ReferenceRequestOptions(confirmed_request_duration_seconds=request.confirmed_request_duration_seconds)


def _speech_admission_error(name: str, exc: SpeechAdmissionError, log: list[str] | None = None) -> ToolOutcome[Any]:
    payload = exc.admission.to_dict()
    text = f"{name} 失败: unit {exc.admission.unit_id} 发声准入未通过；请按 problems 的 action 修复"
    if log:
        text = "\n".join([text, *log])
    return ToolOutcome(problem=ToolProblem("speech_admission_refused", text, params={"speech_admission": payload}))


@dataclass(frozen=True)
class ReferenceGenerationComplete:
    """参考单元生成结果与入队前 current-state 投影。"""

    projections: list[dict[str, object]]
    batch: GenerationBatchReadModel | None = None


@dataclass(frozen=True)
class BatchAdmissionRefused:
    """整批准入未通过：本次调用不产生任何任务。"""

    admission: BatchAdmission


def _confirmation_lines(admission: BatchAdmission) -> list[str]:
    lines = ["以下 unit 将改用不同的视频时长档位，需先向用户确认，本次未入队任何任务："]
    for tier in admission.confirmation_tiers():
        # 档位解析不出来时该组没有可确认的秒数，照实说明；插值出 "None s 档位" 会让调用方
        # 以为存在一个叫 None 的档位。
        tier_label = (
            f"{tier.request_duration_seconds}s 档位" if tier.request_duration_seconds is not None else "档位待定"
        )
        headline = f"- {tier_label} × {tier.unit_count}：{'、'.join(tier.unit_ids)}"
        if tier.cost_amount is not None:
            headline += f"；合计 {tier.cost_amount} {tier.cost_currency}"
        lines.append(headline)
    for ticket in admission.tickets:
        if not ticket.confirmation_only:
            continue
        planned = ticket.problems[0].params.get("script_duration")
        requested = ticket.request_duration_seconds
        # 与剧本计划时长的差值直接给出：档位数字本身不说明成片会变长还是变短。
        delta = ""
        if isinstance(planned, int) and isinstance(requested, int) and requested != planned:
            direction = "更长" if requested > planned else "更短"
            delta = f"（成片{direction} {abs(requested - planned)}s）"
        requested_label = f"{requested}s" if isinstance(requested, int) else "档位待定"
        lines.append(f"  · {ticket.unit_id}：剧本时长 {planned}s，将申请 {requested_label}{delta}")
    lines.append(
        "视频费用按上述申请档位计算，确认仅对本次请求有效。用户同意后，带 "
        "confirmed_request_durations={<unit_id>: <request_duration>} 把原来这一批目标一次性重发；"
        "整批只有一个档位时也可以用 confirmed_request_duration_seconds=<request_duration>。"
        "不要按档位拆成多次调用——先入队的那一档已经花了钱，这批目标就不再是一次全有或全无的请求。"
    )
    return lines


def _blocked_lines(admission: BatchAdmission) -> list[str]:
    lines = ["本次批量请求未通过准入，未入队任何任务。逐 unit 缺口如下："]
    for ticket in admission.refused_tickets:
        lines.extend(
            f"- {ticket.unit_id}：{problem.code}（下一步：{problem.action.value}）{problem.detail}"
            for problem in ticket.problems
        )
    lines.append("修复全部缺口后重试即可一次性提交整批。")
    return lines


def _batch_admission_response(
    refusal: BatchAdmissionRefused,
    log: list[str],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState] | None = None,
) -> ToolOutcome[Any]:
    """转述整批拒绝：零任务入队，每个目标都带自己的机器可读结论。

    待确认档位是入队前的正常拦截点，不是异常——``is_error`` 不跟着 blocked 走，
    这批 unit 仍等待用户决定，不代表请求失败。
    """

    admission = refusal.admission
    admission.record_refusal(builder, states=states)
    confirmation_required = admission.decision is BatchAdmissionDecision.CONFIRMATION_REQUIRED
    lines = [*log, *(_confirmation_lines(admission) if confirmation_required else _blocked_lines(admission))]
    result = builder.build()
    payload: dict[str, Any] = {
        "generation_result": result,
        "batch_admission": admission.to_payload(),
        "request_projections": admission.projections(),
        **_sole_speech_admission(result),
        "summary": "\n".join(lines),
    }
    return ToolOutcome(value=payload)


async def _admit_storyboard_specs(
    *,
    call: _VideoCall,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    items: list[dict[str, Any]],
    id_field: str,
    specs: list[TaskSpec],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
) -> BatchAdmission:
    """Admit the Storyboard-mode specs.

    The admission is the shared one the read-only plan also consults, so a preview
    and the submission it predicts cannot reach different verdicts.
    """

    return await admit_storyboard_video_request(
        project_name=call.project_name,
        project=project,
        script_file=script_filename,
        items=items,
        id_field=id_field,
        specs=specs,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        user_id=call.caller.user_id,
        queue=call.services.queue,
        config_resolver=call.services.capabilities,
    )


def _resolve_reference_route(call: _VideoCall, script: dict[str, Any]) -> str | None:
    """定生成模式并把守骨架闸门。

    项目走参考生视频时返回 ``"reference"``，分镜图生视频返回 ``None``。
    生成模式以 project.json 的 ``generation_mode`` 为唯一真相源；所有创作类型共用
    同一份 ``video_units`` 骨架。

    Raises:
        SkeletonRouteMismatchError: 剧本骨架与项目生成模式失配，生成被拒。
    """
    project = call.projects.load_project(call.project_name)
    content_mode = resolve_content_mode(script, project)
    ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
    if not is_reference_video_project(project):
        return None
    return "reference"


def _build_reference_specs(
    *,
    units: list[Any],
    script_filename: str,
    skip_ids: list[str] | None,
) -> tuple[list[TaskSpec], list[UnitAdmissionTicket]]:
    """Build the reference-route specs, refusing each unit that cannot be requested."""

    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    refused: list[UnitAdmissionTicket] = []
    # 进到这里的 unit 已经过筛查 / 点名选取，unit_id 必为非空标量：不再为记名留兜底名字。
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit_id in skip_set:
            continue
        # 任一 unit 不合法（没有 shots、空提示词、或 from_request 对空 resource_id 抛的
        # 裸 ValueError）都记为受阻，不让一个坏 unit 中断整批。TaskSpecValidationError
        # 是 ValueError 子类，捕 ValueError 同时覆盖两者。
        try:
            spec = reference_unit_task_spec(unit, script_filename)
        except SpeechAdmissionError as exc:
            refused.append(speech_admission_ticket(unit_id, exc))
            continue
        except ValueError as exc:
            refused.append(
                refused_ticket(
                    unit_id,
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"入队校验未通过：{exc}",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        specs.append(spec)
    return specs, refused


def _scene_fallback_relpath(resource_id: str) -> str:
    return f"videos/scene_{resource_id}.mp4"


def _reference_fallback_relpath(resource_id: str) -> str:
    return f"reference_videos/{resource_id}.mp4"


async def _generate_reference_units(
    *,
    call: _VideoCall,
    units: list[Any],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
    resolver: ArtifactCurrencyResolver,
    build_specs: Callable[[list[Any], list[str]], tuple[list[TaskSpec], list[UnitAdmissionTicket]]],
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    reuse_existing: Callable[[dict[str, Any]], bool],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket] | None = None,
) -> ReferenceGenerationComplete | BatchAdmissionRefused:
    """unit 批量生成的共享骨架：时长确认 + 已产出扫描 + durable 批次提交。

    所有创作类型的 ``video_units`` 共用同一构造路径。``build_specs`` 是本批唯一的
    可入队性口径：它先于准入运行，构造不出 TaskSpec 的 unit 直接带着自己的问题码
    进入准入结论，不再被解析或报价。

    ``reuse_existing`` 决定磁盘上已存在的 ``{unit_id}.mp4`` 能否当作该 unit 的
    现行产物复用。调用方必须用持久化资产归属判定，不能只凭同名文件存在猜测；共享
    骨架还会先应用重规划闸门，迁移保留的旧产物不能让 ``needs_replan`` 单元绕过修复。

    准入是一次性的：全部目标由 :func:`admit_reference_video_batch` 一起评估，任一目标
    有问题就返回 :class:`BatchAdmissionRefused`，本次调用不产生任何任务（与 Web 批量入口
    共用同一份判定）。跨档 unit 的申请档位没有与 ``confirmed_request_duration_seconds``
    精确相等时同样属于未通过，用户同意后调用方带对应档位重新调用完成入队。

    """
    project_dir = call.project_path
    output_dir = project_dir / "reference_videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    already_done: list[str] = []
    manifest_blocked: list[str] = []
    refused: list[UnitAdmissionTicket] = list(extra_tickets or [])
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            continue
        candidate = output_dir / f"{unit_id}.mp4"
        reusable = False
        if candidate.exists() and not video_unit_replan_problems(unit):
            try:
                reusable = reuse_existing(unit)
            except ArtifactManifestError:
                # 复用判定（点名强制路线传入的 ``reuse_existing`` 恒为 False，不会走到
                # ``artifact_is_usable``；只有整集路线的复用判定会查 Manifest）对
                # BLOCKED 状态 fail-loud：一张已存在的成片若比对读不出来，不能让它把
                # 整批生成打成 tool_error，而是逐 unit 记为受阻，交回去修复侧车。
                refused.append(
                    refused_ticket(
                        unit_id,
                        code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
                        detail=f"unit {unit_id} 的产物状态不可读，跳过自动重生",
                        action=GenerationAction.REPAIR_ARTIFACT_STATE,
                    )
                )
                manifest_blocked.append(unit_id)
                continue
        if reusable:
            already_done.append(unit_id)
            state = _state_for(states, unit_id)
            builder.skip_unit(
                unit_id,
                artifact_key=state.artifact_key,
                artifact_path=state.artifact_path,
                artifact_status=state.status,
            )

    # 可入队性先判：能否构造 TaskSpec 是本批目标集合的边界，不可入队的 unit 带着自己的
    # 问题码进入准入，既不被解析、也不被静默丢弃。
    specs, spec_refused = build_specs(units, [*already_done, *manifest_blocked])
    buildable = {spec.resource_id for spec in specs}
    targets = [unit for unit in units if isinstance(unit, dict) and str(unit.get("unit_id") or "") in buildable]
    admission = await admit_reference_video_batch(
        project_name=call.project_name,
        project=project,
        project_path=project_dir,
        script=script,
        script_file=script_filename,
        units=targets,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=[*refused, *spec_refused],
        user_id=call.caller.user_id,
        queue=call.services.queue,
        config_resolver=call.services.capabilities,
    )
    if not admission.admitted:
        return BatchAdmissionRefused(admission)
    projections = admission.projections()

    for spec in specs:
        unit_options = request_options_for_unit(request_options, spec.resource_id, confirmed_request_durations)
        spec.payload = {**(spec.payload or {}), "reference_request_options": unit_options.to_payload()}
        spec.unit_id = spec.resource_id
        spec.source = call.caller.source

    submitted = await submit_media_generation(
        scope=call.scope,
        caller=call.stop_on_failure_caller(),
        services=call.services,
        operation=operation,
        preflight=builder.build(),
        pending_ids=[spec.resource_id for spec in specs],
        specs=specs,
        states=states,
        admission={str(item["unit_id"]): item for item in projections},
    )
    if submitted.successes is None or submitted.failures is None:
        return ReferenceGenerationComplete(projections=projections, batch=submitted.batch)
    record_batch_outcomes(
        builder,
        successes=submitted.successes,
        failures=submitted.failures,
        states=states,
        resolver=resolver,
        fallback_path=_reference_fallback_relpath,
    )
    return ReferenceGenerationComplete(projections=projections, batch=submitted.batch)


def _reference_episode(project: dict[str, Any], script: dict[str, Any], script_filename: str) -> int:
    """参考生视频的集号：绑定身份优先，取不到时回落到剧本 / 文件名推断。"""

    return resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )


async def _run_reference_batch(
    *,
    call: _VideoCall,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    episode: int,
    units: list[Any],
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
    reuse_existing: Callable[[ArtifactCurrencyResolver, dict[str, Any]], bool],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> ToolOutcome[Any]:
    """参考生视频的共享收尾：目标状态 → 批量生成 → 准入拒绝或结果响应。

    ``reuse_existing`` 收 currency 解析器与 unit，让整集路线的复用判定与点名路线的
    「一律不复用」共用同一条缝。
    """

    currency = active_artifact_currency_resolver(call.project_path, project)
    states = video_target_states(units, "unit_id", episode=episode, resolver=currency)
    builder = GenerationResultBuilder(operation, selection)
    result = await _generate_reference_units(
        call=call,
        units=units,
        builder=builder,
        states=states,
        resolver=currency,
        build_specs=lambda u, skip: _build_reference_specs(units=u, script_filename=script_filename, skip_ids=skip),
        project=project,
        script=script,
        script_filename=script_filename,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        reuse_existing=lambda unit: reuse_existing(currency, unit),
    )
    if isinstance(result, BatchAdmissionRefused):
        return _batch_admission_response(result, log, builder, states)
    if call.caller.source == "mcp" and result.batch is not None:
        return generation_batch_submission_outcome(result.batch)
    batch = builder.build()
    return generation_result_outcome(
        batch,
        log,
        request_projections=result.projections,
        batch_id=result.batch.batch_id if result.batch is not None else None,
        **_sole_speech_admission(batch),
    )


async def _run_reference_episode(
    *,
    call: _VideoCall,
    script: dict[str, Any],
    script_filename: str,
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> ToolOutcome[Any]:
    """Run reference_video-mode generation and format the tool response.

    All 4 video handlers fall through to whole-episode reference generation
    when ``_resolve_reference_route`` reports the episode branch; this captures
    the shared tail (resolve episode → generate units → header + log).
    """
    project = call.projects.load_project(call.project_name)
    episode = _reference_episode(project, script, script_filename)
    units = script.get("video_units")
    if "video_units" in script and not isinstance(units, list):
        # 生成模式闸门只问键在不在、不问值的类型，容器校验落在这里：不拦的话脏值（导入 / 外部编辑
        # 产生的 dict、字符串）会一路下传到 unit 迭代，报出无从定位的 TypeError。
        logger.debug("第 %d 集 video_units 类型非法: %s (%s)", episode, type(units).__name__, script_filename)
        raise ValueError(f"{describe_episode_for_agent(project, episode)} 的 video_units 必须是数组：{script_filename}")
    if not units:
        raise ValueError(f"{describe_episode_for_agent(project, episode)} 的 video_units 为空：{script_filename}")
    units, malformed = screen_script_entries(units, requested_ids=None)
    return await _run_reference_batch(
        call=call,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=units,
        selection=GenerationSelectionMode.MISSING_ONLY,
        extra_tickets=malformed,
        reuse_existing=lambda currency, unit: _batch_video_is_reusable(
            currency=currency,
            episode=episode,
            resource_id=str(unit.get("unit_id") or ""),
            artifact_path=get_generated_assets(unit).get("video_clip"),
        ),
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        log=log,
        operation=operation,
    )


def _select_reference_units(
    script: dict[str, Any], unit_ids: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """按 unit_id 从 ``video_units`` 点名取 unit。

    返回 ``(selected, unmatched_ids, duplicated_ids)``——不存在的 ID 由调用方作为 blocked
    逐项报告，不在此静默丢弃，也不因此中断其余 unit；点到的 ID 在剧本里有多份时无从判定
    要做哪一条，它不进目标集合、交调用方拒收整批，而不是默默拿第一份去入队计费。
    """
    indexed = script.get("video_units")
    by_id: dict[str, dict[str, Any]] = {}
    duplicated: set[str] = set()
    if isinstance(indexed, list):
        for unit in indexed:
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str) and unit["unit_id"]:
                if unit["unit_id"] in by_id:
                    duplicated.add(unit["unit_id"])
                    continue
                by_id[unit["unit_id"]] = unit

    selected: list[dict[str, Any]] = []
    unmatched: list[str] = []
    named = list(dict.fromkeys(unit_ids))
    for unit_id in named:
        if unit_id in duplicated:
            continue
        unit = by_id.get(unit_id)
        if unit is None:
            unmatched.append(unit_id)
            continue
        selected.append(unit)
    return selected, unmatched, [unit_id for unit_id in named if unit_id in duplicated]


async def _run_reference_units(
    *,
    call: _VideoCall,
    script_filename: str,
    unit_ids: list[str],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
    force: bool = True,
) -> ToolOutcome[Any]:
    """生成点名的参考生视频 unit；统一入口默认复用已有可用成片。"""
    project = call.projects.load_project(call.project_name)
    script = call.projects.load_script(call.project_name, script_filename)
    episode = _reference_episode(project, script, script_filename)

    selected, unmatched, duplicated = _select_reference_units(script, unit_ids)
    if selected:
        action = "重新生成（已有成片一律覆盖）" if force else "生成（已有可用成片复用）"
        log.append(f"{action} {len(selected)} 个 unit：{', '.join(u['unit_id'] for u in selected)}")

    unmatched_tickets = [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_NOT_FOUND,
            detail=f"unit {unit_id} 不在 video_units 中",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in unmatched
    ]
    unmatched_tickets += [
        refused_ticket(
            unit_id,
            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
            detail=f"unit {unit_id} 在剧本中重复出现",
            action=GenerationAction.FIX_INPUT,
        )
        for unit_id in duplicated
    ]

    return await _run_reference_batch(
        call=call,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=selected,
        selection=GenerationSelectionMode.EXPLICIT,
        extra_tickets=unmatched_tickets,
        reuse_existing=lambda currency, unit: (
            not force
            and _batch_video_is_reusable(
                currency=currency,
                episode=episode,
                resource_id=str(unit.get("unit_id") or ""),
                artifact_path=get_generated_assets(unit).get("video_clip"),
            )
        ),
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        log=log,
        operation=operation,
    )


@dataclass(frozen=True)
class _VideoRequestContext:
    """视频工具的请求前导：入参投影、剧本与生成模式判定。"""

    script_filename: str
    request_options: ReferenceRequestOptions
    confirmed_request_durations: dict[str, int]
    project_dir: Path
    script: dict[str, Any]
    reference_route: str | None


def _video_request_context(call: _VideoCall, request: GenerateVideosRequest) -> _VideoRequestContext:
    """把入参折成本次请求的投影选项、载入剧本，并定下走哪条生成模式。"""

    script = call.projects.load_script(call.project_name, request.script)
    return _VideoRequestContext(
        script_filename=request.script,
        request_options=_reference_request_options(request),
        confirmed_request_durations=dict(request.confirmed_request_durations or {}),
        project_dir=call.project_path,
        script=script,
        reference_route=_resolve_reference_route(call, script),
    )


@dataclass(frozen=True)
class _StoryboardScreening:
    """分镜图生视频的目标条目、ID 字段、骨架种类与筛查记名。"""

    items: list[dict[str, Any]]
    id_field: str
    skeleton_kind: str
    refused: list[UnitAdmissionTicket]


def _screen_script_targets(
    script: dict[str, Any],
    *,
    requested_ids: Collection[str] | None,
) -> _StoryboardScreening:
    """取分镜条目并筛掉成不了目标的脏条目。

    骨架种类取剧本实际形态（与生成模式闸门同一份判别），族内历史形态才不会被按创作类型
    反推的种类误判成解析失败。
    """

    items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
    skeleton_kind = resolve_script_kind(script)
    items, screen_refused = screen_storyboard_items(items, id_field, requested_ids=requested_ids)
    return _StoryboardScreening(
        items=items,
        id_field=id_field,
        skeleton_kind=skeleton_kind,
        refused=screen_refused,
    )


@dataclass(frozen=True)
class _StoryboardContext:
    """分镜图生视频的项目侧上下文：项目、集号与创作类型。"""

    project: dict[str, Any]
    episode: int
    content_mode: str


def _storyboard_context(call: _VideoCall, request: _VideoRequestContext) -> _StoryboardContext:
    project = call.projects.load_project(call.project_name)
    episode = resolve_artifact_episode(
        project=project,
        script=request.script,
        script_filename=request.script_filename,
    )
    return _StoryboardContext(
        project=project,
        episode=episode,
        content_mode=resolve_content_mode(request.script, project),
    )


@dataclass(frozen=True)
class _StoryboardBatch:
    """分镜图生视频一次请求的批次上下文：目标口径、准入身份与结果构造器。

    四种 scope 的目标集合与提交方式各不相同，但构造 TaskSpec、整批准入与逐目标记录
    结论这三步共用同一份口径——集中在这里，改一处判定不会只改到其中一个入口。
    """

    call: _VideoCall
    request: _VideoRequestContext
    sb: _StoryboardContext
    screening: _StoryboardScreening
    resolver: ArtifactCurrencyResolver
    operation: str
    selection: GenerationSelectionMode
    builder: GenerationResultBuilder
    states: dict[str, GenerationTargetState]
    log: list[str]

    def result(self) -> ToolOutcome[Any]:
        """把已记录的逐目标结论折成响应。"""

        return generation_result_outcome(self.builder.build(), self.log)

    async def build_specs(
        self,
        *,
        items: list[dict[str, Any]],
        skip_ids: list[str] | None,
    ) -> tuple[list[TaskSpec], list[UnitAdmissionTicket]]:
        """按本次请求的目标条目构造分镜图生视频的 TaskSpec。"""

        voice_characters = await resolve_voice_context(self.sb.project, self.sb.content_mode)
        return build_storyboard_video_specs(
            items=items,
            id_field=self.screening.id_field,
            content_mode=self.sb.content_mode,
            skeleton_kind=self.screening.skeleton_kind,
            script_filename=self.request.script_filename,
            project_dir=self.request.project_dir,
            project=self.sb.project,
            episode=self.sb.episode,
            resolver=self.resolver,
            skip_ids=skip_ids,
            voice_characters=voice_characters,
        )

    def _record(self, successes: list[BatchTaskResult], failures: list[BatchTaskResult]) -> None:
        record_batch_outcomes(
            self.builder,
            successes=successes,
            failures=failures,
            states=self.states,
            resolver=self.resolver,
            fallback_path=_scene_fallback_relpath,
        )

    async def admit_and_submit(
        self,
        *,
        items: list[dict[str, Any]],
        specs: list[TaskSpec],
        extra_tickets: list[UnitAdmissionTicket],
    ) -> ToolOutcome[Any]:
        """整批准入后提交；准入未通过则零任务入队地转述拒绝。"""

        admission = await _admit_storyboard_specs(
            call=self.call,
            project=self.sb.project,
            script=self.request.script,
            script_filename=self.request.script_filename,
            items=items,
            id_field=self.screening.id_field,
            specs=specs,
            operation=self.operation,
            selection=self.selection,
            extra_tickets=extra_tickets,
        )
        if not admission.admitted:
            return _batch_admission_response(BatchAdmissionRefused(admission), self.log, self.builder, self.states)

        for spec in specs:
            spec.unit_id = spec.resource_id
            spec.source = self.call.caller.source

        submitted = await submit_media_generation(
            scope=self.call.scope,
            caller=self.call.stop_on_failure_caller(),
            services=self.call.services,
            operation=self.operation,
            preflight=self.builder.build(),
            pending_ids=[spec.resource_id for spec in specs],
            specs=specs,
            states=self.states,
            admission={str(item["unit_id"]): item for item in admission.projections()},
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        self._record(submitted.successes, submitted.failures)
        return generation_result_outcome(self.builder.build(), self.log, batch_id=submitted.batch.batch_id)


def _check_target_episode(call: _VideoCall, request: _VideoRequestContext, episode: int) -> None:
    """整集选择器点名的集 ID 必须就是剧本所属的那一集：点错集不能按剧本那一集花钱。"""

    project = call.projects.load_project(call.project_name)
    actual_episode = resolve_artifact_episode(
        project=project,
        script=request.script,
        script_filename=request.script_filename,
    ) or ProjectManager.resolve_episode_from_script(request.script, request.script_filename)
    if episode != actual_episode:
        raise ValueError(f"target.episode_id={episode} 与剧本所属的集 ID {actual_episode} 不一致")


async def _generate_episode(call: _VideoCall, request: _VideoRequestContext, log: list[str]) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_episode(
            call=call,
            script=request.script,
            script_filename=script_filename,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
        )
    screening = _screen_script_targets(request.script, requested_ids=None)
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = _storyboard_context(call, request)
    episode = sb.episode
    if not items and not screen_refused:
        raise ValueError(f"{describe_episode_for_agent(sb.project, episode)} 的剧本为空：{script_filename}")

    currency = active_artifact_currency_resolver(project_dir, sb.project)
    states = video_target_states(items, id_field, episode=episode, resolver=currency)
    # 整集生成只补缺失，从不强制重生：仍可用的旧分镜（含 stale 与上传的视频）原样保留。
    already_done = _missing_only_reusable_ids(states)
    builder = GenerationResultBuilder(_OPERATION, GenerationSelectionMode.MISSING_ONLY)
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.MISSING_ONLY,
        builder=builder,
        states=states,
        log=log,
    )
    for done_id in already_done:
        state = _state_for(states, str(done_id))
        builder.skip(state)

    # currency 之外的第三态：Manifest 读不出该分镜的产物状态（BLOCKED），既不能
    # 判定为可复用（进 already_done）也不能安全当作缺失去入队——不可读不等于没有，
    # 花钱重生可能覆盖一份实际仍然可用的分镜。all scope 走
    # select_generation_targets 已经把这一态折进 selection.unavailable，这里是
    # 同一场判定手写的另一条腿，必须同步处理。
    already_done_set = set(already_done)
    blocked_states = [
        state
        for unit_id, state in states.items()
        if unit_id not in already_done_set and state.status == ArtifactStatus.BLOCKED
    ]
    blocked_ids = [state.unit_id for state in blocked_states]
    refused = artifact_state_tickets(blocked_states)
    refused.extend(screen_refused)

    specs, spec_refused = await batch.build_specs(
        items=items,
        skip_ids=[*already_done, *blocked_ids],
    )
    refused.extend(spec_refused)

    if not specs and not refused and not builder.recorded_ids:
        raise RuntimeError("没有可生成的分镜")

    return await batch.admit_and_submit(
        items=items,
        specs=specs,
        extra_tickets=refused,
    )


async def _generate_all(call: _VideoCall, request: _VideoRequestContext, log: list[str]) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_episode(
            call=call,
            script=request.script,
            script_filename=script_filename,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
        )
    screening = _screen_script_targets(request.script, requested_ids=None)
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = _storyboard_context(call, request)
    currency = active_artifact_currency_resolver(project_dir, sb.project)
    states = video_target_states(items, id_field, episode=sb.episode, resolver=currency)
    selection = select_generation_targets(
        candidates=[state.candidate for state in states.values()],
        requested_ids=None,
        resolver=currency,
    )
    # 产物状态不可读的目标由准入报告（折成准入票），结果契约里不重复记录：
    # 同一个 unit 记两次会让结果构造器 fail loud。
    unavailable_tickets = artifact_state_tickets(selection.unavailable)
    builder = GenerationResultBuilder.from_selection(_OPERATION, replace(selection, unavailable=()))
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.MISSING_ONLY,
        builder=builder,
        states=states,
        log=log,
    )
    if not selection.targets and not unavailable_tickets and not screen_refused:
        submitted = await submit_media_generation(
            scope=call.scope,
            caller=call.caller,
            services=call.services,
            operation=_OPERATION,
            preflight=builder.build(),
            pending_ids=[],
            specs=[],
            states=states,
        )
        if submitted.successes is None:
            return generation_batch_submission_outcome(submitted.batch)
        return generation_result_outcome(
            builder.build(),
            log,
            batch_id=submitted.batch.batch_id,
        )

    # 与 ``_video_target_states`` 用同一套 ID 回退规则：条目若缺 ``id_field``
    # 但带 ``scene_id``/``segment_id``，selection 已按回退 ID 记为 target，
    # 这里若只认 ``id_field`` 会把它筛没——进了 requested 却永远不入队。
    target_id_set = set(selection.target_ids)
    pending = [item for item in items if str(storyboard_item_id(item, id_field) or "") in target_id_set]
    specs, refused = await batch.build_specs(items=pending, skip_ids=None)
    # 产物状态不可读的分镜被选目标环节排除在 targets 之外，但它属于这次请求：
    # 不带进准入，同批健康的分镜会照常入队并计费，剩下这一个被无声略过。
    refused.extend(unavailable_tickets)
    refused.extend(screen_refused)
    if not specs and not refused:
        return batch.result()

    return await batch.admit_and_submit(
        items=pending,
        specs=specs,
        extra_tickets=refused,
    )


async def _generate_selected(
    call: _VideoCall,
    request: _VideoRequestContext,
    log: list[str],
    *,
    scene_ids: list[str],
    force: bool,
) -> ToolOutcome[Any]:
    script_filename = request.script_filename
    project_dir = request.project_dir

    if request.reference_route is not None:
        return await _run_reference_units(
            call=call,
            script_filename=script_filename,
            unit_ids=scene_ids,
            request_options=request.request_options,
            confirmed_request_durations=request.confirmed_request_durations,
            log=log,
            operation=_OPERATION,
            force=force,
        )

    screening = _screen_script_targets(request.script, requested_ids=set(scene_ids))
    items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
    sb = _storyboard_context(call, request)
    episode = sb.episode

    items_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        # 按同一份「能寻址到它的写法」建索引：直接拿原值当键，脏剧本里的 list / dict
        # 别名会抛 TypeError，逐目标的结论就塌成一句通用报错。
        for alias in storyboard_item_aliases(item, id_field):
            items_by_id[alias] = item

    builder = GenerationResultBuilder(_OPERATION, GenerationSelectionMode.EXPLICIT)
    selected: list[dict[str, Any]] = []
    refused: list[UnitAdmissionTicket] = []
    seen_canonical: set[str] = set()
    # ``items_by_id`` 同时按 ``id_field`` 与 ``scene_id`` 索引同一个 item，
    # 调用方若把两个值都列入 ``scene_ids`` 会让同一分镜重复入队——必须按
    # 规范 ``id_field`` 再去一次重。
    screened_ids = {ticket.unit_id for ticket in screen_refused}
    for sid in scene_ids:
        if sid in screened_ids:
            # 筛查已经按这个名字记过一条结论，重复记名会撞上结果契约的唯一性。
            continue
        if sid not in items_by_id:
            refused.append(
                refused_ticket(
                    sid,
                    code=GenerationProblemCode.UNIT_NOT_FOUND,
                    detail=f"分镜 '{sid}' 不存在",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        item = items_by_id[sid]
        canonical = str(item.get(id_field, ""))
        if canonical and canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        selected.append(item)
    if not selected and not refused and not screen_refused:
        return generation_result_outcome(builder.build(), log)

    currency = active_artifact_currency_resolver(project_dir, sb.project)
    already_done: list[str] = []
    states = video_target_states(selected, id_field, episode=episode, resolver=currency)
    if not force:
        already_done = list(dict.fromkeys(state.unit_id for state in states.values() if artifact_is_reusable(state)))
    for done_id in already_done:
        builder.skip(_state_for(states, str(done_id)))
    batch = _StoryboardBatch(
        call=call,
        request=request,
        sb=sb,
        screening=screening,
        resolver=currency,
        operation=_OPERATION,
        selection=GenerationSelectionMode.EXPLICIT,
        builder=builder,
        states=states,
        log=log,
    )

    specs, spec_refused = await batch.build_specs(items=selected, skip_ids=already_done)
    refused.extend(spec_refused)
    refused.extend(screen_refused)

    return await batch.admit_and_submit(
        items=selected,
        specs=specs,
        extra_tickets=refused,
    )


async def generate_videos(
    request: ToolRequest[GenerateVideosRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GenerationToolValue]:
    call = _VideoCall(scope=scope, caller=caller, services=services)
    args = request.value
    target = args.target
    log: list[str] = []
    try:
        context = _video_request_context(call, args)
        if isinstance(target, EpisodeTarget):
            _check_target_episode(call, context, target.episode_id)
            return await _generate_episode(call, context, log)
        if isinstance(target, AllTarget):
            return await _generate_all(call, context, log)
        # 去重以避免同一 ID 重复入队；保留首次出现顺序便于人读日志。
        scene_ids = normalize_requested_ids(target.ids, field="target.ids") or []
        return await _generate_selected(call, context, log, scene_ids=scene_ids, force=args.force)
    except SpeechAdmissionError as exc:
        return _speech_admission_error(_OPERATION, exc, log)
    except Exception as exc:
        return tool_error(_OPERATION, exc, log)


__all__ = [
    "AllTarget",
    "EpisodeTarget",
    "GenerateVideosRequest",
    "SceneTarget",
    "SelectedTarget",
    "generate_videos",
]
