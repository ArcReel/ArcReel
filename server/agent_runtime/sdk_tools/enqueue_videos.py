"""SDK MCP tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.config.resolver import VideoCapability, video_bucket_for_generation_mode
from lib.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    batch_enqueue_and_wait,
    enqueue_and_wait,
    get_active_tasks_for_resources,
)
from lib.project_manager import ProjectManager, is_reference_video_project
from lib.prompt_utils import (
    build_drama_video_prompt,
    build_drama_video_prompt_from_legacy_dialogue,
    is_structured_video_prompt,
    strip_voice_profiles,
    video_prompt_to_yaml,
)
from lib.reference_video import assemble_shots_text
from lib.reference_video.units import reference_unit_video_bucket
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.speech_composition import (
    SpeechAdmissionError,
    require_script_unit_admitted,
    video_unit_replan_problems,
)
from lib.storyboard_sequence import get_storyboard_items, resolve_storyboard_image_ref
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    tool_error,
    validate_script_filename,
)
from server.services.reference_video_tasks import (
    ProjectDurationContext,
    precheck_unit,
    resolve_project_duration_context,
)
from server.services.video_caps import assert_audio_switch_supported, resolve_project_is_silent

_CONFIRM_DURATION_SCHEMA_PROPERTY = {
    "type": "boolean",
    "description": (
        "参考生视频时长确认：unit 申请时长与剧本编排不一致时首次调用不入队，"
        "返回待确认清单；用户同意后带 confirm_duration=true 再次调用完成入队。"
    ),
}


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
class DurationConfirmationPending:
    """待确认的 unit 时长清单：申请秒数与剧本编排不一致，尚未入队任何任务。"""

    items: list[dict[str, Any]]


def _duration_confirmation_response(pending: DurationConfirmationPending, log: list[str]) -> dict[str, Any]:
    """把待确认清单连同调用期间产生的 log 一并交给调用方转述。

    log 携带的是同样影响生成范围的事实（如 scene_id 被忽略转整集、ad 派生出的 unit 数），
    确认时一并呈现，用户才知道自己同意的是什么范围。
    """
    lines = [*log, "以下 unit 申请时长与剧本编排不一致，需先向用户确认，本次未入队任何任务："]
    for item in pending.items:
        longer_or_shorter = "更长" if item["adjustment"] == "up" else "更短"
        lines.append(
            f"- {item['unit_id']}：剧本总时长 {item['script_duration']}s，"
            f"将申请 {item['request_duration']}s（成片{longer_or_shorter}）"
        )
    lines.append("用户同意后，带 confirm_duration=true 再次调用本工具完成入队。")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


async def _pending_duration_confirmations(
    *,
    project: dict[str, Any],
    episode: int | None,
    units: list[Any],
    skip_ids: set[str],
    spec_for: Callable[[Any], TaskSpec],
) -> list[dict[str, Any]]:
    """收集本批将入队的 unit 中，申请时长与剧本编排不一致的清单。

    项目视频能力（档位 + 分辨率）按能力桶至多各解析一次（:func:`resolve_project_duration_context`；
    unit 按自身参考集分桶，与执行侧同口径），批内
    逐 unit 取档改用纯函数 :func:`precheck_unit`——避免整批 N 个 unit 各自触发一轮
    DB 往返。解析推迟到第一个真正需要取档的 unit：整批都已完成或都被跳过时不触发任何 IO。

    悬空索引 / 结构异常 / 空提示词的 unit 在此复用与 ``build_specs`` 同一份 spec 构造
    （``spec_for``）判定可入队性并静默跳过，不在预检阶段重复报错——不合法的 unit
    ``build_specs`` 阶段本就会拒绝，若仍纳入确认清单或触发 ctx 解析，会让批次卡在一个
    注定不会入队的 unit 上，且申请时长的转述本身就是失实的（该 unit 根本不会被生成）。
    """
    ctxs: dict[VideoCapability, ProjectDurationContext] = {}
    items: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id or unit_id in skip_ids:
            continue
        try:
            spec_for(unit)
        except ValueError:
            continue
        bucket = reference_unit_video_bucket(unit)
        if bucket not in ctxs:
            ctxs[bucket] = await resolve_project_duration_context(project, capability=bucket)
        try:
            slot = precheck_unit(ctxs[bucket], unit)
        except ValueError:
            continue
        if slot.needs_confirmation:
            items.append(
                {
                    "unit_id": unit_id,
                    "script_duration": slot.total_seconds,
                    "request_duration": slot.seconds,
                    "adjustment": slot.adjustment,
                }
            )
    return items


async def _assert_audio_switch_for_units(
    *,
    project: dict[str, Any],
    units: list[Any],
    skip_ids: set[str],
    spec_for: Callable[[Any], TaskSpec],
) -> None:
    """参考路线入队前的音频闸门，按本批真正要入队的 unit 所属能力桶逐桶检查。

    定桶口径与 :func:`_pending_duration_confirmations` 一致（同一份 ``spec_for`` 判可入队性、
    unit 以自身参考集定桶），同一桶只解析一次。放在时长确认之前：确认轮次走完再拒
    等于让用户白确认一遍；整批都已完成或都不可入队时不触发任何 IO。
    """
    checked: set[VideoCapability] = set()
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id or unit_id in skip_ids:
            continue
        try:
            spec_for(unit)
        except ValueError:
            continue
        bucket = reference_unit_video_bucket(unit)
        if bucket in checked:
            continue
        checked.add(bucket)
        await assert_audio_switch_supported(project, bucket)


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


async def _assert_audio_switch_for_storyboard(ctx: ToolContext) -> None:
    """分镜路线入队前的音频闸门（``assert_audio_switch_supported``，与 WebUI 提交入口同一判据）。

    成片恒有声的模型收不到关闭音频的请求，放行只会让无声判据把音色约束整批裁掉。闸门与内容模式
    无关，narration/ad 同样受检。

    调用点固定在「确有任务要入队」之后、提交之前：整集已完成、或条目全被
    :func:`_build_video_specs` 过滤时本就不会产生任何请求，此时拒绝等于把一次正常的空转变成报错。
    参考路线的 :func:`_assert_audio_switch_for_units` 是同一语义。
    """
    project = ctx.pm.load_project(ctx.project_name)
    await assert_audio_switch_supported(project, video_bucket_for_generation_mode(project.get("generation_mode")))


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


def _build_video_specs(
    *,
    items: list[dict[str, Any]],
    id_field: str,
    content_mode: str,
    script_filename: str,
    project_dir: Path,
    skip_ids: list[str] | None,
    log: list[str],
    voice_characters: dict[str, Any] | None = None,
) -> tuple[list[TaskSpec], dict[str, int]]:
    item_type = "片段" if content_mode == "narration" else "场景"
    skip_set = set(skip_ids or [])

    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, item in enumerate(items):
        item_id = item.get(id_field) or item.get("scene_id") or item.get("segment_id") or f"item_{idx}"
        if item_id in skip_set:
            continue

        storyboard_image = get_generated_assets(item).get("storyboard_image")
        # 字段值来自磁盘剧本 JSON，不可信任：非字符串脏数据/越界/绝对路径引用统一交给
        # resolve_storyboard_image_ref 校验（与路由入队预检、执行层读盘点共用同一份），
        # 批量场景下单个条目非法只跳过并记日志，不中断整批。
        try:
            storyboard_path = resolve_storyboard_image_ref(project_dir, storyboard_image)
        except ValueError as exc:
            log.append(f"⚠️  {item_type} {item_id} 的分镜图引用无效，跳过: {exc}")
            continue
        if storyboard_path is None:
            log.append(f"⚠️  {item_type} {item_id} 没有分镜图，跳过")
            continue
        if not storyboard_path.is_file():
            log.append(f"⚠️  分镜图不存在: {storyboard_path}，跳过")
            continue

        try:
            prompt = _get_video_prompt(item, content_mode=content_mode, voice_characters=voice_characters)
        except Exception as exc:  # noqa: BLE001
            log.append(f"⚠️  {item_type} {item_id} 的 video_prompt 无效，跳过: {exc}")
            continue

        # duration 是能力维度，留待执行层在 provider 解析后校验（见 ADR-0001）；
        # 原样透传调用方显式指定的值，不在入队侧做 int() 截断式归一化（否则会把
        # 本应被执行层拒绝的非法值静默修正）。缺省由执行层按 caps 收口默认。
        extra_payload: dict[str, Any] = {}
        duration = item.get("duration_seconds")
        if duration is not None:
            extra_payload["duration_seconds"] = duration

        specs.append(
            TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                prompt=prompt,
                script_file=script_filename,
                extra_payload=extra_payload or None,
            )
        )
        order_map[item_id] = idx
    return specs, order_map


def _reference_unit_spec(unit: Any, script_filename: str) -> TaskSpec:
    """单 unit 的 TaskSpec 构造，供批量入队与时长预检共用同一份结构校验
    （见 ADR-0001）——``TaskSpec.from_request`` 是「是否可入队」的唯一真相源，两处判断
    不能各自维护一份、由此产生分歧（如预检放行了 build_specs 会拒绝的空提示词 unit）。
    """
    # 用 .get 归一化：缺失 unit_id 的坏数据（Agent 可裸写 script JSON）会被 from_request
    # 当作空 resource_id 拒绝，而不是在此抛 KeyError 中断整批。
    if not isinstance(unit, dict):
        raise ValueError("unit 必须是对象")
    unit_id = str(unit.get("unit_id") or "")
    if unit.get("needs_replan") is True:
        require_script_unit_admitted("video_units", unit)
    if not unit.get("shots"):
        raise ValueError("没有 shots")
    spec = TaskSpec.from_request(
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        prompt=assemble_shots_text(unit["shots"]),
        script_file=script_filename,
    )
    require_script_unit_admitted("video_units", unit)
    return spec


def _build_reference_specs(
    *,
    units: list[Any],
    script_filename: str,
    skip_ids: list[str] | None,
    log: list[str],
) -> tuple[list[TaskSpec], dict[str, int]]:
    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "") if isinstance(unit, dict) else ""
        if unit_id in skip_set:
            continue
        # 任一 unit 不合法（没有 shots、空提示词、或 from_request 对空 resource_id 抛的
        # 裸 ValueError）都跳过并告警，不让一个坏 unit 中断整批。TaskSpecValidationError
        # 是 ValueError 子类，捕 ValueError 同时覆盖两者。
        try:
            spec = _reference_unit_spec(unit, script_filename)
        except SpeechAdmissionError as exc:
            if any(problem.code.value != "parse_failed" for problem in exc.admission.problems):
                raise
            log.append(f"⚠️  {unit_id} 发声准入未通过，跳过：{json.dumps(exc.admission.to_dict(), ensure_ascii=False)}")
            continue
        except ValueError as exc:
            log.append(f"⚠️  {unit_id} 入队校验未通过，跳过：{exc}")
            continue
        specs.append(spec)
        order_map[unit_id] = idx
    return specs, order_map


def _scan_completed_items(
    items: list[dict[str, Any]],
    id_field: str,
    completed_scenes: list[str],
    videos_dir: Path,
) -> tuple[list[Path | None], list[str], list[str]]:
    """Pure scan: reconcile checkpoint claims against on-disk videos.

    Returns ``(ordered_paths, already_done, completed_filtered)``:
    - ``ordered_paths[i]`` is the existing mp4 path for items[i] iff the
      checkpoint claimed it AND the file is on disk; else ``None``.
    - ``already_done`` is the subset of items the caller can skip enqueueing.
    - ``completed_filtered`` drops ids the checkpoint claimed but whose file
      is missing — caller should write this back instead of mutating its
      checkpoint list in place.
    """
    ordered_paths: list[Path | None] = [None] * len(items)
    already_done: list[str] = []
    stale_completions: set[str] = set()
    for idx, item in enumerate(items):
        item_id = item.get(id_field, item.get("scene_id", f"item_{idx}"))
        if item_id not in completed_scenes:
            continue
        video_output = videos_dir / f"scene_{item_id}.mp4"
        if video_output.exists():
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
    log: list[str],
) -> list[BatchTaskResult]:
    """Run a batch and update checkpoint per success. Returns failures.

    ``fallback_relpath`` is called only when the queue result lacks
    ``file_path``; reference_video tasks need a different naming convention
    than scene videos, so the caller chooses per task family.
    """

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or fallback_relpath(br.resource_id)
        output_path = project_dir / relative_path
        ordered_paths[order_map[br.resource_id]] = output_path
        completed.append(br.resource_id)
        save_fn()
        log.append(f"    ✓ {output_path.name}")

    def on_failure(br: BatchTaskResult) -> None:
        log.append(f"    ✗ {br.resource_id}: {br.error}")

    _, failures = await batch_enqueue_and_wait(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )
    return failures


async def _generate_reference_units(
    *,
    ctx: ToolContext,
    units: list[Any],
    episode: int,
    resume: bool,
    log: list[str],
    checkpoint_path: Path | None,
    build_specs: Callable[[list[Any], list[str], list[str]], tuple[list[TaskSpec], dict[str, int]]],
    spec_for: Callable[[Any], TaskSpec],
    project: dict[str, Any],
    confirm_duration: bool,
    reuse_existing: Callable[[dict[str, Any]], bool],
) -> list[Path] | DurationConfirmationPending:
    """unit 批量生成的共享骨架：时长确认 + checkpoint 续传 + 已产出扫描 + 入队等待。

    所有内容模式的 ``video_units`` 共用同一构造路径。``spec_for``
    是同一份单 unit 构造逻辑，供时长预检判定可入队性，与 ``build_specs`` 不能有
    第二份校验口径。

    ``reuse_existing`` 决定磁盘上已存在的 ``{unit_id}.mp4`` 能否当作该 unit 的
    现行产物复用。调用方必须用持久化资产归属判定，不能只凭同名文件存在猜测；共享
    骨架还会先应用重规划闸门，迁移保留的旧产物不能让 ``needs_replan`` 单元绕过修复。

    ``confirm_duration`` 为 false 时，若待入队 unit 中有申请时长与剧本编排不一致的
    （见 :func:`server.services.reference_video_tasks.resolve_duration_slot`），该调用
    调用不产生任何任务，返回 :class:`DurationConfirmationPending` 供调用方转述给用户；
    用户同意后调用方带 ``confirm_duration=True`` 重新调用完成入队（与 Web 端
    ``duration-precheck`` 预检共用同一取档规则）。

    ``checkpoint_path`` 为 None 表示生成不落 checkpoint：点名重新生成一律强制覆盖，
    没有可续传的语义，写一份没有读者的进度文件只会在中断时留下垃圾，也会覆盖掉整集
    生成留下的进度。
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
    for idx, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            continue
        candidate = output_dir / f"{unit_id}.mp4"
        if candidate.exists() and not video_unit_replan_problems(unit) and reuse_existing(unit):
            ordered_paths[idx] = candidate
            already_done.append(unit_id)
            if unit_id not in completed:
                completed.append(unit_id)
        elif unit_id in completed:
            completed.remove(unit_id)

    await _assert_audio_switch_for_units(
        project=project,
        units=units,
        skip_ids=set(already_done),
        spec_for=spec_for,
    )

    if not confirm_duration:
        pending = await _pending_duration_confirmations(
            project=project,
            episode=episode,
            units=units,
            skip_ids=set(already_done),
            spec_for=spec_for,
        )
        if pending:
            return DurationConfirmationPending(items=pending)

    specs, order_map = build_specs(units, already_done, log)
    if specs:
        failures = await _submit_with_checkpoint(
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
            log=log,
        )
        if failures:
            raise RuntimeError(f"{len(failures)} 个 unit 生成失败")

    final = [p for p in ordered_paths if p is not None]
    if not final:
        raise RuntimeError("没有生成任何 video_unit")
    if ckpt_path is not None:
        _clear_checkpoint_at(ckpt_path)
    return final


