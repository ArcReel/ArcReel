"""SDK MCP tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    artifact_is_usable,
    resolve_artifact_episode,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifact_manifest import ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.batch_admission import (
    BatchAdmission,
    BatchAdmissionDecision,
    UnitAdmissionTicket,
    refused_ticket,
)
from lib.config.resolver import video_bucket_for_generation_mode
from lib.generation_queue_client import (
    BatchEnqueueAborted,
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
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    is_structured_video_prompt,
    strip_voice_profiles,
    video_prompt_to_yaml,
)
from lib.reference_video.request_projection import (
    POST_PRODUCTION,
    USE_TTS,
    ReferenceRequestOptions,
)
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.speech_composition import (
    SpeechAdmissionError,
    require_script_unit_admitted,
    video_unit_replan_problems,
)
from lib.storyboard_sequence import StoryboardImageUnavailable, get_storyboard_items
from lib.version_manager import VersionManager
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    generation_result_response,
    tool_error,
    validate_script_filename,
)
from server.services.video_batch_admission import (
    admit_reference_video_batch,
    admit_storyboard_video_batch,
    artifact_state_tickets,
    diagnostic_unit_id,
    reference_unit_task_spec,
    request_options_for_unit,
    screen_script_entries,
    video_target_states,
)
from server.services.video_caps import assert_audio_switch_supported, resolve_project_is_silent

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
    "description": "本次旁白交付方式；use_tts 只使用当前 fresh TTS 的实际媒体时长。",
}


_EPISODE_OPERATION = "generate_video_episode"
_SCENE_OPERATION = "generate_video_scene"
_ALL_OPERATION = "generate_video_all"
_SELECTED_OPERATION = "generate_video_selected"


def _batch_video_is_reusable(
    *,
    currency: ArtifactCurrencyResolver | None,
    versions: VersionManager,
    episode: int,
    resource_type: str,
    resource_id: str,
    artifact_path: object,
) -> bool:
    """Admit a batch skip from either verified currency or one exact raw upload."""

    return artifact_is_usable(
        currency,
        ArtifactKey.episode_video(episode, resource_id) if currency is not None else None,
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
    *,
    manifest_active: bool,
) -> list[str]:
    """Missing-only ids that active currency already reports current/stale.

    The checkpoint's ``completed_scenes`` only tracks what *this* batch (or a
    previous resumed attempt) has submitted — a fresh ``resume=false`` call
    always starts it empty. Without this, missing-only would regenerate every
    scene on a plain (non-resume) episode call even when its video_clip is
    already current, billing for a full re-run of a request that named no ID.
    """

    done = set(already_done)
    return [
        unit_id
        for unit_id, state in states.items()
        if unit_id not in done and artifact_is_reusable(state, manifest_active=manifest_active)
    ]


def _speech_admission_ticket(unit_id: str, exc: SpeechAdmissionError) -> UnitAdmissionTicket:
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
    delivery = args.get("narration_delivery", POST_PRODUCTION)
    if delivery not in (POST_PRODUCTION, USE_TTS):
        delivery = POST_PRODUCTION
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
        raise ValueError(f"confirmed_request_durations 必须是 unit_id 到秒数档位的对象，收到 {type(raw).__name__}")
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
    """Admit the storyboard-route specs as one request, then stamp the delivery choice.

    The visual prompt each spec already carries is what the admission compares
    against the paid artifact, so the triples are built before the delivery payload
    is applied — the payload is a request fact, not part of the visual basis.

    音频开关冲突属于请求自身已知的配置缺口，在这里与投影侧的缺口折进同一批逐目标结论：
    留到提交前才检查，用户会先被问一遍跨档确认、同意之后才收到一句通用报错。
    """

    items_by_id = {
        str(item.get(id_field) or item.get("scene_id") or item.get("segment_id") or ""): item for item in items
    }
    targets: list[tuple[str, dict[str, Any], object]] = []
    for spec in specs:
        item = items_by_id.get(spec.resource_id)
        if item is None:
            raise ValueError(f"找不到待生成条目: {spec.resource_id}")
        targets.append((spec.resource_id, item, (spec.payload or {}).get("prompt")))
    audio_conflict = await _audio_switch_conflict(ctx) if specs else None
    admission = await admit_storyboard_video_batch(
        project_name=ctx.project_name,
        project=project,
        project_path=ctx.project_path,
        script=script,
        script_file=script_filename,
        items=targets,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=selection,
        extra_tickets=extra_tickets,
    )
    if audio_conflict is not None:
        # 音频开关冲突与投影侧的缺口写进同一批票：短路返回只会报出这一条，用户改完配置
        # 重试才撞见下一个已知缺口，正是这道门要免掉的逐条试探。
        conflict = GenerationProblem(
            code="video_audio_switch_not_supported",
            detail=audio_conflict,
            action=GenerationAction.CONFIGURE_PROVIDER,
            params={},
        )
        target_ids = {spec.resource_id for spec in specs}
        admission = replace(
            admission,
            tickets=tuple(
                replace(ticket, problems=(*ticket.problems, conflict)) if ticket.unit_id in target_ids else ticket
                for ticket in admission.tickets
            ),
        )
    if admission.admitted:
        _apply_delivery_payload(specs, request_options, confirmed_request_durations)
    return admission


def _enqueue_aborted_error(name: str, exc: BatchEnqueueAborted, log: list[str] | None = None) -> dict[str, Any]:
    """回滚后的整批入队失败：明确说明哪些任务已撤销、哪些仍占用队列。"""

    text = (
        f"{name} 失败: {exc}；本次已回滚 {len(exc.rolled_back)} 个任务，"
        f"未回滚 {len(exc.orphaned)} 个（{'、'.join(exc.orphaned) or '无'}）"
    )
    if log:
        text = "\n".join([text, *log])
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _get_video_prompt(
    item: dict[str, Any], *, content_mode: str, voice_characters: dict[str, Any] | None = None
) -> str:
    prompt = item.get("video_prompt")
    if not prompt:
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"片段/场景缺少 video_prompt 字段: {item_id}")
    if is_structured_video_prompt(prompt):
        # Voice_Profiles 声明段唯一来源是下方 build_drama_video_prompt 系的机械派生：剧本 JSON
        # 里残留的 voice_profiles 一律先剥离，不因门控不触发（narration/ad、或 drama 无
        # utterances 的条目）而绕过 C 类（真无声）门控直达 YAML。
        prompt = strip_voice_profiles(prompt)
        if content_mode == "drama":
            # drama 口型台词单一真相源在场景级有序 utterances：取 dialogue-kind 注入 video YAML 的
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
        raise ValueError(f"片段/场景 video_prompt 为对象但格式不符合结构化规范: {item_id}")
    if not isinstance(prompt, str):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise TypeError(f"片段/场景 video_prompt 类型无效（期望 str 或 dict）: {item_id}")
    return prompt


async def _audio_switch_conflict(ctx: ToolContext) -> str | None:
    """分镜路线入队前的音频闸门（``assert_audio_switch_supported``，与 WebUI 提交入口同一判据）。

    成片恒有声的模型收不到关闭音频的请求，放行只会让无声判据把音色约束整批裁掉。闸门与内容模式
    无关，narration/ad 同样受检。

    调用点固定在「确有任务要入队」之后：整集已完成、或条目全被 :func:`_build_video_specs`
    过滤时本就不会产生任何请求，此时拒绝等于把一次正常的空转变成报错。
    参考路线由公共 request projection 给出同一音频能力判定。

    返回冲突说明文本；无冲突返回 ``None``。调用方把它折成逐目标的准入结论，与其它缺口
    一起在建任务之前一次报全。
    """
    project = ctx.pm.load_project(ctx.project_name)
    try:
        await assert_audio_switch_supported(project, video_bucket_for_generation_mode(project.get("generation_mode")))
    except ValueError as exc:
        return str(exc)
    return None


async def _resolve_voice_context(ctx: ToolContext, content_mode: str) -> dict[str, Any] | None:
    """供 Voice_Profiles 注入的角色资产（``None`` 表示不注入）。

    非 drama 不注入；drama 按无声判据排除（C 类模型不产音、或本集关闭了音频，两条路径同口径）。
    台词不受影响、照常下发。
    """
    if content_mode != "drama":
        return None
    project = ctx.pm.load_project(ctx.project_name)
    if await resolve_project_is_silent(project):
        return None
    return project.get("characters") or {}


def _resolve_reference_route(ctx: ToolContext, script: dict[str, Any]) -> str | None:
    """定生成路线并把守骨架闸门。

    项目走参考生视频路线时返回 ``"reference"``，分镜路线返回 ``None``。
    路线以 project.json 的 ``generation_mode`` 为唯一真相源；所有内容模式共用
    同一份 ``video_units`` 骨架。

    Raises:
        SkeletonRouteMismatchError: 剧本骨架与项目路线失配，生成被拒。
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


