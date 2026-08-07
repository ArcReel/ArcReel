"""SDK MCP tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.config.resolver import VideoCapability
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
from lib.reference_video.ad_units import (
    ad_stale_unit_ids,
    render_ad_unit_prompt,
    resolve_ad_unit_shots,
    sync_ad_reference_units,
)
from lib.reference_video.units import reference_unit_video_bucket
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton
from lib.storyboard_sequence import get_storyboard_items, resolve_storyboard_image_ref
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    tool_error,
    validate_script_filename,
)
from server.services.reference_video_tasks import (
    ProjectDurationContext,
    precheck_unit,
    resolve_max_unit_duration,
    resolve_project_duration_context,
)
from server.services.video_caps import resolve_project_is_silent

_CONFIRM_DURATION_SCHEMA_PROPERTY = {
    "type": "boolean",
    "description": (
        "参考生视频时长确认：unit 申请时长与剧本编排不一致时首次调用不入队，"
        "返回待确认清单；用户同意后带 confirm_duration=true 再次调用完成入队。"
    ),
}


@dataclass(frozen=True)
class DurationConfirmationPending:
    """待确认的 unit 时长清单：申请秒数与剧本编排不一致，尚未入队任何任务。"""

    items: list[dict[str, Any]]


def _duration_confirmation_response(pending: DurationConfirmationPending, log: list[str]) -> dict[str, Any]:
    """把待确认清单连同本次已产生的 log 一并交给调用方转述。

    log 携带的是同样影响本次生成范围的事实（如 scene_id 被忽略转整集、ad 派生出的 unit 数），
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
    units: list[dict[str, Any]],
    skip_ids: set[str],
    spec_for: Callable[[dict[str, Any]], TaskSpec],
    ad_shots_for: Callable[[dict[str, Any]], list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    """收集本批将入队的 unit 中，申请时长与剧本编排不一致的清单。

    项目视频能力（档位 + 分辨率）按能力桶至多各解析一次（:func:`resolve_project_duration_context`；
    unit 按参考集分桶，ad 从水合后的成员镜头现算——无参考图退化镜头按 i2v 桶模型取档，
    与执行侧同口径），批内
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
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id or unit_id in skip_ids:
            continue
        try:
            spec_for(unit)
        except ValueError:
            continue
        try:
            ad_shots = ad_shots_for(unit) if ad_shots_for else None
        except ValueError:
            continue
        bucket = reference_unit_video_bucket(unit, ad_shots=ad_shots)
        if bucket not in ctxs:
            ctxs[bucket] = await resolve_project_duration_context(project, capability=bucket)
        try:
            slot = precheck_unit(ctxs[bucket], unit, ad_shots)
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


async def _resolve_voice_characters(ctx: ToolContext, content_mode: str) -> dict[str, Any] | None:
    """供 Voice_Profiles 注入的角色资产；``None`` 表示不注入。

    非 drama 直接返回 None（无需为 narration/ad 项目多付一次视频能力解析），drama 再按无声
    判据排除：C 类模型不产音、或本集关闭了音频，两条路径同口径。台词不受影响、照常下发。
    """
    if content_mode != "drama":
        return None
    project = ctx.pm.load_project(ctx.project_name)
    if await resolve_project_is_silent(project):
        return None
    return project.get("characters") or {}


def _resolve_reference_route(ctx: ToolContext, script: dict[str, Any]) -> str | None:
    """定生成路线并把守骨架闸门。

    项目走参考生视频路线时返回子分支（``"ad"`` / ``"episode"``，两者展示与派生颗粒度不同），
    分镜路线返回 ``None``。路线以 project.json 的 ``generation_mode`` 为唯一真相源——剧本不
    携带路线信息，同一项目逐集不变。

    Raises:
        SkeletonRouteMismatchError: 剧本骨架与项目路线失配，生成被拒。
    """
    project = ctx.pm.load_project(ctx.project_name)
    content_mode = resolve_content_mode(script, project)
    ensure_route_skeleton(script, content_mode, project.get("generation_mode"))
    if not is_reference_video_project(project):
        return None
    return "ad" if content_mode == "ad" else "episode"


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


def _reference_unit_spec(unit: dict[str, Any], script_filename: str) -> TaskSpec:
    """单 unit 的 narration/drama TaskSpec 构造，供批量入队与时长预检共用同一份结构校验
    （见 ADR-0001）——``TaskSpec.from_request`` 是「是否可入队」的唯一真相源，两处判断
    不能各自维护一份、由此产生分歧（如预检放行了 build_specs 会拒绝的空提示词 unit）。
    """
    # 用 .get 归一化：缺失 unit_id 的坏数据（Agent 可裸写 script JSON）会被 from_request
    # 当作空 resource_id 拒绝，而不是在此抛 KeyError 中断整批。
    unit_id = str(unit.get("unit_id") or "")
    if not unit.get("shots"):
        raise ValueError("没有 shots")
    return TaskSpec.from_request(
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        prompt=assemble_shots_text(unit["shots"]),
        script_file=script_filename,
    )


def _build_reference_specs(
    *,
    units: list[dict[str, Any]],
    script_filename: str,
    skip_ids: list[str] | None,
    log: list[str],
) -> tuple[list[TaskSpec], dict[str, int]]:
    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "")
        if unit_id in skip_set:
            continue
        # 任一 unit 不合法（没有 shots、空提示词、或 from_request 对空 resource_id 抛的
        # 裸 ValueError）都跳过并告警，不让一个坏 unit 中断整批。TaskSpecValidationError
        # 是 ValueError 子类，捕 ValueError 同时覆盖两者。
        try:
            spec = _reference_unit_spec(unit, script_filename)
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


def _ad_reference_unit_spec(
    script: dict[str, Any], unit: dict[str, Any], script_filename: str, style: str | None
) -> TaskSpec:
    """单 unit 的 ad TaskSpec 构造，供批量入队与时长预检共用同一份结构校验（同
    ``_reference_unit_spec``）。成员镜头从 shots（内容唯一真相）水合后渲染 prompt。"""
    unit_id = str(unit.get("unit_id") or "")
    shots = resolve_ad_unit_shots(script, unit)
    return TaskSpec.from_request(
        task_type="reference_video",
        media_type="video",
        resource_id=unit_id,
        prompt=render_ad_unit_prompt(shots, style=style),
        script_file=script_filename,
    )


def _build_ad_reference_specs(
    *,
    script: dict[str, Any],
    units: list[dict[str, Any]],
    script_filename: str,
    style: str | None,
    skip_ids: list[str] | None,
    log: list[str],
) -> tuple[list[TaskSpec], dict[str, int]]:
    """ad 派生索引 → TaskSpec。索引悬空 / 空画面提示词的 unit 跳过并告警，不让一个坏
    unit 中断整批（与 ``_build_reference_specs`` 同口径）。"""
    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "")
        if unit_id in skip_set:
            continue
        try:
            spec = _ad_reference_unit_spec(script, unit, script_filename, style)
        except ValueError as exc:
            log.append(f"⚠️  {unit_id} 入队校验未通过，跳过：{exc}")
            continue
        specs.append(spec)
        order_map[unit_id] = idx
    return specs, order_map


async def _generate_reference_units(
    *,
    ctx: ToolContext,
    units: list[dict[str, Any]],
    episode: int,
    resume: bool,
    log: list[str],
    checkpoint_path: Path | None,
    build_specs: Callable[[list[dict[str, Any]], list[str], list[str]], tuple[list[TaskSpec], dict[str, int]]],
    spec_for: Callable[[dict[str, Any]], TaskSpec],
    project: dict[str, Any],
    confirm_duration: bool,
    reuse_existing: Callable[[dict[str, Any]], bool] | None = None,
    ad_shots_for: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> list[Path] | DurationConfirmationPending:
    """unit 批量生成的共享骨架：时长确认 + checkpoint 续传 + 已产出扫描 + 入队等待。

    narration/drama（video_units 内容自包含）与 ad（reference_units 派生索引）
    仅 spec 构造不同，经 ``build_specs(units, skip_ids, log)`` 注入；``spec_for``
    是同一份单 unit 构造逻辑，供时长预检判定可入队性，与 ``build_specs`` 不能有
    第二份校验口径。ad 的每 unit 剧本时长需要成员镜头（``ad_shots_for``）才能
    算出，narration/drama 不传。

    ``reuse_existing`` 决定磁盘上已存在的 ``{unit_id}.mp4`` 能否当作该 unit 的
    现行产物复用（None 表示仅凭文件存在即复用）。ad 按 generated_assets 是否仍
    指向成片判定：指针为空的孤儿同名文件不可信，须由该判定排除。

    ``confirm_duration`` 为 false 时，若待入队 unit 中有申请时长与剧本编排不一致的
    （见 :func:`server.services.reference_video_tasks.resolve_duration_slot`），本次
    调用不产生任何任务，返回 :class:`DurationConfirmationPending` 供调用方转述给用户；
    用户同意后调用方带 ``confirm_duration=True`` 重新调用完成入队（与 Web 端
    ``duration-precheck`` 预检共用同一取档规则）。

    ``checkpoint_path`` 为 None 表示本次生成不落 checkpoint：点名重新生成一律强制覆盖，
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
        unit_id = unit["unit_id"]
        candidate = output_dir / f"{unit_id}.mp4"
        if candidate.exists() and (reuse_existing is None or reuse_existing(unit)):
            ordered_paths[idx] = candidate
            already_done.append(unit_id)
            if unit_id not in completed:
                completed.append(unit_id)
        elif unit_id in completed:
            completed.remove(unit_id)

    if not confirm_duration:
        pending = await _pending_duration_confirmations(
            project=project,
            episode=episode,
            units=units,
            skip_ids=set(already_done),
            spec_for=spec_for,
            ad_shots_for=ad_shots_for,
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
    )
    if isinstance(result, DurationConfirmationPending):
        return _duration_confirmation_response(result, log)
    header = f"第 {episode} 集参考视频生成完成，共 {len(result)} 个 unit"
    return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}