async def _run_reference_episode(
    *,
    ctx: ToolContext,
    script: dict[str, Any],
    script_filename: str,
    resume: bool,
    confirm_duration: bool,
    log: list[str],
) -> dict[str, Any]:
    """Run reference_video-mode generation and format the tool response.

    All 4 video handlers fall through to whole-episode reference generation
    when ``_resolve_reference_route`` reports the episode branch; this captures
    the shared tail (resolve episode → generate units → header + log).
    """
    episode = ProjectManager.resolve_episode_from_script(script, script_filename)
    units = script.get("video_units")
    if "video_units" in script and not isinstance(units, list):
        # 路线闸门只问键在不在、不问值的类型，容器校验落在这里：不拦的话脏值（导入 / 外部编辑
        # 产生的 dict、字符串）会一路下传到 unit 迭代，报出无从定位的 TypeError。
        raise ValueError(f"第 {episode} 集 video_units 必须是数组，当前为 {type(units).__name__}：{script_filename}")
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")
    project = ctx.pm.load_project(ctx.project_name)
    result = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        log=log,
        checkpoint_path=_episode_checkpoint_path(ctx.project_path, episode),
        build_specs=lambda u, skip, lg: _build_reference_specs(
            units=u, script_filename=script_filename, skip_ids=skip, log=lg
        ),
        spec_for=lambda u: _reference_unit_spec(u, script_filename),
        project=project,
        confirm_duration=confirm_duration,
        reuse_existing=lambda unit: (
            get_generated_assets(unit).get("video_clip") == _reference_fallback_relpath(str(unit.get("unit_id") or ""))
        ),
    )
    if isinstance(result, DurationConfirmationPending):
        return _duration_confirmation_response(result, log)
    header = f"第 {episode} 集参考视频生成完成，共 {len(result)} 个 unit"
    return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}