def _storyboard_item_id(item: dict[str, Any], id_field: str) -> object:
    """条目自报的 id，按各入口共用的回退顺序取。"""

    return item.get(id_field) or item.get("scene_id") or item.get("segment_id")


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
    taken = {
        str(_storyboard_item_id(item, id_field)).strip()
        for item in items
        if isinstance(item, dict) and isinstance(_storyboard_item_id(item, id_field), str)
    }
    named = set(requested_ids) if requested_ids is not None else None
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"items[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=f"该条目不是对象，当前为 {type(item).__name__}",
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            continue
        raw_id = _storyboard_item_id(item, id_field)
        if raw_id is not None and not isinstance(raw_id, str):
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"items[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail=f"该条目的 ID 不是字符串，当前为 {type(raw_id).__name__}",
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            continue
        item_id = (raw_id or "").strip()
        if not item_id:
            if named is None:
                tickets.append(
                    refused_ticket(
                        diagnostic_unit_id(f"items[{index}]", taken),
                        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                        detail="该条目没有可用的 ID",
                        action=GenerationAction.FIX_INPUT,
                    )
                )
            continue
        if named is not None and item_id not in named and str(item.get(id_field) or "") not in named:
            clean.append(item)
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
    return clean, tickets


def _build_video_specs(
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
    """Build the storyboard-route specs, refusing each unit that cannot be requested.

    A unit whose speech, inputs or prompt are unusable is refused with its own code
    and next action instead of being dropped into a log line, so one bad unit never
    silently shrinks the batch — and, because the refusal is a ticket rather than a
    recorded block, it can hold the whole batch back before any task exists.

    ``skeleton_kind`` 取路线闸门给出的剧本实际骨架种类，而不是按内容模式反推：族内的历史
    形态（narration 数据落 ``scenes`` 键）按反推值去适配，合法的旧剧本会被整批判成解析失败。

    发声准入在这里执行，与参考路线由 ``reference_unit_task_spec`` 承担的位置对应：
    构造 TaskSpec 的这一步是「这个 unit 能不能被请求」的唯一口径，四个 storyboard 入口
    （整集 / 全部 / 点名 / 单条）都经由它，混合发声与 needs_replan 因此在任何入口都会
    在建任务之前扣住整批。
    """

    item_type = "片段" if content_mode == "narration" else "场景"
    skip_set = set(skip_ids or [])

    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    refused: list[UnitAdmissionTicket] = []
    project = project or {}
    if resolver is None:
        resolver = active_artifact_currency_resolver(project_dir, project)
    for idx, item in enumerate(items):
        item_id = str(_storyboard_item_id(item, id_field))
        if item_id in skip_set:
            continue

        try:
            require_script_unit_admitted(skeleton_kind, item)
        except SpeechAdmissionError as exc:
            # 逐 ID 契约与既有 speech_admission 载荷并存：前者说「这个 ID 没做成、
            # 下一步做什么」，后者带着准入自己的完整定位信息。
            refused.append(_speech_admission_ticket(str(item_id), exc))
            continue

        try:
            resolve_usable_storyboard_video_inputs(
                project_path=project_dir,
                project=project,
                episode=episode,
                resource_id=str(item_id),
                item=item,
                resolver=resolver,
                allow_legacy_same_name=False,
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
            prompt = _get_video_prompt(item, content_mode=content_mode, voice_characters=voice_characters)
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
            refused.append(_speech_admission_ticket(unit_id, exc))
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
    resolver: ArtifactCurrencyResolver | None,
) -> tuple[list[Path | None], list[str], list[str]]:
    """Reconcile checkpoint claims against canonical videos and active currency.

    Returns ``(ordered_paths, already_done, completed_filtered)``:
    - ``ordered_paths[i]`` is the existing mp4 path for items[i] iff the
      checkpoint claimed it and it is reusable: legacy projects require the
      file on disk, while active projects require its exact formal path to be
      usable under the Manifest (which also rejects absent files); else ``None``.
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
        reusable = (
            video_output.exists()
            if resolver is None
            else artifact_is_usable(
                resolver,
                ArtifactKey.episode_video(episode, str(item_id)),
                artifact_path,
            )
        )
        if reusable:
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
        atomic=True,
    )


async def _generate_reference_units(
    *,
    ctx: ToolContext,
    units: list[Any],
    episode: int,
    resume: bool,
    builder: GenerationResultBuilder,
    states: dict[str, GenerationTargetState],
    resolver: ArtifactCurrencyResolver | None,
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

    所有内容模式的 ``video_units`` 共用同一构造路径。``build_specs`` 是本批唯一的
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
    if specs:
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
    return ReferenceGenerationComplete(paths=final, projections=projections)


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
    episode = resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )
    if episode is None:
        episode = ProjectManager.resolve_episode_from_script(script, script_filename)
    units = script.get("video_units")
    if "video_units" in script and not isinstance(units, list):
        # 路线闸门只问键在不在、不问值的类型，容器校验落在这里：不拦的话脏值（导入 / 外部编辑
        # 产生的 dict、字符串）会一路下传到 unit 迭代，报出无从定位的 TypeError。
        raise ValueError(f"第 {episode} 集 video_units 必须是数组，当前为 {type(units).__name__}：{script_filename}")
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")
    units, malformed = screen_script_entries(units, requested_ids=None)
    currency = active_artifact_currency_resolver(ctx.project_path, project)
    versions = VersionManager(ctx.project_path)
    states = video_target_states(units, "unit_id", episode=episode, resolver=currency)
    builder = GenerationResultBuilder(operation, GenerationSelectionMode.MISSING_ONLY)
    result = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        builder=builder,
        states=states,
        resolver=currency,
        checkpoint_path=_episode_checkpoint_path(ctx.project_path, episode),
        build_specs=lambda u, skip: _build_reference_specs(units=u, script_filename=script_filename, skip_ids=skip),
        project=project,
        script=script,
        script_filename=script_filename,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=GenerationSelectionMode.MISSING_ONLY,
        extra_tickets=malformed,
        reuse_existing=lambda unit: _batch_video_is_reusable(
            currency=currency,
            versions=versions,
            episode=episode,
            resource_type="reference_videos",
            resource_id=str(unit.get("unit_id") or ""),
            artifact_path=get_generated_assets(unit).get("video_clip"),
        ),
    )
    if isinstance(result, BatchAdmissionRefused):
        return _batch_admission_response(result, log, builder, states)
    batch = builder.build()
    return generation_result_response(
        batch,
        log,
        request_projections=result.projections,
        **_sole_speech_admission(batch),
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
) -> dict[str, Any]:
    """对点名的参考生视频 unit 强制重新生成成片。"""
    project = ctx.pm.load_project(ctx.project_name)
    script = ctx.pm.load_script(ctx.project_name, script_filename)
    episode = resolve_artifact_episode(
        project=project,
        script=script,
        script_filename=script_filename,
    )
    if episode is None:
        episode = ProjectManager.resolve_episode_from_script(script, script_filename)

    selected, unmatched, duplicated = _select_reference_units(script, unit_ids)
    if selected:
        log.append(f"重新生成 {len(selected)} 个 unit（已有成片一律覆盖）：{', '.join(u['unit_id'] for u in selected)}")

    currency = active_artifact_currency_resolver(ctx.project_path, project)
    states = video_target_states(selected, "unit_id", episode=episode, resolver=currency)
    builder = GenerationResultBuilder(operation, GenerationSelectionMode.EXPLICIT)
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

    result = await _generate_reference_units(
        ctx=ctx,
        units=selected,
        episode=episode,
        resume=False,
        builder=builder,
        states=states,
        resolver=currency,
        checkpoint_path=None,
        build_specs=lambda u, skip: _build_reference_specs(units=u, script_filename=script_filename, skip_ids=skip),
        project=project,
        script=script,
        script_filename=script_filename,
        request_options=request_options,
        confirmed_request_durations=confirmed_request_durations,
        operation=operation,
        selection=GenerationSelectionMode.EXPLICIT,
        extra_tickets=unmatched_tickets,
        # 点名即强制：磁盘上的同名成片一律不复用。
        reuse_existing=lambda _u: False,
    )
    if isinstance(result, BatchAdmissionRefused):
        return _batch_admission_response(result, log, builder, states)
    batch = builder.build()
    return generation_result_response(
        batch,
        log,
        request_projections=result.projections,
        **_sole_speech_admission(batch),
    )


def generate_video_episode_tool(ctx: ToolContext):
    @tool(
        "generate_video_episode",
        "为剧本对应的整集生成所有场景视频。resume=true 时从 checkpoint 续传。"
        "reference_video 模式会自动按 video_units 处理。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "resume": {"type": "boolean", "description": "是否从上次中断处继续"},
                "confirmed_request_duration_seconds": _CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY,
                "confirmed_request_durations": _CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY,
                "narration_delivery": _NARRATION_DELIVERY_SCHEMA_PROPERTY,
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            resume = bool(args.get("resume"))
            request_options = _reference_request_options(args)
            confirmed_request_durations = _confirmed_request_durations(args)

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=resume,
                    request_options=request_options,
                    confirmed_request_durations=confirmed_request_durations,
                    log=log,
                    operation=_EPISODE_OPERATION,
                )
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            # 骨架种类取剧本实际形态（与路线闸门同一份判别），族内历史形态才不会被按内容模式
            # 反推的种类误判成解析失败。
            skeleton_kind = resolve_script_kind(script)
            items, screen_refused = _screen_storyboard_items(items, id_field, requested_ids=None)
            project = ctx.pm.load_project(ctx.project_name)
            episode = (
                resolve_artifact_episode(
                    project=project,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )
            content_mode = resolve_content_mode(script, project)
            if not items and not screen_refused:
                raise ValueError(f"第 {episode} 集剧本为空：{script_filename}")

            ckpt_path = _episode_checkpoint_path(project_dir, episode)
            completed: list[str] = []
            started_at = datetime.now(UTC).isoformat()
            if resume:
                ckpt = _load_checkpoint_at(ckpt_path)
                if ckpt:
                    completed = ckpt.get("completed_scenes", [])
                    started_at = ckpt.get("started_at", started_at)

            videos_dir = project_dir / "videos"
            videos_dir.mkdir(parents=True, exist_ok=True)
            currency = active_artifact_currency_resolver(project_dir, project)
            ordered_paths, already_done, completed = _scan_completed_items(
                items,
                id_field,
                completed,
                videos_dir,
                episode=episode,
                resolver=currency,
            )
            states = video_target_states(items, id_field, episode=episode, resolver=currency)
            # 整集生成始终复用仍可用的旧片段（含 stale），从不强制重生——所以
            # 无论是否 resume，选择模式如实报告为 missing-only；点名重做走
            # generate_video_scene / generate_selected_videos。checkpoint 的
            # completed_scenes 只认得本批次（或此前 resume）提交过的 ID，非
            # resume 调用永远从空表起步；不并入当前 currency 观测到的
            # current/stale，就会把整集当作缺失重新生成一遍。
            already_done = [
                *already_done,
                *_currency_reusable_ids(states, already_done, manifest_active=currency is not None),
            ]
            builder = GenerationResultBuilder(_EPISODE_OPERATION, GenerationSelectionMode.MISSING_ONLY)
            for done_id in already_done:
                state = _state_for(states, str(done_id))
                builder.skip(state)

            # currency 之外的第三态：Manifest 读不出该片段的产物状态（BLOCKED），既不能
            # 判定为可复用（进 already_done）也不能安全当作缺失去入队——不可读不等于没有，
            # 花钱重生可能覆盖一份实际仍然可用的片段。generate_video_all 走
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

            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, order_map, spec_refused = _build_video_specs(
                items=items,
                id_field=id_field,
                content_mode=content_mode,
                skeleton_kind=skeleton_kind,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
                episode=episode,
                resolver=currency,
                skip_ids=[*already_done, *blocked_ids],
                voice_characters=voice_characters,
            )
            refused.extend(spec_refused)

            if not specs and not refused and not builder.recorded_ids and not any(ordered_paths):
                raise RuntimeError("没有可生成的视频片段")

            admission = await _admit_storyboard_specs(
                ctx=ctx,
                project=project,
                script=script,
                script_filename=script_filename,
                items=items,
                id_field=id_field,
                specs=specs,
                request_options=request_options,
                confirmed_request_durations=confirmed_request_durations,
                operation=_EPISODE_OPERATION,
                selection=GenerationSelectionMode.MISSING_ONLY,
                extra_tickets=refused,
            )
            if not admission.admitted:
                return _batch_admission_response(BatchAdmissionRefused(admission), log, builder, states)

            if specs:
                successes, failures = await _submit_with_checkpoint(
                    project_name=ctx.project_name,
                    project_dir=project_dir,
                    specs=specs,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    fallback_relpath=_scene_fallback_relpath,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, episode=episode),
                )
                record_batch_outcomes(
                    builder,
                    successes=successes,
                    failures=failures,
                    states=states,
                    resolver=currency,
                    fallback_path=_scene_fallback_relpath,
                )

            # checkpoint 只在整批无失败时清除，否则 resume=true 仍能接上断点。
            if not builder.has_failures:
                _clear_checkpoint_at(ckpt_path)
            return generation_result_response(builder.build(), log)
        except BatchEnqueueAborted as exc:
            return _enqueue_aborted_error(_EPISODE_OPERATION, exc, log)
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_EPISODE_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_EPISODE_OPERATION, exc, log)

    return _handler


def generate_video_scene_tool(ctx: ToolContext):
    @tool(
        "generate_video_scene",
        "生成单个场景/片段的视频。reference_video 项目传 unit_id 即对该 unit 重新生成（覆盖已有成片）。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_id": {
                    "type": "string",
                    "description": "场景或片段 ID；reference_video 项目传 video_unit 的 unit_id（如 E1U2）",
                },
                "confirmed_request_duration_seconds": _CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY,
                "confirmed_request_durations": _CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY,
                "narration_delivery": _NARRATION_DELIVERY_SCHEMA_PROPERTY,
            },
            "required": ["script", "scene_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            scene_id = args["scene_id"]
            request_options = _reference_request_options(args)
            confirmed_request_durations = _confirmed_request_durations(args)

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_units(
                    ctx=ctx,
                    script_filename=script_filename,
                    unit_ids=[scene_id],
                    request_options=request_options,
                    confirmed_request_durations=confirmed_request_durations,
                    log=log,
                    operation=_SCENE_OPERATION,
                )

            builder = GenerationResultBuilder(_SCENE_OPERATION, GenerationSelectionMode.EXPLICIT)
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            # 骨架种类取剧本实际形态（与路线闸门同一份判别），族内历史形态才不会被按内容模式
            # 反推的种类误判成解析失败。
            skeleton_kind = resolve_script_kind(script)
            items, screen_refused = _screen_storyboard_items(items, id_field, requested_ids={scene_id})
            item = next((s for s in items if s.get(id_field) == scene_id or s.get("scene_id") == scene_id), None)
            if not item:
                builder.block(
                    scene_id,
                    problem=GenerationProblem(
                        code=GenerationProblemCode.UNIT_NOT_FOUND,
                        detail=f"场景/片段 '{scene_id}' 不存在",
                        action=GenerationAction.FIX_INPUT,
                    ),
                )
                return generation_result_response(builder.build(), log)
            project = ctx.pm.load_project(ctx.project_name)
            episode = (
                resolve_artifact_episode(
                    project=project,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )
            currency = active_artifact_currency_resolver(project_dir, project)
            states = video_target_states([item], id_field, episode=episode, resolver=currency)

            # 发声准入与输入可用性都由 _build_video_specs 判定，点名单条与整批走同一条缝：
            # 两处各判一次，口径就会随其中一处的改动分叉。
            content_mode = resolve_content_mode(script, project)
            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, _order_map, refused = _build_video_specs(
                items=[item],
                id_field=id_field,
                content_mode=content_mode,
                skeleton_kind=skeleton_kind,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
                episode=episode,
                resolver=currency,
                skip_ids=None,
                voice_characters=voice_characters,
            )
            refused.extend(screen_refused)
            if not specs and not refused:
                return generation_result_response(builder.build(), log)

            admission = await _admit_storyboard_specs(
                ctx=ctx,
                project=project,
                script=script,
                script_filename=script_filename,
                items=[item],
                id_field=id_field,
                specs=specs,
                request_options=request_options,
                confirmed_request_durations=confirmed_request_durations,
                operation=_SCENE_OPERATION,
                selection=GenerationSelectionMode.EXPLICIT,
                extra_tickets=refused,
            )
            if not admission.admitted:
                return _batch_admission_response(BatchAdmissionRefused(admission), log, builder, states)

            successes, failures = await batch_enqueue_and_wait(project_name=ctx.project_name, specs=specs, atomic=True)
            record_batch_outcomes(
                builder,
                successes=successes,
                failures=failures,
                states=states,
                resolver=currency,
                fallback_path=_scene_fallback_relpath,
            )
            return generation_result_response(builder.build(), log)
        except BatchEnqueueAborted as exc:
            return _enqueue_aborted_error(_SCENE_OPERATION, exc, log)
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_SCENE_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_SCENE_OPERATION, exc, log)

    return _handler


def generate_video_all_tool(ctx: ToolContext):
    @tool(
        "generate_video_all",
        "为剧本批量生成所有缺视频的场景/片段（独立模式，不拼接）。reference_video 模式等同 episode 模式。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "confirmed_request_duration_seconds": _CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY,
                "confirmed_request_durations": _CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY,
                "narration_delivery": _NARRATION_DELIVERY_SCHEMA_PROPERTY,
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            request_options = _reference_request_options(args)
            confirmed_request_durations = _confirmed_request_durations(args)
            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=False,
                    request_options=request_options,
                    confirmed_request_durations=confirmed_request_durations,
                    log=log,
                    operation=_ALL_OPERATION,
                )
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            # 骨架种类取剧本实际形态（与路线闸门同一份判别），族内历史形态才不会被按内容模式
            # 反推的种类误判成解析失败。
            skeleton_kind = resolve_script_kind(script)
            items, screen_refused = _screen_storyboard_items(items, id_field, requested_ids=None)
            project = ctx.pm.load_project(ctx.project_name)
            content_mode = resolve_content_mode(script, project)
            currency = active_artifact_currency_resolver(project_dir, project)
            versions = VersionManager(project_dir)
            episode = (
                resolve_artifact_episode(
                    project=project,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )
            states = video_target_states(items, id_field, episode=episode, resolver=currency)
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
            builder = GenerationResultBuilder.from_selection(_ALL_OPERATION, replace(selection, unavailable=()))
            if not selection.targets and not unavailable_tickets and not screen_refused:
                return generation_result_response(builder.build(), log)

            # 与 ``_video_target_states`` 用同一套 ID 回退规则：条目若缺 ``id_field``
            # 但带 ``scene_id``/``segment_id``，selection 已按回退 ID 记为 target，
            # 这里若只认 ``id_field`` 会把它筛没——进了 requested 却永远不入队。
            target_id_set = set(selection.target_ids)
            pending = [
                item
                for item in items
                if str(item.get(id_field) or item.get("scene_id") or item.get("segment_id") or "") in target_id_set
            ]
            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, _order_map, refused = _build_video_specs(
                items=pending,
                id_field=id_field,
                content_mode=content_mode,
                skeleton_kind=skeleton_kind,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
                episode=episode,
                resolver=currency,
                skip_ids=None,
                voice_characters=voice_characters,
            )
            # 产物状态不可读的场景被选目标环节排除在 targets 之外，但它属于这次请求：
            # 不带进准入，同批健康的场景会照常入队并计费，剩下这一个被无声略过。
            refused.extend(unavailable_tickets)
            refused.extend(screen_refused)
            if not specs and not refused:
                return generation_result_response(builder.build(), log)

            admission = await _admit_storyboard_specs(
                ctx=ctx,
                project=project,
                script=script,
                script_filename=script_filename,
                items=pending,
                id_field=id_field,
                specs=specs,
                request_options=request_options,
                confirmed_request_durations=confirmed_request_durations,
                operation=_ALL_OPERATION,
                selection=GenerationSelectionMode.MISSING_ONLY,
                extra_tickets=refused,
            )
            if not admission.admitted:
                return _batch_admission_response(BatchAdmissionRefused(admission), log, builder, states)

            successes, failures = await batch_enqueue_and_wait(project_name=ctx.project_name, specs=specs, atomic=True)
            record_batch_outcomes(
                builder,
                successes=successes,
                failures=failures,
                states=states,
                resolver=currency,
                fallback_path=_scene_fallback_relpath,
            )
            return generation_result_response(builder.build(), log)
        except BatchEnqueueAborted as exc:
            return _enqueue_aborted_error(_ALL_OPERATION, exc, log)
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_ALL_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_ALL_OPERATION, exc, log)

    return _handler


def generate_video_selected_tool(ctx: ToolContext):
    @tool(
        "generate_video_selected",
        "生成指定多个场景的视频。storyboard 项目用按 scene_ids 哈希的独立 checkpoint，支持 resume 续传。"
        "reference_video 项目传 unit_id 列表即对这些 unit 重新生成（覆盖已有成片），"
        "不落批次进度 checkpoint、忽略此处 resume 参数；已入队任务的 provider 提交恢复由队列独立处理。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": '场景或片段 ID 列表；reference_video 项目传 video_unit 的 unit_id 列表（如 ["E1U2"]）',
                },
                "resume": {
                    "type": "boolean",
                    "description": "是否从上次中断处继续；reference_video 项目的点名重新生成会忽略此参数",
                },
                "confirmed_request_duration_seconds": _CONFIRMED_REQUEST_DURATION_SCHEMA_PROPERTY,
                "confirmed_request_durations": _CONFIRMED_REQUEST_DURATIONS_SCHEMA_PROPERTY,
                "narration_delivery": _NARRATION_DELIVERY_SCHEMA_PROPERTY,
            },
            "required": ["script", "scene_ids"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            # 去重以避免同一 ID 重复入队；保留首次出现顺序便于人读日志，
            # checkpoint hash 再单独排序（见下方 ``canonical_scene_ids``）。
            scene_ids: list[str] = normalize_requested_ids(args["scene_ids"], field="scene_ids") or []
            resume = bool(args.get("resume"))
            request_options = _reference_request_options(args)
            confirmed_request_durations = _confirmed_request_durations(args)

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                if resume:
                    # 点名重新生成一律覆盖已有成片，没有可续传的中断态；照单收下再无视会让
                    # 调用方以为断点还在。
                    log.append("⚠️  点名重新生成不支持续传，resume 已忽略。")
                return await _run_reference_units(
                    ctx=ctx,
                    script_filename=script_filename,
                    unit_ids=scene_ids,
                    request_options=request_options,
                    confirmed_request_durations=confirmed_request_durations,
                    log=log,
                    operation=_SELECTED_OPERATION,
                )

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            # 骨架种类取剧本实际形态（与路线闸门同一份判别），族内历史形态才不会被按内容模式
            # 反推的种类误判成解析失败。
            skeleton_kind = resolve_script_kind(script)
            items, screen_refused = _screen_storyboard_items(items, id_field, requested_ids=set(scene_ids))
            project = ctx.pm.load_project(ctx.project_name)
            content_mode = resolve_content_mode(script, project)
            episode = (
                resolve_artifact_episode(
                    project=project,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )

            items_by_id: dict[str, dict[str, Any]] = {}
            for item in items:
                items_by_id[item.get(id_field, "")] = item
                if "scene_id" in item:
                    items_by_id[item["scene_id"]] = item

            builder = GenerationResultBuilder(_SELECTED_OPERATION, GenerationSelectionMode.EXPLICIT)
            selected: list[dict[str, Any]] = []
            refused: list[UnitAdmissionTicket] = []
            seen_canonical: set[str] = set()
            # ``items_by_id`` 同时按 ``id_field`` 与 ``scene_id`` 索引同一个 item，
            # 调用方若把两个值都列入 ``scene_ids`` 会让同一场景重复入队——必须按
            # 规范 ``id_field`` 再去一次重。
            for sid in scene_ids:
                if sid not in items_by_id:
                    refused.append(
                        refused_ticket(
                            sid,
                            code=GenerationProblemCode.UNIT_NOT_FOUND,
                            detail=f"场景/片段 '{sid}' 不存在",
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
            if not selected and not refused:
                return generation_result_response(builder.build(), log)

            # checkpoint hash 用 ``selected`` 解析出的规范 ID 集合，让同一批
            # 场景无论用别名 ``scene_id`` 还是规范 ``id_field`` 调用都落到同一
            # checkpoint 文件（否则 resume 会因 hash 不同读到空 ``completed_scenes``，
            # 已生成的视频被 ``_scan_completed_items`` 漏判，重复入队）。
            canonical_scene_ids = sorted(seen_canonical)
            scenes_hash = hashlib.md5(",".join(canonical_scene_ids).encode("utf-8")).hexdigest()[:8]
            ckpt_path = _selected_checkpoint_path(project_dir, scenes_hash)
            completed: list[str] = []
            started_at = datetime.now(UTC).isoformat()
            if resume:
                ckpt = _load_checkpoint_at(ckpt_path)
                if ckpt:
                    completed = ckpt.get("completed_scenes", [])
                    started_at = ckpt.get("started_at", started_at)

            videos_dir = project_dir / "videos"
            videos_dir.mkdir(parents=True, exist_ok=True)
            currency = active_artifact_currency_resolver(project_dir, project)
            ordered_paths, already_done, completed = _scan_completed_items(
                selected,
                id_field,
                completed,
                videos_dir,
                episode=episode,
                resolver=currency,
            )
            states = video_target_states(selected, id_field, episode=episode, resolver=currency)
            for done_id in already_done:
                builder.skip(_state_for(states, str(done_id)))

            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, order_map, spec_refused = _build_video_specs(
                items=selected,
                id_field=id_field,
                content_mode=content_mode,
                skeleton_kind=skeleton_kind,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
                episode=episode,
                resolver=currency,
                skip_ids=already_done,
                voice_characters=voice_characters,
            )
            refused.extend(spec_refused)
            refused.extend(screen_refused)

            admission = await _admit_storyboard_specs(
                ctx=ctx,
                project=project,
                script=script,
                script_filename=script_filename,
                items=selected,
                id_field=id_field,
                specs=specs,
                request_options=request_options,
                confirmed_request_durations=confirmed_request_durations,
                operation=_SELECTED_OPERATION,
                selection=GenerationSelectionMode.EXPLICIT,
                extra_tickets=refused,
            )
            if not admission.admitted:
                return _batch_admission_response(BatchAdmissionRefused(admission), log, builder, states)

            if specs:
                successes, failures = await _submit_with_checkpoint(
                    project_name=ctx.project_name,
                    project_dir=project_dir,
                    specs=specs,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    fallback_relpath=_scene_fallback_relpath,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, scene_ids=scene_ids),
                )
                record_batch_outcomes(
                    builder,
                    successes=successes,
                    failures=failures,
                    states=states,
                    resolver=currency,
                    fallback_path=_scene_fallback_relpath,
                )

            # checkpoint 只在整批无失败时清除，否则 resume=true 仍能接上断点。
            if not builder.has_failures:
                _clear_checkpoint_at(ckpt_path)
            return generation_result_response(builder.build(), log)
        except BatchEnqueueAborted as exc:
            return _enqueue_aborted_error(_SELECTED_OPERATION, exc, log)
        except SpeechAdmissionError as exc:
            return _speech_admission_error(_SELECTED_OPERATION, exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_SELECTED_OPERATION, exc, log)

    return _handler


__all__ = [
    "generate_video_episode_tool",
    "generate_video_scene_tool",
    "generate_video_all_tool",
    "generate_video_selected_tool",
]