async def _run_ad_reference_episode(
    *,
    ctx: ToolContext,
    script_filename: str,
    resume: bool,
    confirm_duration: bool,
    log: list[str],
) -> dict[str, Any]:
    """ad + reference_video：先（重新）派生分组索引并持久化，再按 unit 批量直出。

    分组是纯函数派生（shots + 供应商时长上限 → 可复现分组）；generated_assets
    按 unit_id 沿用，已有产物经磁盘扫描跳过重复入队；成片偏离当前编排的 unit
    按读时签名比较派生 stale 并透出清单，不自动重生成。
    """
    project = ctx.pm.load_project(ctx.project_name)
    max_unit_duration = await resolve_max_unit_duration(project)

    def _sync() -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
            episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            units = sync_ad_reference_units(script, episode=episode, max_unit_duration=max_unit_duration)
            return script, units, episode

    script, units, episode = await asyncio.to_thread(_sync)
    if not units:
        raise ValueError(f"剧本没有可分组的镜头：{script_filename}")
    log.append(f"已派生 {len(units)} 个 video_unit（连续镜头分组，索引已写入剧本）")
    # stale 清单透出给调用方：这些 unit 的成片仍有效并按现有产物复用，不自动重生成；
    # 是否重生成由用户/智能体决定（重生成 finalize 落新签名后自然回归非 stale）。
    stale_ids = ad_stale_unit_ids(script, units)
    if stale_ids:
        log.append(
            f"⚠️  以下 unit 的剧本已变更但保留既有成片（stale）：{', '.join(stale_ids)}。"
            "如需更新，用 generate_video_selected 点名这些 unit 重新生成。"
        )

    style = project.get("style")
    result = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        log=log,
        checkpoint_path=_episode_checkpoint_path(ctx.project_path, episode),
        build_specs=lambda u, skip, lg: _build_ad_reference_specs(
            script=script,
            units=u,
            script_filename=script_filename,
            style=style if isinstance(style, str) else None,
            skip_ids=skip,
            log=lg,
        ),
        spec_for=lambda u: _ad_reference_unit_spec(
            script, u, script_filename, style if isinstance(style, str) else None
        ),
        project=project,
        confirm_duration=confirm_duration,
        # 剧本编辑不作废产物：有成片指针的 unit（含 stale）一律按现有产物复用，
        # 不自动重生成——stale 清单已透出，是否重生成由用户/智能体决定。
        # 指针为空的孤儿同名文件不可信，不复用。
        reuse_existing=lambda u: bool(get_generated_assets(u).get("video_clip")),
        ad_shots_for=lambda u: resolve_ad_unit_shots(script, u),
    )
    if isinstance(result, DurationConfirmationPending):
        return _duration_confirmation_response(result, log)
    header = f"参考直出生成完成，共 {len(result)} 个 unit"
    return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}


