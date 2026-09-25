"""Host-neutral tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
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
from lib.artifacts.version_manager import VersionManager
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
from lib.script.reference_video.request_projection import (
    USE_TTS,
    NarrationDelivery,
    ReferenceRequestOptions,
)
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
    diagnostic_unit_id,
    reference_unit_task_spec,
    request_options_for_unit,
    resolve_voice_context,
    screen_script_entries,
    speech_admission_ticket,
    storyboard_item_id,
    video_target_states,
)
from server.tool_runtime import (
    BatchWaiter,
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
#: 前三个是点名目标的旧 id 参数，后两个是视频单元已删除的 ``shots`` 与参考清单字段。
#: 视频单元现在只持有 ``text`` 与 ``duration_seconds``，参考图在执行期从正文的
#: ``@[名称]`` 首次提及顺序派生，两者都不再经工具入参传入。
_RETIRED_PARAMS: dict[str, str] = {
    "resume": "查询 durable batch，并用 selected scope、force=false 只重发未成功的 ID",
    "shot_ids": "改用 target.ids，并选择 scene（单个）或 selected（批量）scope",
    "unit_id": "改用 target.ids——参考生视频项目直接传 unit_id，并选择 scene scope",
    "unit_ids": "改用 target.ids——参考生视频项目直接传 unit_id 列表，并选择 selected scope",
    "shots": "视频单元不再有 shots 数组；正文写在剧本的 text 字段里，经 patch_episode_script 修改",
    "references": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "reference_images": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
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
    episode: _PositiveInt = Field(description="集号，须与 script 的集号一致")


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
    narration_delivery: NarrationDelivery = Field(
        description=(
            "本次旁白交付方式，必填；use_tts 只使用当前 fresh TTS 的实际媒体时长，"
            "post_production 不因 TTS 缺失或过期受阻"
        )
    )
    confirmed_request_duration_seconds: _PositiveInt | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "用户明确接受的本次视频请求秒数档位；仅在预检返回跨档费用提示后填写。"
            "它不冻结正文、引用、供应商或 TTS，当前投影改到其它档位时必须重新确认。"
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

    def stop_on_failure_waiter(self) -> BatchWaiter | None:
        """内嵌等待器的包装：批次内一个任务失败即停止等待其余任务。"""

        waiter = self.caller.batch_waiter
        return None if waiter is None else functools.partial(waiter, stop_on_failure=True)


_OPERATION = "generate_videos"


def _batch_video_is_reusable(
    *,
    currency: ArtifactCurrencyResolver,
    versions: VersionManager,
    episode: int,
    resource_type: str,
    resource_id: str,
    artifact_path: object,
) -> bool:
    """Admit a batch skip from either verified currency or one exact raw upload."""

    return artifact_is_usable(
        currency,
        ArtifactKey.episode_video(episode, resource_id),
        artifact_path,
    ) or versions.selected_manual_upload_matches_current_file(
        resource_type,
        resource_id,
        artifact_path,
    )


def _state_for(states: dict[str, GenerationTargetState], unit_id: str) -> GenerationTargetState:
    return states.get(unit_id) or GenerationTargetState(candidate=GenerationCandidate(unit_id=unit_id))


def _currency_reusable_ids(
    states: dict[str, GenerationTargetState],
    already_done: list[str],
) -> list[str]:
    """Missing-only ids that active currency already reports current/stale."""

    done = set(already_done)
    return [unit_id for unit_id, state in states.items() if unit_id not in done and artifact_is_reusable(state)]


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
    """把工具入参折成一次请求的投影选项。

    交付方式决定整批走哪一套准入判据与哪一份时长基准（TTS 实测 vs 剧本计划），请求模型把它
    定为必填：替调用方挑一个默认值会让一批视频按它没声明过的交付方式准入并计费。
    storyboard 与 reference_video 两种生成模式都经这里取交付方式，判定只有这一处。
    """

    return ReferenceRequestOptions(
        narration_delivery=request.narration_delivery,
        confirmed_request_duration_seconds=request.confirmed_request_duration_seconds,
    )


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
        params = ticket.problems[0].params
        baseline = ticket.current_duration_seconds
        requested = ticket.request_duration_seconds
        tier_basis = (
            f"现有视觉档位 {baseline}s" if isinstance(baseline, int) else f"剧本档位 {params.get('script_duration')}s"
        )
        # 与现有成片的差值直接给出：档位数字本身不说明成片会变长还是变短。
        delta = ""
        if isinstance(baseline, int) and isinstance(requested, int) and requested != baseline:
            direction = "更长" if requested > baseline else "更短"
            delta = f"（成片{direction} {abs(requested - baseline)}s）"
        requested_label = f"{requested}s" if isinstance(requested, int) else "档位待定"
        lines.append(
            f"  · {ticket.unit_id}：{tier_basis}，将申请 {requested_label}{delta}，"
            f"时长基准 {params.get('duration_input')}s"
        )
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


def _apply_delivery_payload(
    specs: list[TaskSpec],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
) -> None:
    """Attach this request's delivery choice to every admitted storyboard spec.

    TTS 的取档结果是当前状态投影，不是耐久请求事实。worker 起跑时会从最新剧本 unit、
    fresh TTS 与当前模型能力重投影；即使 TaskSpec 的旧构造器放入 duration_seconds，
    这里也必须剥离。跨档确认按 unit 记入各自的请求事实——worker 重投影时读的是任务上的
    这份选项，只写整批共用的那一份会让准入已接受的档位在执行期重新变成待确认。
    """

    for spec in specs:
        unit_options = request_options_for_unit(request_options, spec.resource_id, confirmed_request_durations)
        spec.payload = {**(spec.payload or {}), "narration_delivery_options": unit_options.to_payload()}
        if request_options.narration_delivery == USE_TTS:
            spec.payload.pop("duration_seconds", None)


async def _admit_storyboard_specs(
    *,
    call: _VideoCall,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    items: list[dict[str, Any]],
    id_field: str,
    specs: list[TaskSpec],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    operation: str,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
) -> BatchAdmission:
    """Admit the Storyboard-mode specs, then stamp the delivery choice onto them.

    The admission itself is the shared one the read-only plan also consults, so a
    preview and the submission it predicts cannot reach different verdicts. Only the
    payload stamping is enqueue-side: it is a request fact, not part of the basis the
    admission compares.
    """

    admission = await admit_storyboard_video_request(
        project_name=call.project_name,
        project=project,
        project_path=call.project_path,
        script=script,
        script_file=script_filename,
        items=items,
        id_field=id_field,
        specs=specs,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
        user_id=call.caller.user_id,
        queue=call.services.queue,
        config_resolver=call.services.capabilities,
        tts_settings_resolver=call.services.tts_settings_resolver,
    )
    if admission.admitted:
        _apply_delivery_payload(specs, request_options, confirmed_request_durations)
    return admission


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


def _storyboard_item_aliases(item: dict[str, Any], id_field: str) -> set[str]:
    """点名时能寻址到这个条目的全部写法。

    各入口既认规范 ``id_field`` 也认 ``scene_id`` 别名，两者在剧本里可以不同；按哪一个
    点名都要指到同一个条目，否则同一个名字在不同入口指向不同条目。
    """

    # 先按类型过滤再进集合：脏剧本里的 list / dict 别名不可哈希，直接建集合会抛 TypeError，
    # 逐目标的拒绝契约就塌成一句通用报错。
    aliases = (item.get(id_field), item.get("scene_id"))
    return {alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()}


def screen_storyboard_items(
    items: Sequence[Any],
    id_field: str,
    *,
    requested_ids: Collection[str] | None,
) -> tuple[list[dict[str, Any]], list[UnitAdmissionTicket]]:
    """把剧本条目分成「能当目标的」与「成不了目标的」两份，后者按位置记名。

    非对象条目、id 不是字符串、id 为空、以及同一个 id 出现多次，都会让后面按 id 索引的每一步
    失手：条目被静默滤掉时同批健康的目标独自入队计费，撞上集合查询时又把逐目标的拒绝契约打成
    一句通用报错。数字与布尔 id 混过 ``str()`` 进队列后，执行期按原值比对同样找不到目标。
    各入口在读 id 之前先经这一道筛，这些失手都变成一张记名的准入票。

    缺失即生成把整个剧本当作目标集合，剧本里任何一处脏条目都参与判定；点名生成的目标集合由
    调用方给定，只有点到的 id 上的脏（同一个 id 的副本）才参与，否则别处的脏数据会否决一次
    精确点名的重做。

    记名用带方括号的位置写法，与合法 id 不共用命名空间：同名会让结果契约把两条不同的条目
    当作同一个，写第二遍时 fail loud，用户拿到的又是一句通用报错。
    """

    clean: list[dict[str, Any]] = []
    tickets: list[UnitAdmissionTicket] = []
    seen: set[str] = set()
    addressable: list[tuple[dict[str, Any], str]] = []
    taken = {
        str(storyboard_item_id(item, id_field)).strip()
        for item in items
        if isinstance(item, dict) and isinstance(storyboard_item_id(item, id_field), str)
    }
    named = set(requested_ids) if requested_ids is not None else None
    if named is not None:
        # 点名的 ID 也占着记名空间：点到剧本里没有的名字时上游还会记一条「不存在」，
        # 诊断名与它同名会把两条并成一条。
        taken |= named
    refused_names: set[str] = set()
    for index, item in enumerate(items):
        detail: str | None = None
        item_id = ""
        if not isinstance(item, dict):
            logger.debug("剧本条目 items[%d] 类型非法: %s", index, type(item).__name__)
            detail = "该条目不是对象"
        else:
            raw_id = storyboard_item_id(item, id_field)
            if raw_id is not None and not isinstance(raw_id, str):
                logger.debug("剧本条目 items[%d] 的 ID 类型非法: %s", index, type(raw_id).__name__)
                detail = "该条目的 ID 不是字符串"
            else:
                item_id = (raw_id or "").strip()
                if not item_id:
                    detail = "该条目没有可用的 ID"
        if detail is not None:
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"items[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=detail,
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            elif isinstance(item, dict):
                # 点名点中的正好是这个脏条目：按点名的写法给结论，否则调用方只收到一句
                # 「不存在」，而这个名字在剧本里明明有条目。
                for name in sorted(_storyboard_item_aliases(item, id_field) & named):
                    if name in refused_names:
                        continue
                    refused_names.add(name)
                    tickets.append(
                        refused_ticket(
                            name,
                            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                            detail=detail,
                            action=GenerationAction.FIX_INPUT,
                        )
                    )
            continue
        if named is not None:
            addressable.append((item, item_id))
            continue
        if item_id in seen:
            tickets.append(
                refused_ticket(
                    diagnostic_unit_id(f"{item_id}#{index}", taken),
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=f"ID {item_id} 在剧本中重复出现",
                    action=GenerationAction.FIX_INPUT,
                )
            )
            continue
        seen.add(item_id)
        clean.append(item)
    if named is None:
        return clean, tickets

    # 点名可以用规范 ID，也可以用 ``scene_id`` 别名，两者在剧本里可以不同；执行期按规范 ID
    # 定位目标。因此一个名字指到几个条目，要把「直接被它寻址的条目」连同「与之共用规范 ID
    # 的兄弟」一起数：只按名字数会漏掉别名不同、规范 ID 相同的那种，各入口按各自的查法分别
    # 选中头一个或末一个，同一次点名在不同入口做的是不同条目。
    by_canonical: dict[str, list[dict[str, Any]]] = {}
    for item, item_id in addressable:
        by_canonical.setdefault(item_id, []).append(item)
    ambiguous: set[int] = set()
    for name in sorted(named - refused_names):
        owners = {id(item) for item, _ in addressable if name in _storyboard_item_aliases(item, id_field)}
        targets = {
            id(sibling) for item, item_id in addressable if id(item) in owners for sibling in by_canonical[item_id]
        }
        if len(targets) <= 1:
            continue
        ambiguous |= targets
        tickets.append(
            refused_ticket(
                name,
                code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                detail=f"ID {name} 在剧本中指向多个条目",
                action=GenerationAction.FIX_INPUT,
            )
        )
    clean.extend(item for item, _ in addressable if id(item) not in ambiguous)
    return clean, tickets


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
        tts_settings_resolver=call.services.tts_settings_resolver,
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
        caller=call.caller,
        services=call.services,
        operation=operation,
        preflight=builder.build(),
        pending_ids=[spec.resource_id for spec in specs],
        specs=specs,
        states=states,
        admission={str(item["unit_id"]): item for item in projections},
        embedded_waiter=call.stop_on_failure_waiter(),
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
        raise ValueError(f"第 {episode} 集 video_units 必须是数组：{script_filename}")
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")
    units, malformed = screen_script_entries(units, requested_ids=None)
    versions = VersionManager(call.project_path)
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
            versions=versions,
            episode=episode,
            resource_type="reference_videos",
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

    versions = VersionManager(call.project_path)
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
                versions=versions,
                episode=episode,
                resource_type="reference_videos",
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
            request_options=self.request.request_options,
            confirmed_request_durations=self.request.confirmed_request_durations,
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
            caller=self.call.caller,
            services=self.call.services,
            operation=self.operation,
            preflight=self.builder.build(),
            pending_ids=[spec.resource_id for spec in specs],
            specs=specs,
            states=self.states,
            admission={str(item["unit_id"]): item for item in admission.projections()},
            embedded_waiter=self.call.stop_on_failure_waiter(),
        )
        if submitted.successes is None or submitted.failures is None:
            return generation_batch_submission_outcome(submitted.batch)
        self._record(submitted.successes, submitted.failures)
        return generation_result_outcome(self.builder.build(), self.log, batch_id=submitted.batch.batch_id)


def _check_target_episode(call: _VideoCall, request: _VideoRequestContext, episode: int) -> None:
    """整集选择器点名的集号必须就是剧本的集号：点错集不能按剧本那一集花钱。"""

    project = call.projects.load_project(call.project_name)
    actual_episode = resolve_artifact_episode(
        project=project,
        script=request.script,
        script_filename=request.script_filename,
    ) or ProjectManager.resolve_episode_from_script(request.script, request.script_filename)
    if episode != actual_episode:
        raise ValueError(f"target.episode={episode} 与剧本集号 {actual_episode} 不一致")


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
        raise ValueError(f"第 {episode} 集剧本为空：{script_filename}")

    currency = active_artifact_currency_resolver(project_dir, sb.project)
    states = video_target_states(items, id_field, episode=episode, resolver=currency)
    # 整集生成始终复用仍可用的旧分镜（含 stale），从不强制重生——所以
    already_done = _currency_reusable_ids(states, [])
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
    versions = VersionManager(project_dir)
    states = video_target_states(items, id_field, episode=sb.episode, resolver=currency)
    selection = select_generation_targets(
        candidates=[state.candidate for state in states.values()],
        requested_ids=None,
        resolver=currency,
        # 一次精确匹配的手动上传与 Manifest 认定的 current/stale 同样可复用，
        # 两条腿合起来才是「这个 ID 还缺不缺视频」。
        reusable_override=lambda candidate: versions.selected_manual_upload_matches_current_file(
            "videos",
            candidate.unit_id,
            candidate.artifact_path,
        ),
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
        for alias in _storyboard_item_aliases(item, id_field):
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
        versions = VersionManager(project_dir)
        already_done = list(
            dict.fromkeys(
                state.unit_id
                for state in states.values()
                if artifact_is_reusable(state)
                or versions.selected_manual_upload_matches_current_file(
                    "videos",
                    state.unit_id,
                    state.artifact_path,
                )
            )
        )
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
            _check_target_episode(call, context, target.episode)
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
    "screen_storyboard_items",
]