def _select_reference_units(script: dict[str, Any], unit_ids: list[str], log: list[str]) -> list[dict[str, Any]]:
    """按 unit_id 从 ``video_units`` 点名取 unit，重复 ID 只取一次。"""
    indexed = script.get("video_units")
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(indexed, list):
        for unit in indexed:
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str) and unit["unit_id"]:
                by_id.setdefault(unit["unit_id"], unit)

    selected: list[dict[str, Any]] = []
    for unit_id in dict.fromkeys(unit_ids):
        unit = by_id.get(unit_id)
        if unit is None:
            log.append(f"⚠️  unit {unit_id} 不在 video_units 中，跳过")
            continue
        selected.append(unit)
    if not selected:
        known = "、".join(by_id) if by_id else "（video_units 为空）"
        raise ValueError(f"没有匹配到任何 unit：{', '.join(unit_ids)}；现有 {known}")
    return selected


def _assert_reference_units_generatable(units: list[dict[str, Any]], script_filename: str) -> None:
    """点名的 unit 逐个当场校验可入队性，不合法即抛错。

    批量路径对坏 unit 是「跳过并告警」——一个坏 unit 不该中断整批；点名重新生成没有
    批次可保全，沿用跳过会让调用以「没有生成任何 video_unit」收场，智能体转述不出原因。
    校验走 ``_reference_unit_spec``，与真正入队时同一份构造，不另立判据。
    """
    for unit in units:
        try:
            _reference_unit_spec(unit, script_filename)
        except SpeechAdmissionError:
            raise
        except ValueError as exc:
            raise ValueError(f"unit {unit.get('unit_id')} 无法生成：{exc}") from exc


