"""Host-neutral tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.batch_admission import (
    BatchAdmission,
    BatchAdmissionDecision,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.generation_batch import GenerationBatchReadModel
from lib.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    batch_enqueue_and_wait,
)
from lib.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_is_reusable,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.project_manager import ProjectManager, is_reference_video_project
from lib.reference_video.request_projection import (
    POST_PRODUCTION,
    USE_TTS,
    ReferenceRequestOptions,
)
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.speech_composition import (
    SpeechAdmissionError,
    video_unit_replan_problems,
)
from lib.storyboard_sequence import get_storyboard_items
from lib.version_manager import VersionManager
from server.media_tools.context import (
    ToolContext,
    generation_batch_submission_response,
    generation_result_response,
    tool_error,
    tool_services,
    validate_script_filename,
)
from server.media_tools.definition import tool
from server.services.video_batch_admission import (
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
from server.tool_runtime import submit_media_generation

logger = logging.getLogger(__name__)

_CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "description": (
        "用户明确接受的本次视频请求秒数档位；仅在预检返回跨档费用提示后填写。"
        "它不冻结正文、引用、供应商或 TTS，当前投影改到其它档位时必须重新确认。"
    ),
}

_CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY = {
    "type": "object",
    "additionalProperties": {"type": "integer", "minimum": 1},
    "description": (
        '按 unit_id 记的档位确认（{"E1U1": 8}）；一次请求里多个 unit 档位不同时用它，'
        "让原目标集合仍作为一批重发。与 confirmed_request_duration_seconds 同时给出时，本字段按 unit 覆盖。"
    ),
}

_NARRATION_DELIVERY_SCHEMA_PROPERTY = {
    "type": "string",
    "enum": [POST_PRODUCTION, USE_TTS],
    "description": (
        "本次旁白交付方式，必填；use_tts 只使用当前 fresh TTS 的实际媒体时长，post_production 不因 TTS 缺失或过期受阻。"
    ),
}

_SCRIPT_SCHEMA_PROPERTY = {
    "type": "string",
    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
}

# 四个视频工具共用的请求尾部参数，按声明顺序展开进各自的 properties。
_REQUEST_SCHEMA_PROPERTIES = {
    "confirmed_request_duration_seconds": _CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY,
    "confirmed_request_durations": _CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY,
    "narration_delivery": _NARRATION_DELIVERY_SCHEMA_PROPERTY,
}


#: 已退役的入参名 → 该怎么写。键都是曾经真实存在、或与视频单元旧结构同名的写法：
#: 前三个是点名目标的旧 id 参数，后两个是视频单元已删除的 ``shots`` 与参考清单字段。
#: 视频单元现在只持有 ``text`` 与 ``duration_seconds``，参考图在执行期从正文的
#: ``@[名称]`` 首次提及顺序派生，两者都不再经工具入参传入。
_RETIRED_PARAMS: dict[str, str] = {
    "shot_ids": "改用点名参数 scene_id（单个）/ scene_ids（批量）",
    "unit_id": "改用点名参数 scene_id——参考生视频项目在该参数里直接传 unit_id",
    "unit_ids": "改用点名参数 scene_ids——参考生视频项目在该参数里直接传 unit_id 列表",
    "shots": "视频单元不再有 shots 数组；正文写在剧本的 text 字段里，经 patch_episode_script 修改",
    "references": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
    "reference_images": "视频单元不再有参考清单；参考图由正文的 @[名称] 提及在执行期派生",
}


def _reject_retired_params(args: dict[str, Any]) -> None:
    """入参里出现已退役的参数名时 fail loud，并指明当下该怎么写。

    Raises:
        ValueError: 命中 :data:`_RETIRED_PARAMS`。
    """
    for name, guidance in _RETIRED_PARAMS.items():
        if name in args:
            raise ValueError(f"参数 {name!r} 已不存在：{guidance}")


def _video_tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """工具入参 schema 的统一外壳。"""

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


_EPISODE_TOOL_SCHEMA = _video_tool_schema(
    {
        "script": _SCRIPT_SCHEMA_PROPERTY,
        "resume": {"type": "boolean", "description": "是否从上次中断处继续"},
        **_REQUEST_SCHEMA_PROPERTIES,
    },
    ["script", "narration_delivery"],
)

_SCENE_TOOL_SCHEMA = _video_tool_schema(
    {
        "script": _SCRIPT_SCHEMA_PROPERTY,
        "scene_id": {
            "type": "string",
            "description": "分镜 ID；reference_video 项目传视频单元的 unit_id（如 E1U2）",
        },
        **_REQUEST_SCHEMA_PROPERTIES,
    },
    ["script", "scene_id", "narration_delivery"],
)

_ALL_TOOL_SCHEMA = _video_tool_schema(
    {
        "script": _SCRIPT_SCHEMA_PROPERTY,
        **_REQUEST_SCHEMA_PROPERTIES,
    },
    ["script", "narration_delivery"],
)

_SELECTED_TOOL_SCHEMA = _video_tool_schema(
    {
        "script": _SCRIPT_SCHEMA_PROPERTY,
        "scene_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": '分镜 ID 列表；reference_video 项目传视频单元的 unit_id 列表（如 ["E1U2"]）',
        },
        "resume": {
            "type": "boolean",
            "description": "是否从上次中断处继续；reference_video 项目的点名重新生成会忽略此参数",
        },
        **_REQUEST_SCHEMA_PROPERTIES,
    },
    ["script", "scene_ids", "narration_delivery"],
)


_EPISODE_OPERATION = "generate_video_episode"
_SCENE_OPERATION = "generate_video_scene"
_ALL_OPERATION = "generate_video_all"
_SELECTED_OPERATION = "generate_video_selected"


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
    """Missing-only ids that active currency already reports current/stale.

    The checkpoint's ``completed_scenes`` only tracks what *this* batch (or a
    previous resumed attempt) has submitted — a fresh ``resume=false`` call
    always starts it empty. Without this, missing-only would regenerate every
    scene on a plain (non-resume) episode call even when its video_clip is
    already current, billing for a full re-run of a request that named no ID.
    """

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


def _reference_request_options(args: dict[str, Any]) -> ReferenceRequestOptions:
    """把工具入参折成一次请求的投影选项；交付方式缺省或非法一律拒绝，不入队任何任务。

    交付方式决定整批走哪一套准入判据与哪一份时长基准（TTS 实测 vs 剧本计划），
    替调用方挑一个默认值会让一批视频按它没声明过的交付方式准入并计费。
    storyboard 与 reference_video 两种生成模式都经这里取交付方式，判定只有这一处。
    """

    delivery = args.get("narration_delivery")
    if delivery not in (POST_PRODUCTION, USE_TTS):
        raise ValueError(f"narration_delivery 必填，合法值：{POST_PRODUCTION} | {USE_TTS}，收到 {delivery!r}")
    raw_confirmed = args.get("confirmed_request_duration_seconds")
    confirmed: int | None = None
    if raw_confirmed is not None:
        if not isinstance(raw_confirmed, int) or isinstance(raw_confirmed, bool) or raw_confirmed <= 0:
            raise ValueError(f"confirmed_request_duration_seconds 必须是大于 0 的整数秒档位，收到 {raw_confirmed!r}")
        confirmed = raw_confirmed
    return ReferenceRequestOptions(
        narration_delivery=delivery,
        confirmed_request_duration_seconds=confirmed,
    )


def _confirmed_request_durations(args: dict[str, Any]) -> dict[str, int]:
    """取按 unit 记的档位确认。

    整批只有一个档位时标量入参就够用；档位不止一个时必须按 unit 记，否则调用方只能
    拆成几次调用，而拆开之后先入队的那一档已经花掉了钱，原目标集合再也无法作为一批
    重新评估。
    """

    raw = args.get("confirmed_request_durations")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.debug("confirmed_request_durations 类型非法: %s", type(raw).__name__)
        raise ValueError("confirmed_request_durations 必须是 unit_id 到秒数档位的对象")
    confirmed: dict[str, int] = {}
    for unit_id, seconds in raw.items():
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError(f"confirmed_request_durations[{unit_id!r}] 必须是大于 0 的整数秒档位，收到 {seconds!r}")
        confirmed[str(unit_id)] = seconds
    return confirmed


def _speech_admission_error(name: str, exc: SpeechAdmissionError, log: list[str] | None = None) -> dict[str, Any]:
    payload = exc.admission.to_dict()
    text = f"{name} 失败: unit {exc.admission.unit_id} 发声准入未通过；请按 problems 的 action 修复"
    if log:
        text = "\n".join([text, *log])
    return {
        "content": [{"type": "text", "text": text}],
        "is_error": True,
        "speech_admission": payload,
    }


@dataclass(frozen=True)
class ReferenceGenerationComplete:
    """参考单元生成结果与入队前 current-state 投影。"""

    paths: list[Path]
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
        for problem in ticket.problems:
            lines.append(f"- {ticket.unit_id}：{problem.code}（下一步：{problem.action.value}）{problem.detail}")
    lines.append("修复全部缺口后重试即可一次性提交整批。")
    return lines


def _batch_admission_response(
    refusal: BatchAdmissionRefused,
    log: list[str],
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState] | None = None,
) -> dict[str, Any]:
    """转述整批拒绝：零任务入队，每个目标都带自己的机器可读结论。

    待确认档位是入队前的正常拦截点，不是异常——``is_error`` 不跟着 blocked 走，
    这批 unit 仍等待用户决定，不代表请求失败。
    """

    admission = refusal.admission
    admission.record_refusal(builder, states=states)
    confirmation = admission.decision is BatchAdmissionDecision.CONFIRMATION_REQUIRED
    lines = [*log, *(_confirmation_lines(admission) if confirmation else _blocked_lines(admission))]
    result = builder.build()
    response: dict[str, Any] = {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "generation_result": result.model_dump(mode="json"),
        "batch_admission": admission.to_payload(),
        "request_projections": admission.projections(),
        **_sole_speech_admission(result),
    }
    if not confirmation:
        response["is_error"] = True
    return response


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
    ctx: ToolContext,
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
        project_name=ctx.project_name,
        project=project,
        project_path=ctx.project_path,
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
    )
    if admission.admitted:
        _apply_delivery_payload(specs, request_options, confirmed_request_durations)
    return admission


def _resolve_reference_route(ctx: ToolContext, script: dict[str, Any]) -> str | None:
    """定生成模式并把守骨架闸门。

    项目走参考生视频时返回 ``"reference"``，分镜图生视频返回 ``None``。
    生成模式以 project.json 的 ``generation_mode`` 为唯一真相源；所有创作类型共用
    同一份 ``video_units`` 骨架。

    Raises:
        SkeletonRouteMismatchError: 剧本骨架与项目生成模式失配，生成被拒。
    """
    project = ctx.pm.load_project(ctx.project_name)
    content_mode = resolve_content_mode(script, project)
    ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
    if not is_reference_video_project(project):
        return None
    return "reference"


# Checkpoint helpers


def _episode_checkpoint_path(project_dir: Path, episode: int) -> Path:
    return project_dir / "videos" / f".checkpoint_ep{episode}.json"


def _selected_checkpoint_path(project_dir: Path, scenes_hash: str) -> Path:
    return project_dir / "videos" / f".checkpoint_selected_{scenes_hash}.json"


def _load_checkpoint_at(path: Path) -> dict[str, Any] | None:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_checkpoint_at(path: Path, completed: list[str], started_at: str, **extra: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "completed_scenes": completed,
        "started_at": started_at,
        "updated_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_checkpoint_at(path: Path) -> None:
    if path.exists():
        path.unlink()


def _storyboard_item_aliases(item: dict[str, Any], id_field: str) -> set[str]:
    """点名时能寻址到这个条目的全部写法。

    各入口既认规范 ``id_field`` 也认 ``scene_id`` 别名，两者在剧本里可以不同；按哪一个
    点名都要指到同一个条目，否则同一个名字在不同入口指向不同条目。
    """

    # 先按类型过滤再进集合：脏剧本里的 list / dict 别名不可哈希，直接建集合会抛 TypeError，
    # 逐目标的拒绝契约就塌成一句通用报错。
    aliases = (item.get(id_field), item.get("scene_id"))
    return {alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()}


def _screen_storyboard_items(
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
) -> tuple[list[TaskSpec], dict[str, int], list[UnitAdmissionTicket]]:
    """Build the reference-route specs, refusing each unit that cannot be requested."""

    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    refused: list[UnitAdmissionTicket] = []
    # 进到这里的 unit 已经过筛查 / 点名选取，unit_id 必为非空标量：不再为记名留兜底名字。
    for idx, unit in enumerate(units):
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
        order_map[unit_id] = idx
    return specs, order_map, refused


def _scan_completed_items(
    items: list[dict[str, Any]],
    id_field: str,
    completed_scenes: list[str],
    videos_dir: Path,
    *,
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> tuple[list[Path | None], list[str], list[str]]:
    """Reconcile checkpoint claims against canonical videos and active currency.

    Returns ``(ordered_paths, already_done, completed_filtered)``:
    - ``ordered_paths[i]`` is the existing mp4 path for items[i] iff the
      checkpoint claimed it and its exact formal path is usable under the
      Manifest (which also rejects absent files); else ``None``.
    - ``already_done`` is the subset of items the caller can skip enqueueing.
    - ``completed_filtered`` drops ids whose checkpoint output is missing or
      no longer admitted — caller should write this back instead of mutating
      its checkpoint list in place.

    A blocked Manifest comparison propagates so checkpoint resume fails loud;
    silently regenerating a paid artifact would discard the corruption signal.
    """
    ordered_paths: list[Path | None] = [None] * len(items)
    already_done: list[str] = []
    stale_completions: set[str] = set()
    for idx, item in enumerate(items):
        item_id = item.get(id_field, item.get("scene_id", f"item_{idx}"))
        if item_id not in completed_scenes:
            continue
        video_output = videos_dir / f"scene_{item_id}.mp4"
        artifact_path = video_output.relative_to(videos_dir.parent).as_posix()
        if artifact_is_usable(
            resolver,
            ArtifactKey.episode_video(episode, str(item_id)),
            artifact_path,
        ):
            ordered_paths[idx] = video_output
            already_done.append(item_id)
        else:
            stale_completions.add(item_id)
    completed_filtered = [cid for cid in completed_scenes if cid not in stale_completions]
    return ordered_paths, already_done, completed_filtered


def _scene_fallback_relpath(resource_id: str) -> str:
    return f"videos/scene_{resource_id}.mp4"


def _reference_fallback_relpath(resource_id: str) -> str:
    return f"reference_videos/{resource_id}.mp4"


async def _submit_with_checkpoint(
    *,
    project_name: str,
    project_dir: Path,
    specs: list[TaskSpec],
    order_map: dict[str, int],
    ordered_paths: list[Path | None],
    completed: list[str],
    fallback_relpath: Callable[[str], str],
    save_fn: Callable[[], None],
) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
    """Run a batch and update checkpoint per success. Returns ``(successes, failures)``.

    ``fallback_relpath`` is called only when the queue result lacks
    ``file_path``; reference_video tasks need a different naming convention
    than scene videos, so the caller chooses per task family.
    """

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or fallback_relpath(br.resource_id)
        ordered_paths[order_map[br.resource_id]] = project_dir / relative_path
        completed.append(br.resource_id)
        save_fn()

    return await batch_enqueue_and_wait(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        stop_on_failure=True,
    )


async def _generate_reference_units(
    *,
    ctx: ToolContext,
    units: list[Any],
    episode: int,
    resume: bool,
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
    resolver: ArtifactCurrencyResolver,
    checkpoint_path: Path | None,
    build_specs: Callable[[list[Any], list[str]], tuple[list[TaskSpec], dict[str, int], list[UnitAdmissionTicket]]],
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
    """unit 批量生成的共享骨架：时长确认 + checkpoint 续传 + 已产出扫描 + 入队等待。

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

    ``checkpoint_path`` 为 None 表示生成不落批次进度 checkpoint：点名重新生成一律强制覆盖，
    没有可续传的语义，写一份没有读者的进度文件只会在中断时留下垃圾，也会覆盖掉整集
    生成留下的进度。每个入队任务在 provider 提交边界使用的 execution checkpoint 是独立机制。
    """
    project_dir = ctx.project_path
    ckpt_path = checkpoint_path
    completed: list[str] = []
    started_at = datetime.now(UTC).isoformat()
    if resume and ckpt_path is not None:
        ckpt = _load_checkpoint_at(ckpt_path)
        if ckpt:
            completed = ckpt.get("completed_scenes", [])
            started_at = ckpt.get("started_at", started_at)

    output_dir = project_dir / "reference_videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    ordered_paths: list[Path | None] = [None] * len(units)
    already_done: list[str] = []
    manifest_blocked: list[str] = []
    refused: list[UnitAdmissionTicket] = list(extra_tickets or [])
    for idx, unit in enumerate(units):
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
            ordered_paths[idx] = candidate
            already_done.append(unit_id)
            state = _state_for(states, unit_id)
            builder.skip_unit(
                unit_id,
                artifact_key=state.artifact_key,
                artifact_path=state.artifact_path,
                artifact_status=state.status,
            )
            if unit_id not in completed:
                completed.append(unit_id)
        elif unit_id in completed:
            completed.remove(unit_id)

    # 可入队性先判：能否构造 TaskSpec 是本批目标集合的边界，不可入队的 unit 带着自己的
    # 问题码进入准入，既不被解析、也不被静默丢弃。
    specs, order_map, spec_refused = build_specs(units, [*already_done, *manifest_blocked])
    buildable = {spec.resource_id for spec in specs}
    targets = [unit for unit in units if isinstance(unit, dict) and str(unit.get("unit_id") or "") in buildable]
    admission = await admit_reference_video_batch(
        project_name=ctx.project_name,
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
    )
    if not admission.admitted:
        return BatchAdmissionRefused(admission)
    projections = admission.projections()

    for spec in specs:
        unit_options = request_options_for_unit(request_options, spec.resource_id, confirmed_request_durations)
        spec.payload = {**(spec.payload or {}), "reference_request_options": unit_options.to_payload()}
        spec.unit_id = spec.resource_id
        spec.source = ctx.caller.source
    submitted_batch: GenerationBatchReadModel | None = None
    if ctx.caller.source == "mcp" or ckpt_path is None:

        async def _wait_reference_batch(**kwargs: Any) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
            return await batch_enqueue_and_wait(stop_on_failure=True, **kwargs)

        submitted = await submit_media_generation(
            scope=ctx.scope,
            caller=ctx.caller,
            services=tool_services(ctx),
            operation=operation,
            preflight=builder.build(),
            pending_ids=[spec.resource_id for spec in specs],
            specs=specs,
            states=states,
            admission={str(item["unit_id"]): item for item in projections},
            embedded_waiter=_wait_reference_batch,
        )
        submitted_batch = submitted.batch
        if submitted.successes is None or submitted.failures is None:
            return ReferenceGenerationComplete(paths=[], projections=projections, batch=submitted.batch)
        for success in submitted.successes:
            result = success.result or {}
            relative_path = result.get("file_path") or _reference_fallback_relpath(success.resource_id)
            ordered_paths[order_map[success.resource_id]] = project_dir / relative_path
        record_batch_outcomes(
            builder,
            successes=submitted.successes,
            failures=submitted.failures,
            states=states,
            resolver=resolver,
            fallback_path=_reference_fallback_relpath,
        )
    elif specs:
        successes, failures = await _submit_with_checkpoint(
            project_name=ctx.project_name,
            project_dir=project_dir,
            specs=specs,
            order_map=order_map,
            ordered_paths=ordered_paths,
            completed=completed,
            fallback_relpath=_reference_fallback_relpath,
            save_fn=lambda: (
                None if ckpt_path is None else _save_checkpoint_at(ckpt_path, completed, started_at, episode=episode)
            ),
        )
        record_batch_outcomes(
            builder,
            successes=successes,
            failures=failures,
            states=states,
            resolver=resolver,
            fallback_path=_reference_fallback_relpath,
        )

    final = [p for p in ordered_paths if p is not None]
    # 全部成功才清理批次进度：有失败时保留 checkpoint 供 resume 续传。
    if ckpt_path is not None and not builder.has_failures:
        _clear_checkpoint_at(ckpt_path)
    return ReferenceGenerationComplete(paths=final, projections=projections, batch=submitted_batch)


def _reference_episode(project: dict[str, Any], script: dict[str, Any], script_filename: str) -> int:
    """参考生视频的集号：绑定身份优先，取不到时回落到剧本 / 文件名推断。"""

    episode = resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )
    if episode is None:
        episode = ProjectManager.resolve_episode_from_script(script, script_filename)
    return episode


async def _run_reference_batch(
    *,
    ctx: ToolContext,
    project: dict[str, Any],
    script: dict[str, Any],
    script_filename: str,
    episode: int,
    units: list[Any],
    resume: bool,
    checkpoint_path: Path | None,
    selection: GenerationSelectionMode,
    extra_tickets: list[UnitAdmissionTicket],
    reuse_existing: Callable[[ArtifactCurrencyResolver, dict[str, Any]], bool],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> dict[str, Any]:
    """参考生视频的共享收尾：目标状态 → 批量生成 → 准入拒绝或结果响应。

    ``reuse_existing`` 收 currency 解析器与 unit，让整集路线的复用判定与点名路线的
    「一律不复用」共用同一条缝。
    """

    currency = active_artifact_currency_resolver(ctx.project_path, project)
    states = video_target_states(units, "unit_id", episode=episode, resolver=currency)
    builder = GenerationResultBuilder(operation, selection)
    result = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        builder=builder,
        states=states,
        resolver=currency,
        checkpoint_path=checkpoint_path,
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
    if ctx.caller.source == "mcp" and result.batch is not None:
        return generation_batch_submission_response(result.batch)
    batch = builder.build()
    return generation_result_response(
        batch,
        log,
        request_projections=result.projections,
        batch_id=result.batch.batch_id if result.batch is not None else None,
        **_sole_speech_admission(batch),
    )


async def _run_reference_episode(
    *,
    ctx: ToolContext,
    script: dict[str, Any],
    script_filename: str,
    resume: bool,
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
) -> dict[str, Any]:
    """Run reference_video-mode generation and format the tool response.

    All 4 video handlers fall through to whole-episode reference generation
    when ``_resolve_reference_route`` reports the episode branch; this captures
    the shared tail (resolve episode → generate units → header + log).
    """
    project = ctx.pm.load_project(ctx.project_name)
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
    versions = VersionManager(ctx.project_path)
    return await _run_reference_batch(
        ctx=ctx,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=units,
        resume=resume,
        checkpoint_path=_episode_checkpoint_path(ctx.project_path, episode),
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
    ctx: ToolContext,
    script_filename: str,
    unit_ids: list[str],
    request_options: ReferenceRequestOptions,
    confirmed_request_durations: Mapping[str, int],
    log: list[str],
    operation: str,
    force: bool = True,
) -> dict[str, Any]:
    """生成点名的参考生视频 unit；统一入口默认复用已有可用成片。"""
    project = ctx.pm.load_project(ctx.project_name)
    script = ctx.pm.load_script(ctx.project_name, script_filename)
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

    versions = VersionManager(ctx.project_path)
    return await _run_reference_batch(
        ctx=ctx,
        project=project,
        script=script,
        script_filename=script_filename,
        episode=episode,
        units=selected,
        resume=False,
        checkpoint_path=None,
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
    """四个视频工具共有的请求前导：入参投影、剧本与生成模式判定。"""

    script_filename: str
    request_options: ReferenceRequestOptions
    confirmed_request_durations: dict[str, int]
    project_dir: Path
    script: dict[str, Any]
    reference_route: str | None


def _video_request_context(
    ctx: ToolContext,
    args: dict[str, Any],
    script_filename: str,
) -> _VideoRequestContext:
    """把入参折成本次请求的投影选项、载入剧本，并定下走哪条生成模式。

    剧本文件名由调用方先行校验后传入：``scene_ids`` 之类的入参校验要排在文件名之后、
    投影选项之前，入参报错的先后次序才与各入口一致。已退役参数名的拒绝落在这里，
    四个入口因此共用同一份判据与同一个报错次序。
    """

    _reject_retired_params(args)
    request_options = _reference_request_options(args)
    confirmed_request_durations = _confirmed_request_durations(args)
    project_dir = ctx.project_path
    script = ctx.pm.load_script(ctx.project_name, script_filename)
    return _VideoRequestContext(
        script_filename=script_filename,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        project_dir=project_dir,
        script=script,
        reference_route=_resolve_reference_route(ctx, script),
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
    items, screen_refused = _screen_storyboard_items(items, id_field, requested_ids=requested_ids)
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


def _storyboard_context(ctx: ToolContext, request: _VideoRequestContext) -> _StoryboardContext:
    project = ctx.pm.load_project(ctx.project_name)
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


def _checkpoint_progress(ckpt_path: Path, resume: bool) -> tuple[list[str], str]:
    """取批次进度：非续传从空表与当前时刻起步。"""

    completed: list[str] = []
    started_at = datetime.now(UTC).isoformat()
    if resume:
        ckpt = _load_checkpoint_at(ckpt_path)
        if ckpt:
            completed = ckpt.get("completed_scenes", [])
            started_at = ckpt.get("started_at", started_at)
    return completed, started_at


def _scan_checkpointed_items(
    *,
    items: list[dict[str, Any]],
    id_field: str,
    ckpt_path: Path,
    resume: bool,
    project_dir: Path,
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> tuple[list[Path | None], list[str], list[str], str]:
    """读批次进度并按当前产物核对，返回 ``(ordered_paths, already_done, completed, started_at)``。"""

    completed, started_at = _checkpoint_progress(ckpt_path, resume)
    videos_dir = project_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    ordered_paths, already_done, completed = _scan_completed_items(
        items,
        id_field,
        completed,
        videos_dir,
        episode=episode,
        resolver=resolver,
    )
    return ordered_paths, already_done, completed, started_at


@dataclass(frozen=True)
class _CheckpointSubmission:
    """带批次进度的提交参数：进度文件、顺序映射与写回动作。"""

    project_dir: Path
    ckpt_path: Path
    order_map: dict[str, int]
    ordered_paths: list[Path | None]
    completed: list[str]
    save_fn: Callable[[], None]


@dataclass(frozen=True)
class _StoryboardBatch:
    """分镜图生视频一次请求的批次上下文：目标口径、准入身份与结果构造器。

    四个视频工具的目标集合与提交方式各不相同，但构造 TaskSpec、整批准入与逐目标记录
    结论这三步共用同一份口径——集中在这里，改一处判定不会只改到其中一个入口。
    """

    ctx: ToolContext
    request: _VideoRequestContext
    sb: _StoryboardContext
    screening: _StoryboardScreening
    resolver: ArtifactCurrencyResolver
    operation: str
    selection: GenerationSelectionMode
    builder: GenerationResultBuilder
    states: dict[str, GenerationTargetState]
    log: list[str]

    def result(self) -> dict[str, Any]:
        """把已记录的逐目标结论折成响应。"""

        return generation_result_response(self.builder.build(), self.log)

    async def build_specs(
        self,
        *,
        items: list[dict[str, Any]],
        skip_ids: list[str] | None,
    ) -> tuple[list[TaskSpec], dict[str, int], list[UnitAdmissionTicket]]:
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
        checkpoint: _CheckpointSubmission | None,
    ) -> dict[str, Any]:
        """整批准入后提交；准入未通过则零任务入队地转述拒绝。

        ``checkpoint`` 为 None 表示本批不落批次进度，直接入队等待。
        """

        admission = await _admit_storyboard_specs(
            ctx=self.ctx,
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

        if checkpoint is None:
            for spec in specs:
                spec.unit_id = spec.resource_id
                spec.source = self.ctx.caller.source

            async def _wait_storyboard_batch(**kwargs: Any) -> tuple[list[BatchTaskResult], list[BatchTaskResult]]:
                return await batch_enqueue_and_wait(stop_on_failure=True, **kwargs)

            submitted = await submit_media_generation(
                scope=self.ctx.scope,
                caller=self.ctx.caller,
                services=tool_services(self.ctx),
                operation=self.operation,
                preflight=self.builder.build(),
                pending_ids=[spec.resource_id for spec in specs],
                specs=specs,
                states=self.states,
                admission={str(item["unit_id"]): item for item in admission.projections()},
                embedded_waiter=_wait_storyboard_batch,
            )
            if submitted.successes is None or submitted.failures is None:
                return generation_batch_submission_response(submitted.batch)
            self._record(submitted.successes, submitted.failures)
            return generation_result_response(self.builder.build(), self.log, batch_id=submitted.batch.batch_id)

        if specs:
            successes, failures = await _submit_with_checkpoint(
                project_name=self.ctx.project_name,
                project_dir=checkpoint.project_dir,
                specs=specs,
                order_map=checkpoint.order_map,
                ordered_paths=checkpoint.ordered_paths,
                completed=checkpoint.completed,
                fallback_relpath=_scene_fallback_relpath,
                save_fn=checkpoint.save_fn,
            )
            self._record(successes, failures)

        # checkpoint 只在整批无失败时清除，否则 resume=true 仍能接上断点。
        if not self.builder.has_failures:
            _clear_checkpoint_at(checkpoint.ckpt_path)
        return self.result()


def generate_video_episode_tool(ctx: ToolContext):
    @tool(
        "generate_video_episode",
        "为剧本对应的整集生成所有分镜视频。resume=true 时从 checkpoint 续传。参考生视频会自动按视频单元处理。",
        _EPISODE_TOOL_SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            resume = bool(args.get("resume"))
            request = _video_request_context(ctx, args, script_filename)
            project_dir = request.project_dir

            if request.reference_route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=request.script,
                    script_filename=script_filename,
                    resume=resume,
                    request_options=request.request_options,
                    confirmed_request_durations=request.confirmed_request_durations,
                    log=log,
                    operation=_EPISODE_OPERATION,
                )
            screening = _screen_script_targets(request.script, requested_ids=None)
            items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
            sb = _storyboard_context(ctx, request)
            episode = sb.episode
            if not items and not screen_refused:
                raise ValueError(f"第 {episode} 集剧本为空：{script_filename}")

            ckpt_path = _episode_checkpoint_path(project_dir, episode)
            currency = active_artifact_currency_resolver(project_dir, sb.project)
            ordered_paths, already_done, completed, started_at = _scan_checkpointed_items(
                items=items,
                id_field=id_field,
                ckpt_path=ckpt_path,
                resume=resume,
                project_dir=project_dir,
                episode=episode,
                resolver=currency,
            )
            states = video_target_states(items, id_field, episode=episode, resolver=currency)
            # 整集生成始终复用仍可用的旧分镜（含 stale），从不强制重生——所以
            # 无论是否 resume，选择模式如实报告为 missing-only；点名重做走
            # generate_video_scene / generate_selected_videos。checkpoint 的
            # completed_scenes 只认得本批次（或此前 resume）提交过的 ID，非
            # resume 调用永远从空表起步；不并入当前 currency 观测到的
            # current/stale，就会把整集当作缺失重新生成一遍。
            already_done = [
                *already_done,
                *_currency_reusable_ids(states, already_done),
            ]
            builder = GenerationResultBuilder(_EPISODE_OPERATION, GenerationSelectionMode.MISSING_ONLY)
            batch = _StoryboardBatch(
                ctx=ctx,
                request=request,
                sb=sb,
                screening=screening,
                resolver=currency,
                operation=_EPISODE_OPERATION,
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
            # 花钱重生可能覆盖一份实际仍然可用的分镜。generate_video_all 走
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

            specs, order_map, spec_refused = await batch.build_specs(
                items=items,
                skip_ids=[*already_done, *blocked_ids],
            )
            refused.extend(spec_refused)

            if not specs and not refused and not builder.recorded_ids and not any(ordered_paths):
                raise RuntimeError("没有可生成的分镜")

            return await batch.admit_and_submit(
                items=items,
                specs=specs,
                extra_tickets=refused,
                checkpoint=_CheckpointSubmission(
                    project_dir=project_dir,
                    ckpt_path=ckpt_path,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, episode=episode),
                ),
            )
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_EPISODE_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_EPISODE_OPERATION, exc, log)

    return _handler


def generate_video_scene_tool(ctx: ToolContext):
    @tool(
        "generate_video_scene",
        "生成单个分镜的视频。reference_video 项目把视频单元的 unit_id 填进 scene_id 即重新生成该视频单元（覆盖已有成片）。",
        _SCENE_TOOL_SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            scene_id = args["scene_id"]
            request = _video_request_context(ctx, args, script_filename)

            if request.reference_route is not None:
                return await _run_reference_units(
                    ctx=ctx,
                    script_filename=script_filename,
                    unit_ids=[scene_id],
                    request_options=request.request_options,
                    confirmed_request_durations=request.confirmed_request_durations,
                    log=log,
                    operation=_SCENE_OPERATION,
                )

            builder = GenerationResultBuilder(_SCENE_OPERATION, GenerationSelectionMode.EXPLICIT)
            screening = _screen_script_targets(request.script, requested_ids={scene_id})
            items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
            item = next((s for s in items if s.get(id_field) == scene_id or s.get("scene_id") == scene_id), None)
            if not item:
                # 筛查已经按这个名字拒过（剧本里它指向多个条目）时报筛查的结论：同一个 ID
                # 记两遍会撞上结果契约的唯一性，用户拿到一句通用报错。
                screened = next((ticket for ticket in screen_refused if ticket.unit_id == scene_id), None)
                builder.block(
                    scene_id,
                    problem=screened.problems[0]
                    if screened is not None and screened.problems
                    else GenerationProblem(
                        code=GenerationProblemCode.UNIT_NOT_FOUND,
                        detail=f"分镜 '{scene_id}' 不存在",
                        action=GenerationAction.FIX_INPUT,
                    ),
                )
                return generation_result_response(builder.build(), log)
            sb = _storyboard_context(ctx, request)
            currency = active_artifact_currency_resolver(request.project_dir, sb.project)
            states = video_target_states([item], id_field, episode=sb.episode, resolver=currency)
            batch = _StoryboardBatch(
                ctx=ctx,
                request=request,
                sb=sb,
                screening=screening,
                resolver=currency,
                operation=_SCENE_OPERATION,
                selection=GenerationSelectionMode.EXPLICIT,
                builder=builder,
                states=states,
                log=log,
            )

            # 发声准入与输入可用性都由 _build_video_specs 判定，点名单条与整批走同一条缝：
            # 两处各判一次，口径就会随其中一处的改动分叉。
            specs, _order_map, refused = await batch.build_specs(items=[item], skip_ids=None)
            refused.extend(screen_refused)
            if not specs and not refused:
                return batch.result()

            return await batch.admit_and_submit(
                items=[item],
                specs=specs,
                extra_tickets=refused,
                checkpoint=None,
            )
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_SCENE_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_SCENE_OPERATION, exc, log)

    return _handler


def generate_video_all_tool(ctx: ToolContext):
    @tool(
        "generate_video_all",
        "为剧本批量生成所有缺视频的分镜（独立模式，不拼接）。参考生视频等同 episode 模式。",
        _ALL_TOOL_SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        operation = str(args.get("_operation") or _ALL_OPERATION)
        try:
            script_filename = validate_script_filename(args["script"])
            request = _video_request_context(ctx, args, script_filename)
            project_dir = request.project_dir

            if request.reference_route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=request.script,
                    script_filename=script_filename,
                    resume=False,
                    request_options=request.request_options,
                    confirmed_request_durations=request.confirmed_request_durations,
                    log=log,
                    operation=operation,
                )
            screening = _screen_script_targets(request.script, requested_ids=None)
            items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
            sb = _storyboard_context(ctx, request)
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
            builder = GenerationResultBuilder.from_selection(operation, replace(selection, unavailable=()))
            batch = _StoryboardBatch(
                ctx=ctx,
                request=request,
                sb=sb,
                screening=screening,
                resolver=currency,
                operation=operation,
                selection=GenerationSelectionMode.MISSING_ONLY,
                builder=builder,
                states=states,
                log=log,
            )
            if not selection.targets and not unavailable_tickets and not screen_refused:
                if args.get("_durable"):
                    submitted = await submit_media_generation(
                        scope=ctx.scope,
                        caller=ctx.caller,
                        services=tool_services(ctx),
                        operation=operation,
                        preflight=builder.build(),
                        pending_ids=[],
                        specs=[],
                        states=states,
                        embedded_waiter=batch_enqueue_and_wait,
                    )
                    if submitted.successes is None:
                        return generation_batch_submission_response(submitted.batch)
                    return generation_result_response(
                        builder.build(),
                        log,
                        batch_id=submitted.batch.batch_id,
                    )
                return batch.result()

            # 与 ``_video_target_states`` 用同一套 ID 回退规则：条目若缺 ``id_field``
            # 但带 ``scene_id``/``segment_id``，selection 已按回退 ID 记为 target，
            # 这里若只认 ``id_field`` 会把它筛没——进了 requested 却永远不入队。
            target_id_set = set(selection.target_ids)
            pending = [item for item in items if str(storyboard_item_id(item, id_field) or "") in target_id_set]
            specs, _order_map, refused = await batch.build_specs(items=pending, skip_ids=None)
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
                checkpoint=None,
            )
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_ALL_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_ALL_OPERATION, exc, log)

    return _handler


def generate_video_selected_tool(ctx: ToolContext):
    @tool(
        "generate_video_selected",
        "生成指定多个分镜的视频。storyboard 项目用按 scene_ids 哈希的独立 checkpoint，支持 resume 续传。"
        "reference_video 项目传 unit_id 列表即对这些 unit 重新生成（覆盖已有成片），"
        "不落批次进度 checkpoint、忽略此处 resume 参数；已入队任务的 provider 提交恢复由队列独立处理。",
        _SELECTED_TOOL_SCHEMA,
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        operation = str(args.get("_operation") or _SELECTED_OPERATION)
        try:
            script_filename = validate_script_filename(args["script"])
            # 去重以避免同一 ID 重复入队；保留首次出现顺序便于人读日志，
            # checkpoint hash 再单独排序（见下方 ``canonical_scene_ids``）。
            scene_ids: list[str] = normalize_requested_ids(args["scene_ids"], field="scene_ids") or []
            resume = bool(args.get("resume"))
            request = _video_request_context(ctx, args, script_filename)
            project_dir = request.project_dir

            if request.reference_route is not None:
                if resume:
                    # 点名重新生成一律覆盖已有成片，没有可续传的中断态；照单收下再无视会让
                    # 调用方以为断点还在。
                    log.append("⚠️  点名重新生成不支持续传，resume 已忽略。")
                return await _run_reference_units(
                    ctx=ctx,
                    script_filename=script_filename,
                    unit_ids=scene_ids,
                    request_options=request.request_options,
                    confirmed_request_durations=request.confirmed_request_durations,
                    log=log,
                    operation=operation,
                    force=bool(args.get("_force", True)),
                )

            screening = _screen_script_targets(request.script, requested_ids=set(scene_ids))
            items, id_field, screen_refused = screening.items, screening.id_field, screening.refused
            sb = _storyboard_context(ctx, request)
            episode = sb.episode

            items_by_id: dict[str, dict[str, Any]] = {}
            for item in items:
                # 按同一份「能寻址到它的写法」建索引：直接拿原值当键，脏剧本里的 list / dict
                # 别名会抛 TypeError，逐目标的结论就塌成一句通用报错。
                for alias in _storyboard_item_aliases(item, id_field):
                    items_by_id[alias] = item

            builder = GenerationResultBuilder(operation, GenerationSelectionMode.EXPLICIT)
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
                return generation_result_response(builder.build(), log)

            # checkpoint hash 用 ``selected`` 解析出的规范 ID 集合，让同一批
            # 分镜无论用别名 ``scene_id`` 还是规范 ``id_field`` 调用都落到同一
            # checkpoint 文件（否则 resume 会因 hash 不同读到空 ``completed_scenes``，
            # 已生成的视频被 ``_scan_completed_items`` 漏判，重复入队）。
            canonical_scene_ids = sorted(seen_canonical)
            scenes_hash = hashlib.md5(",".join(canonical_scene_ids).encode("utf-8")).hexdigest()[:8]
            ckpt_path = _selected_checkpoint_path(project_dir, scenes_hash)
            currency = active_artifact_currency_resolver(project_dir, sb.project)
            ordered_paths, already_done, completed, started_at = _scan_checkpointed_items(
                items=selected,
                id_field=id_field,
                ckpt_path=ckpt_path,
                resume=resume,
                project_dir=project_dir,
                episode=episode,
                resolver=currency,
            )
            states = video_target_states(selected, id_field, episode=episode, resolver=currency)
            if args.get("_durable") and not args.get("_force"):
                already_done = list(
                    dict.fromkeys(
                        [*already_done, *(state.unit_id for state in states.values() if artifact_is_reusable(state))]
                    )
                )
            for done_id in already_done:
                builder.skip(_state_for(states, str(done_id)))
            batch = _StoryboardBatch(
                ctx=ctx,
                request=request,
                sb=sb,
                screening=screening,
                resolver=currency,
                operation=operation,
                selection=GenerationSelectionMode.EXPLICIT,
                builder=builder,
                states=states,
                log=log,
            )

            specs, order_map, spec_refused = await batch.build_specs(items=selected, skip_ids=already_done)
            refused.extend(spec_refused)
            refused.extend(screen_refused)

            checkpoint = None
            if not args.get("_durable"):
                checkpoint = _CheckpointSubmission(
                    project_dir=project_dir,
                    ckpt_path=ckpt_path,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, scene_ids=scene_ids),
                )
            return await batch.admit_and_submit(
                items=selected,
                specs=specs,
                extra_tickets=refused,
                checkpoint=checkpoint,
            )
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_SELECTED_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_SELECTED_OPERATION, exc, log)

    return _handler


async def handle_generate_videos(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        target = args.get("target")
        if not isinstance(target, dict):
            raise ValueError("target 必须是对象")
        scope = target.get("scope")
        if scope not in {"episode", "scene", "all", "selected"}:
            raise ValueError("target.scope 必须是 episode/scene/all/selected")
        forwarded = {key: value for key, value in args.items() if key not in {"target", "force"}}
        forwarded.update(_durable=True, _force=bool(args.get("force")), _operation="generate_videos")
        if scope in {"episode", "all"} and not args.get("force"):
            return await generate_video_all_tool(ctx).handler(forwarded)

        ids = normalize_requested_ids(target.get("ids"), field="target.ids")
        if scope == "scene" and (ids is None or len(ids) != 1):
            raise ValueError("target.scope=scene 时 target.ids 必须恰好包含一个 ID")
        if scope in {"selected", "scene"}:
            assert ids is not None
            forwarded["scene_ids"] = ids
            return await generate_video_selected_tool(ctx).handler(forwarded)

        script_filename = validate_script_filename(str(args["script"]))
        script = ctx.pm.load_script(ctx.project_name, script_filename)
        project = ctx.pm.load_project(ctx.project_name)
        if is_reference_video_project(project):
            raw_units = script.get("video_units")
            ids = [
                str(unit.get("unit_id")) for unit in raw_units or [] if isinstance(unit, dict) and unit.get("unit_id")
            ]
        else:
            items, id_field, *_rest = get_storyboard_items(script)
            ids = [str(item.get(id_field)) for item in items if item.get(id_field)]
        if not ids:
            raise ValueError("剧本没有可生成的视频目标")
        forwarded["scene_ids"] = ids
        return await generate_video_selected_tool(ctx).handler(forwarded)
    except Exception as exc:  # noqa: BLE001
        return tool_error("generate_videos", exc)


def generate_videos_tool(ctx: ToolContext):
    @tool(
        "generate_videos",
        "生成视频。target.scope 取 episode/scene/all/selected；scene/selected 在 target.ids 传目标 ID。"
        "force 默认 false，复用 current/stale 成片；true 才强制重生。narration_delivery 必填。"
        "整批预检任一目标不通过则零任务、无 batch handle；通过后返回 durable batch。",
        {
            "type": "object",
            "properties": {
                "script": _SCRIPT_SCHEMA_PROPERTY,
                "target": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["episode", "scene", "all", "selected"]},
                        "episode": {"type": "integer", "minimum": 1},
                        "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    },
                    "required": ["scope"],
                },
                "force": {"type": "boolean", "description": "是否强制重生已有可用成片，默认 false"},
                **_REQUEST_SCHEMA_PROPERTIES,
            },
            "required": ["script", "target", "narration_delivery"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return await handle_generate_videos(ctx, args)

    return _handler


__all__ = [
    "generate_videos_tool",
    "generate_video_episode_tool",
    "generate_video_scene_tool",
    "generate_video_all_tool",
    "generate_video_selected_tool",
]