def _select_ad_units(script: dict[str, Any], unit_ids: list[str], log: list[str]) -> list[dict[str, Any]]:
    """按 unit_id 从持久化的分组索引里点名取 unit，重复 ID 只取一次。

    索引里没有的 ID 记日志跳过（多半是镜头增删后未重新派生分组），一个都没命中才抛错：
    错误里带上索引现有的 unit_id，智能体据此就能引导用户改口或先重新派生，不必再问一轮。
    """
    indexed = script.get("reference_units")
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(indexed, list):
        for unit in indexed:
            if isinstance(unit, dict) and isinstance(unit.get("unit_id"), str) and unit["unit_id"]:
                by_id.setdefault(unit["unit_id"], unit)

    selected: list[dict[str, Any]] = []
    for unit_id in dict.fromkeys(unit_ids):
        unit = by_id.get(unit_id)
        if unit is None:
            log.append(f"⚠️  unit {unit_id} 不在分组索引中，跳过")
            continue
        selected.append(unit)
    if not selected:
        known = "、".join(by_id) if by_id else "（索引为空，请先整集生成或重新派生分组）"
        raise ValueError(f"没有匹配到任何 unit：{', '.join(unit_ids)}；索引中现有 {known}")
    return selected


def _assert_ad_units_generatable(
    script: dict[str, Any], units: list[dict[str, Any]], script_filename: str, style: str | None
) -> None:
    """点名的 unit 逐个当场校验可入队性，不合法即抛错。

    批量路径对坏 unit 是「跳过并告警」——一个坏 unit 不该中断整批；点名重新生成没有
    批次可保全，沿用跳过会让调用以「没有生成任何 video_unit」收场，智能体转述不出原因。
    校验走 ``_ad_reference_unit_spec``，与真正入队时同一份构造，不另立判据。
    """
    for unit in units:
        try:
            _ad_reference_unit_spec(script, unit, script_filename, style)
        except ValueError as exc:
            raise ValueError(f"unit {unit.get('unit_id')} 无法生成：{exc}") from exc