async def _assert_no_active_tasks(ctx: ToolContext, script_filename: str, units: list[dict[str, Any]]) -> None:
    """点名重做前探测同 unit 是否已有在途任务：命中即拒绝，不新建任务也不静默沿用在途任务。

    点名即强制（见 ``_run_reference_units`` docstring），但强制不等于抢占——在途任务
    没有可抢占的中间产物，直接入队只会被 ``enqueue`` 的去重悄悄折回既有任务，智能体读到
    一次"已提交"却并未真的重做。整批拒绝而非部分入队，避免一部分 unit 已建任务、一部分
    被拒的不一致状态。只作用于点名路径；常规批量生成（``_run_reference_episode``）
    仍走 ``GenerationQueue.enqueue_task`` 的既有入队去重。
    """
    unit_ids = [str(u["unit_id"]) for u in units]
    active = await get_active_tasks_for_resources(
        project_name=ctx.project_name,
        task_type="reference_video",
        resource_ids=unit_ids,
        script_file=script_filename,
    )
    if not active:
        return
    details = "、".join(f"{t['resource_id']}（状态：{t['status']}）" for t in active)
    raise ValueError(f"以下 unit 已有在途任务，请等待其完成后再重做：{details}")


async def _run_reference_units(
    *,
    ctx: ToolContext,
    script_filename: str,
    unit_ids: list[str],
    confirm_duration: bool,
    log: list[str],
) -> dict[str, Any]:
    """对点名的参考生视频 unit 强制重新生成成片。"""
    project = ctx.pm.load_project(ctx.project_name)
    script = ctx.pm.load_script(ctx.project_name, script_filename)
    episode = ProjectManager.resolve_episode_from_script(script, script_filename)

    selected = _select_reference_units(script, unit_ids, log)
    # 结构校验先于在途任务探测：结构不合法的 unit 等在途任务跑完也依然生成不了，
    # 先报「请等待」会把一个死结说成暂时性阻塞。顺带省掉一次注定要失败的库查询。
    _assert_reference_units_generatable(selected, script_filename)
    await _assert_no_active_tasks(ctx, script_filename, selected)
    log.append(f"重新生成 {len(selected)} 个 unit（已有成片一律覆盖）：{', '.join(u['unit_id'] for u in selected)}")

    result = await _generate_reference_units(
        ctx=ctx,
        units=selected,
        episode=episode,
        resume=False,
        log=log,
        checkpoint_path=None,
        build_specs=lambda u, skip, lg: _build_reference_specs(
            units=u,
            script_filename=script_filename,
            skip_ids=skip,
            log=lg,
        ),
        spec_for=lambda u: _reference_unit_spec(u, script_filename),
        project=project,
        confirm_duration=confirm_duration,
        # 点名即强制：磁盘上的同名成片一律不复用。
        reuse_existing=lambda _u: False,
    )
    if isinstance(result, DurationConfirmationPending):
        return _duration_confirmation_response(result, log)
    header = f"参考生视频重新生成完成，共 {len(result)} 个 unit"
    return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}


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
                "confirm_duration": _CONFIRM_DURATION_SCHEMA_PROPERTY,
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            resume = bool(args.get("resume"))
            confirm_duration = bool(args.get("confirm_duration"))

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=resume,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = resolve_content_mode(script, ctx.pm.load_project(ctx.project_name))
            if not items:
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
            ordered_paths, already_done, completed = _scan_completed_items(items, id_field, completed, videos_dir)
            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, order_map = _build_video_specs(
                items=items,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=already_done,
                log=log,
                voice_characters=voice_characters,
            )

            if not specs and not any(ordered_paths):
                raise RuntimeError("没有可生成的视频片段")

            if specs:
                await _assert_audio_switch_for_storyboard(ctx)
                failures = await _submit_with_checkpoint(
                    project_name=ctx.project_name,
                    project_dir=project_dir,
                    specs=specs,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    fallback_relpath=_scene_fallback_relpath,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, episode=episode),
                    log=log,
                )
                if failures:
                    raise RuntimeError(f"{len(failures)} 个视频生成失败（使用 resume=true 续传）")

            scene_videos = [p for p in ordered_paths if p is not None]
            _clear_checkpoint_at(ckpt_path)
            header = f"第 {episode} 集视频生成完成，共 {len(scene_videos)} 个片段"
            return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}
        except SpeechAdmissionError as exc:
            return _speech_admission_error("generate_video_episode", exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_episode", exc, log)

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
                "confirm_duration": _CONFIRM_DURATION_SCHEMA_PROPERTY,
            },
            "required": ["script", "scene_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            scene_id = args["scene_id"]
            confirm_duration = bool(args.get("confirm_duration"))

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_units(
                    ctx=ctx,
                    script_filename=script_filename,
                    unit_ids=[scene_id],
                    confirm_duration=confirm_duration,
                    log=log,
                )

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            item = next((s for s in items if s.get(id_field) == scene_id or s.get("scene_id") == scene_id), None)
            if not item:
                raise ValueError(f"场景/片段 '{scene_id}' 不存在")
            # 调用方可能用 ``scene_id`` 别名命中条目，但入队 / 文件名 / fallback
            # 必须用脚本里的规范 ``id_field`` 值，否则下游 generate_video_all 和
            # checkpoint 扫描会找不到产物。
            item_id = str(item[id_field])
            require_script_unit_admitted(resolve_script_kind(script), item)

            storyboard_image = get_generated_assets(item).get("storyboard_image")
            # 字段值来自磁盘剧本 JSON，不可信任：resolve_storyboard_image_ref 统一做类型检查 +
            # 越界 / 绝对路径拒绝（与路由入队预检、执行层读盘点共用同一份），异常经外层
            # except 转为可读的 tool_error，不再让非字符串脏数据抛未处理 TypeError。
            storyboard_path = resolve_storyboard_image_ref(project_dir, storyboard_image)
            if storyboard_path is None:
                raise ValueError(f"场景/片段 '{item_id}' 没有分镜图，请先运行 generate_storyboards")
            if not storyboard_path.is_file():
                raise FileNotFoundError(f"分镜图不存在: {storyboard_path}")

            content_mode = resolve_content_mode(script, ctx.pm.load_project(ctx.project_name))
            voice_characters = await _resolve_voice_context(ctx, content_mode)
            prompt = _get_video_prompt(item, content_mode=content_mode, voice_characters=voice_characters)
            # duration 是能力维度，留待执行层在 provider 解析后校验（见 ADR-0001）；
            # 原样透传调用方显式指定的值，不在入队侧做 int() 截断式归一化（否则会把
            # 本应被执行层拒绝的非法值静默修正）。缺省由执行层按 caps 收口默认。
            extra_payload: dict[str, Any] = {}
            duration = item.get("duration_seconds")
            if duration is not None:
                extra_payload["duration_seconds"] = duration
            spec = TaskSpec.from_request(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                prompt=prompt,
                script_file=script_filename,
                extra_payload=extra_payload or None,
            )

            await _assert_audio_switch_for_storyboard(ctx)
            queued = await enqueue_and_wait(
                project_name=ctx.project_name,
                task_type=spec.task_type,
                media_type=spec.media_type,
                resource_id=spec.resource_id,
                payload=spec.payload,
                script_file=spec.script_file,
                source="skill",
            )
            result = queued.get("result") or {}
            rel = result.get("file_path") or f"videos/scene_{item_id}.mp4"
            output_path = project_dir / rel
            return {"content": [{"type": "text", "text": f"✅ 视频已保存: {output_path}"}]}
        except SpeechAdmissionError as exc:
            return _speech_admission_error("generate_video_scene", exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_scene", exc, log)

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
                "confirm_duration": _CONFIRM_DURATION_SCHEMA_PROPERTY,
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            confirm_duration = bool(args.get("confirm_duration"))
            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            route = _resolve_reference_route(ctx, script)
            if route is not None:
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=False,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = resolve_content_mode(script, ctx.pm.load_project(ctx.project_name))
            pending = [it for it in items if not get_generated_assets(it).get("video_clip")]
            if not pending:
                return {"content": [{"type": "text", "text": "✨ 所有场景/片段的视频都已生成"}]}

            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, _order_map = _build_video_specs(
                items=pending,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=None,
                log=log,
                voice_characters=voice_characters,
            )
            if not specs:
                return {"content": [{"type": "text", "text": "\n".join([*log, "⚠️  没有任何可生成的视频任务"])}]}

            await _assert_audio_switch_for_storyboard(ctx)
            successes, failures = await batch_enqueue_and_wait(project_name=ctx.project_name, specs=specs)
            details: list[str] = []
            for br in successes:
                rel = (br.result or {}).get("file_path") or f"videos/scene_{br.resource_id}.mp4"
                details.append(f"  ✓ {br.resource_id} → {rel}")
            for br in failures:
                details.append(f"  ✗ {br.resource_id}: {br.error}")
            header = f"generate_video_all summary: {len(successes)} succeeded, {len(failures)} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *log, *details])}],
                "is_error": bool(failures),
            }
        except SpeechAdmissionError as exc:
            return _speech_admission_error("generate_video_all", exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_all", exc, log)

    return _handler


def generate_video_selected_tool(ctx: ToolContext):
    @tool(
        "generate_video_selected",
        "生成指定多个场景的视频。storyboard 项目用按 scene_ids 哈希的独立 checkpoint，支持 resume 续传。"
        "reference_video 项目传 unit_id 列表即对这些 unit 重新生成（覆盖已有成片），不落 checkpoint、不支持 resume。",
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
                "confirm_duration": _CONFIRM_DURATION_SCHEMA_PROPERTY,
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
            scene_ids: list[str] = list(dict.fromkeys(args["scene_ids"]))
            resume = bool(args.get("resume"))
            confirm_duration = bool(args.get("confirm_duration"))

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
                    confirm_duration=confirm_duration,
                    log=log,
                )

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = resolve_content_mode(script, ctx.pm.load_project(ctx.project_name))

            items_by_id: dict[str, dict[str, Any]] = {}
            for item in items:
                items_by_id[item.get(id_field, "")] = item
                if "scene_id" in item:
                    items_by_id[item["scene_id"]] = item

            selected: list[dict[str, Any]] = []
            seen_canonical: set[str] = set()
            # ``items_by_id`` 同时按 ``id_field`` 与 ``scene_id`` 索引同一个 item，
            # 调用方若把两个值都列入 ``scene_ids`` 会让同一场景重复入队——必须按
            # 规范 ``id_field`` 再去一次重。
            for sid in scene_ids:
                if sid not in items_by_id:
                    log.append(f"⚠️  场景/片段 '{sid}' 不存在，跳过")
                    continue
                item = items_by_id[sid]
                canonical = str(item.get(id_field, ""))
                if canonical and canonical in seen_canonical:
                    continue
                seen_canonical.add(canonical)
                selected.append(item)
            if not selected:
                raise ValueError("没有找到任何有效的场景/片段")

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
            ordered_paths, already_done, completed = _scan_completed_items(selected, id_field, completed, videos_dir)
            voice_characters = await _resolve_voice_context(ctx, content_mode)
            specs, order_map = _build_video_specs(
                items=selected,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=already_done,
                log=log,
                voice_characters=voice_characters,
            )

            # ``_build_video_specs`` 可能把所有 selected 都过滤掉（缺分镜图 /
            # video_prompt 无效），此时如果 ``ordered_paths`` 也没有已生成项就是
            # "什么也没做"，必须抛错，否则下游会把 "完成：0 个" 当成功推进流程。
            if not specs and not any(ordered_paths):
                raise RuntimeError("没有任何可生成的视频任务（全部 selected 都被跳过）")

            if specs:
                await _assert_audio_switch_for_storyboard(ctx)
                failures = await _submit_with_checkpoint(
                    project_name=ctx.project_name,
                    project_dir=project_dir,
                    specs=specs,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    fallback_relpath=_scene_fallback_relpath,
                    save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, scene_ids=scene_ids),
                    log=log,
                )
                if failures:
                    raise RuntimeError(f"{len(failures)} 个视频生成失败（使用 resume=true 续传）")

            final_results = [p for p in ordered_paths if p is not None]
            _clear_checkpoint_at(ckpt_path)
            header = f"generate_video_selected 完成：{len(final_results)} 个"
            return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}
        except SpeechAdmissionError as exc:
            return _speech_admission_error("generate_video_selected", exc, log)
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_selected", exc, log)

    return _handler


__all__ = [
    "generate_video_episode_tool",
    "generate_video_scene_tool",
    "generate_video_all_tool",
    "generate_video_selected_tool",
]