def _report_residual_staleness(ctx: ToolContext, script_filename: str, unit_ids: set[str], log: list[str]) -> None:
    """生成后按签名复核点名 unit 是否仍偏离编排，仍偏离的明说。

    stale 是产物签名与当前编排的读时比较结果，正常路径下 finalize 落新签名即自然回清；
    但生成期间剧本被改、或产物没带上签名时它会留下来。不复核就只能由智能体替系统宣布
    角标已消失，说错了用户无从察觉。复核失败不改变本次生成的成败——产物已落盘、已计费。

    覆盖不到全部点名 unit 的复核一律按「无法复核」报，不按「未偏离」报：索引被并发重新
    派生后点名的 ID 可能已不存在，此时过滤只会得到空集合，沿用空集合等于替系统宣布干净。
    """
    try:
        fresh = ctx.pm.load_script(ctx.project_name, script_filename)
        indexed = fresh.get("reference_units") or []
        if not isinstance(indexed, list):
            raise ValueError(f"reference_units 必须是数组，当前为 {type(indexed).__name__}")
        units = [u for u in indexed if isinstance(u, dict) and u.get("unit_id") in unit_ids]
        if missing := unit_ids - {str(u["unit_id"]) for u in units}:
            raise ValueError(f"以下 unit 已不在分组索引中：{'、'.join(sorted(missing))}")
        residual = ad_stale_unit_ids(fresh, units)
    except Exception as exc:  # noqa: BLE001
        log.append(f"⚠️  无法复核重新生成后的 stale 状态：{exc}")
        return
    if residual:
        log.append(f"⚠️  以下 unit 重新生成后仍偏离当前编排（stale），可能是生成期间剧本又被改动：{', '.join(residual)}")


async def _assert_no_active_tasks(ctx: ToolContext, script_filename: str, units: list[dict[str, Any]]) -> None:
    """点名重做前探测同 unit 是否已有在途任务：命中即拒绝，不新建任务也不静默沿用在途任务。

    点名即强制（见 ``_run_ad_reference_units`` docstring），但强制不等于抢占：撞上同一
    unit 的在途任务时用户已裁定拒绝入队，不采用「沿用在途任务并在输出中透出 deduped」。
    只作用于点名路径；常规批量生成（``_run_ad_reference_episode``）的入队去重语义不变，
    仍走 ``GenerationQueue.enqueue_task`` 的既有去重。
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


async def _run_ad_reference_units(
    *,
    ctx: ToolContext,
    script_filename: str,
    unit_ids: list[str],
    confirm_duration: bool,
    log: list[str],
) -> dict[str, Any]:
    """ad + reference_video：对点名的 unit 重新生成成片，不重新派生分组。

    与 Web 端逐 unit 重新生成同口径：分组索引原样沿用（分组是纯函数，重生成单个 unit
    时可复现），成员镜头按索引从 shots 水合。点名即强制——已有成片的 unit 一律重新
    生成，不按现有产物复用，因此非 stale 的 unit 也能重来一次；stale 不是被谁清掉的
    状态位，而是产物签名与当前编排的比较结果，finalize 落下新签名后自然不再偏离。
    """
    project = ctx.pm.load_project(ctx.project_name)
    script = ctx.pm.load_script(ctx.project_name, script_filename)
    episode = ProjectManager.resolve_episode_from_script(script, script_filename)

    selected = _select_ad_units(script, unit_ids, log)
    await _assert_no_active_tasks(ctx, script_filename, selected)
    style = project.get("style")
    style_str = style if isinstance(style, str) else None
    _assert_ad_units_generatable(script, selected, script_filename, style_str)
    log.append(f"重新生成 {len(selected)} 个 unit（已有成片一律覆盖）：{', '.join(u['unit_id'] for u in selected)}")

    result = await _generate_reference_units(
        ctx=ctx,
        units=selected,
        episode=episode,
        resume=False,
        log=log,
        checkpoint_path=None,
        build_specs=lambda u, skip, lg: _build_ad_reference_specs(
            script=script,
            units=u,
            script_filename=script_filename,
            style=style_str,
            skip_ids=skip,
            log=lg,
        ),
        spec_for=lambda u: _ad_reference_unit_spec(script, u, script_filename, style_str),
        project=project,
        confirm_duration=confirm_duration,
        # 点名即强制：磁盘上的同名成片一律不复用，否则「重新生成」会变成一次空转，
        # 而这恰是 stale unit 最需要它生效的场景。
        reuse_existing=lambda _u: False,
        ad_shots_for=lambda u: resolve_ad_unit_shots(script, u),
    )
    if isinstance(result, DurationConfirmationPending):
        return _duration_confirmation_response(result, log)
    _report_residual_staleness(ctx, script_filename, {u["unit_id"] for u in selected}, log)
    header = f"参考直出重新生成完成，共 {len(result)} 个 unit"
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
            if route == "episode":
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=resume,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            if route == "ad":
                return await _run_ad_reference_episode(
                    ctx=ctx,
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
            voice_characters = await _resolve_voice_characters(ctx, content_mode)
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
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_episode", exc, log)

    return _handler


def generate_video_scene_tool(ctx: ToolContext):
    @tool(
        "generate_video_scene",
        "生成单个场景/片段的视频。ad 参考直出项目传 unit_id 即对该 unit 重新生成（覆盖已有成片）；"
        "其余 reference_video 项目会忽略 scene_id 转为整集生成。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_id": {
                    "type": "string",
                    "description": "场景或片段 ID；ad 参考直出项目传 video_unit 的 unit_id（如 E1U2）",
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
            if route == "episode":
                log.append(
                    f"⚠️  narration / drama 的 reference_video 项目暂不支持单 unit 精确选择；"
                    f"scene_id={scene_id} 被忽略，转整集生成。"
                )
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=False,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            if route == "ad":
                return await _run_ad_reference_units(
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
            voice_characters = await _resolve_voice_characters(ctx, content_mode)
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
            if route == "episode":
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=False,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            if route == "ad":
                return await _run_ad_reference_episode(
                    ctx=ctx,
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

            voice_characters = await _resolve_voice_characters(ctx, content_mode)
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
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_all", exc, log)

    return _handler


def generate_video_selected_tool(ctx: ToolContext):
    @tool(
        "generate_video_selected",
        "生成指定多个场景的视频。storyboard 项目用按 scene_ids 哈希的独立 checkpoint，支持 resume 续传。"
        "ad 参考直出项目传 unit_id 列表即对这些 unit 重新生成（覆盖已有成片），不落 checkpoint、不支持 resume；"
        "其余 reference_video 项目会忽略 scene_ids 转整集生成。",
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
                    "description": '场景或片段 ID 列表；ad 参考直出项目传 video_unit 的 unit_id 列表（如 ["E1U2"]）',
                },
                "resume": {
                    "type": "boolean",
                    "description": "是否从上次中断处继续；ad 参考直出项目的点名重新生成会忽略此参数",
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
            if route == "episode":
                log.append(
                    f"⚠️  narration / drama 的 reference_video 项目暂不支持多 unit 精确选择；"
                    f"scene_ids={','.join(scene_ids)} 被忽略，转整集生成。"
                )
                return await _run_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    resume=resume,
                    confirm_duration=confirm_duration,
                    log=log,
                )
            if route == "ad":
                if resume:
                    # 点名重新生成一律覆盖已有成片，没有可续传的中断态；照单收下再无视会让
                    # 调用方以为断点还在。
                    log.append("⚠️  点名重新生成不支持续传，resume 已忽略。")
                return await _run_ad_reference_units(
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
            voice_characters = await _resolve_voice_characters(ctx, content_mode)
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
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_video_selected", exc, log)

    return _handler


__all__ = [
    "generate_video_episode_tool",
    "generate_video_scene_tool",
    "generate_video_all_tool",
    "generate_video_selected_tool",
]
