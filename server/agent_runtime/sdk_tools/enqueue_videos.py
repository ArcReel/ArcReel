"""SDK MCP tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    batch_enqueue_and_wait,
    enqueue_and_wait,
)
from lib.project_manager import ProjectManager, effective_mode
from lib.prompt_utils import is_structured_video_prompt, video_prompt_to_yaml
from lib.reference_video import assemble_shots_text
from lib.reference_video.ad_units import (
    render_ad_unit_prompt,
    resolve_ad_unit_shots,
    sync_ad_reference_units,
)
from lib.storyboard_sequence import get_storyboard_items
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    tool_error,
    validate_script_filename,
)
from server.services.reference_video_tasks import resolve_max_unit_duration


def _get_video_prompt(item: dict[str, Any]) -> str:
    prompt = item.get("video_prompt")
    if not prompt:
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"片段/场景缺少 video_prompt 字段: {item_id}")
    if is_structured_video_prompt(prompt):
        return video_prompt_to_yaml(prompt)
    if isinstance(prompt, dict):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise ValueError(f"片段/场景 video_prompt 为对象但格式不符合结构化规范: {item_id}")
    if not isinstance(prompt, str):
        item_id = item.get("segment_id") or item.get("scene_id")
        raise TypeError(f"片段/场景 video_prompt 类型无效（期望 str 或 dict）: {item_id}")
    return prompt


def _is_reference_script(script: dict[str, Any]) -> bool:
    return script.get("generation_mode") == "reference_video"


def _is_ad_reference(ctx: ToolContext, script: dict[str, Any]) -> bool:
    """ad + reference_video 判定：ad 剧本骨架唯一、不打 generation_mode 戳，
    生成路径以 project.json（项目级/集级 generation_mode）为真相源。"""
    project = ctx.pm.load_project(ctx.project_name)
    if project.get("content_mode") != "ad":
        return False
    episode = script.get("episode")
    meta = next(
        (e for e in (project.get("episodes") or []) if isinstance(e, dict) and e.get("episode") == episode),
        None,
    )
    return effective_mode(project=project, episode=meta or {}) == "reference_video"


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
) -> tuple[list[TaskSpec], dict[str, int]]:
    item_type = "片段" if content_mode == "narration" else "场景"
    skip_set = set(skip_ids or [])

    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, item in enumerate(items):
        item_id = item.get(id_field) or item.get("scene_id") or item.get("segment_id") or f"item_{idx}"
        if item_id in skip_set:
            continue

        storyboard_image = (item.get("generated_assets") or {}).get("storyboard_image")
        if not storyboard_image:
            log.append(f"⚠️  {item_type} {item_id} 没有分镜图，跳过")
            continue
        storyboard_path = project_dir / storyboard_image
        if not storyboard_path.exists():
            log.append(f"⚠️  分镜图不存在: {storyboard_path}，跳过")
            continue

        try:
            prompt = _get_video_prompt(item)
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
        # 用 .get 归一化：缺失 unit_id 的坏数据（Agent 可裸写 script JSON）会被 from_request
        # 当作空 resource_id 拒绝并走下面的跳过分支，而不是在此抛 KeyError 中断整批。
        unit_id = str(unit.get("unit_id") or "")
        if unit_id in skip_set:
            continue
        if not unit.get("shots"):
            log.append(f"⚠️  {unit_id} 没有 shots，跳过")
            continue
        # prompt 由 shots[*].text 拼接，经统一守卫点做空提示词结构校验（见 ADR-0001）；
        # 任一 unit 不合法（空提示词、或 from_request 对空 resource_id 抛的裸 ValueError）
        # 都跳过并告警，与「没有 shots」一致，不让一个坏 unit 中断整批。
        # 注意 TaskSpecValidationError 是 ValueError 子类，捕 ValueError 同时覆盖两者。
        try:
            spec = TaskSpec.from_request(
                task_type="reference_video",
                media_type="video",
                resource_id=unit_id,
                prompt=assemble_shots_text(unit["shots"]),
                script_file=script_filename,
            )
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


def _build_ad_reference_specs(
    *,
    script: dict[str, Any],
    units: list[dict[str, Any]],
    script_filename: str,
    style: str | None,
    skip_ids: list[str] | None,
    log: list[str],
) -> tuple[list[TaskSpec], dict[str, int]]:
    """ad 派生索引 → TaskSpec。成员镜头从 shots（内容唯一真相）水合后渲染 prompt；
    索引悬空 / 空画面提示词的 unit 跳过并告警，不让一个坏 unit 中断整批
    （与 ``_build_reference_specs`` 同口径）。"""
    skip_set = set(skip_ids or [])
    specs: list[TaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = str(unit.get("unit_id") or "")
        if unit_id in skip_set:
            continue
        try:
            shots = resolve_ad_unit_shots(script, unit)
            spec = TaskSpec.from_request(
                task_type="reference_video",
                media_type="video",
                resource_id=unit_id,
                prompt=render_ad_unit_prompt(shots, style=style),
                script_file=script_filename,
            )
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
    build_specs: Callable[[list[dict[str, Any]], list[str], list[str]], tuple[list[TaskSpec], dict[str, int]]],
    reuse_existing: Callable[[dict[str, Any]], bool] | None = None,
) -> list[Path]:
    """unit 批量生成的共享骨架：checkpoint 续传 + 已产出扫描 + 入队等待。

    narration/drama（video_units 内容自包含）与 ad（reference_units 派生索引）
    仅 spec 构造不同，经 ``build_specs(units, skip_ids, log)`` 注入。

    ``reuse_existing`` 决定磁盘上已存在的 ``{unit_id}.mp4`` 能否当作该 unit 的
    现行产物复用（None 表示仅凭文件存在即复用）。ad 派生索引在成员/参考集变化
    时会重置 unit 的 generated_assets，同名旧文件已不可信，须由该判定排除。
    """
    project_dir = ctx.project_path
    ckpt_path = _episode_checkpoint_path(project_dir, episode)
    completed: list[str] = []
    started_at = datetime.now(UTC).isoformat()
    if resume:
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
            save_fn=lambda: _save_checkpoint_at(ckpt_path, completed, started_at, episode=episode),
            log=log,
        )
        if failures:
            raise RuntimeError(f"{len(failures)} 个 unit 生成失败")

    final = [p for p in ordered_paths if p is not None]
    if not final:
        raise RuntimeError("没有生成任何 video_unit")
    _clear_checkpoint_at(ckpt_path)
    return final


async def _run_reference_episode(
    *,
    ctx: ToolContext,
    script: dict[str, Any],
    script_filename: str,
    resume: bool,
    log: list[str],
) -> dict[str, Any]:
    """Run reference_video-mode generation and format the tool response.

    All 4 video handlers fall through to whole-episode reference generation
    when ``_is_reference_script`` returns True; this captures the shared tail
    (resolve episode → generate units → header + log).
    """
    episode = ProjectManager.resolve_episode_from_script(script, script_filename)
    units = script.get("video_units") or []
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")
    paths = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        log=log,
        build_specs=lambda u, skip, lg: _build_reference_specs(
            units=u, script_filename=script_filename, skip_ids=skip, log=lg
        ),
    )
    header = f"第 {episode} 集参考视频生成完成，共 {len(paths)} 个 unit"
    return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}


async def _run_ad_reference_episode(
    *,
    ctx: ToolContext,
    script_filename: str,
    resume: bool,
    log: list[str],
) -> dict[str, Any]:
    """ad + reference_video：先（重新）派生分组索引并持久化，再按 unit 批量直出。

    分组是纯函数派生（shots + 供应商时长上限 → 可复现分组）；成员与参考集未变
    的 unit 保留 generated_assets，已有产物经磁盘扫描跳过重复入队。
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

    style = project.get("style")
    paths = await _generate_reference_units(
        ctx=ctx,
        units=units,
        episode=episode,
        resume=resume,
        log=log,
        build_specs=lambda u, skip, lg: _build_ad_reference_specs(
            script=script,
            units=u,
            script_filename=script_filename,
            style=style if isinstance(style, str) else None,
            skip_ids=skip,
            log=lg,
        ),
        # sync 把成员/参考集变化的 unit 重置为待生成；旧同名产物不可复用，
        # 仅 generated_assets 仍指向产物的 unit 才按磁盘文件跳过
        reuse_existing=lambda u: bool((u.get("generated_assets") or {}).get("video_clip")),
    )
    header = f"参考直出生成完成，共 {len(paths)} 个 unit"
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
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            resume = bool(args.get("resume"))

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if _is_reference_script(script):
                return await _run_reference_episode(
                    ctx=ctx, script=script, script_filename=script_filename, resume=resume, log=log
                )
            if _is_ad_reference(ctx, script):
                return await _run_ad_reference_episode(ctx=ctx, script_filename=script_filename, resume=resume, log=log)

            episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = script.get("content_mode", "narration")
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
            specs, order_map = _build_video_specs(
                items=items,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=already_done,
                log=log,
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
        "生成单个场景/片段的视频。reference_video 模式会忽略 scene_id 转为整集生成。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_id": {"type": "string", "description": "场景或片段 ID"},
            },
            "required": ["script", "scene_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            scene_id = args["scene_id"]

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if _is_reference_script(script):
                log: list[str] = [
                    f"⚠️  reference_video 模式暂不支持单 unit 精确选择；scene_id={scene_id} 被忽略，转整集生成。"
                ]
                return await _run_reference_episode(
                    ctx=ctx, script=script, script_filename=script_filename, resume=False, log=log
                )
            if _is_ad_reference(ctx, script):
                ad_log: list[str] = [
                    f"⚠️  reference_video 模式暂不支持单 unit 精确选择；scene_id={scene_id} 被忽略，转整集生成。"
                ]
                return await _run_ad_reference_episode(
                    ctx=ctx, script_filename=script_filename, resume=False, log=ad_log
                )

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            item = next((s for s in items if s.get(id_field) == scene_id or s.get("scene_id") == scene_id), None)
            if not item:
                raise ValueError(f"场景/片段 '{scene_id}' 不存在")
            # 调用方可能用 ``scene_id`` 别名命中条目，但入队 / 文件名 / fallback
            # 必须用脚本里的规范 ``id_field`` 值，否则下游 generate_video_all 和
            # checkpoint 扫描会找不到产物。
            item_id = str(item[id_field])

            storyboard_image = item.get("generated_assets", {}).get("storyboard_image")
            if not storyboard_image:
                raise ValueError(f"场景/片段 '{item_id}' 没有分镜图，请先运行 generate_storyboards")
            if not (project_dir / storyboard_image).exists():
                raise FileNotFoundError(f"分镜图不存在: {project_dir / storyboard_image}")

            prompt = _get_video_prompt(item)
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
            return tool_error("generate_video_scene", exc)

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
                }
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        log: list[str] = []
        try:
            script_filename = validate_script_filename(args["script"])
            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if _is_reference_script(script):
                return await _run_reference_episode(
                    ctx=ctx, script=script, script_filename=script_filename, resume=False, log=log
                )
            if _is_ad_reference(ctx, script):
                return await _run_ad_reference_episode(ctx=ctx, script_filename=script_filename, resume=False, log=log)

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = script.get("content_mode", "narration")
            pending = [it for it in items if not (it.get("generated_assets") or {}).get("video_clip")]
            if not pending:
                return {"content": [{"type": "text", "text": "✨ 所有场景/片段的视频都已生成"}]}

            specs, _order_map = _build_video_specs(
                items=pending,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=None,
                log=log,
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
        "生成指定多个场景的视频（独立 checkpoint，按 scene_ids 哈希）。reference_video 模式会忽略 scene_ids 转整集生成。",
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
                    "description": "场景或片段 ID 列表",
                },
                "resume": {"type": "boolean", "description": "是否从上次中断处继续"},
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

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if _is_reference_script(script):
                log.append(
                    f"⚠️  reference_video 模式暂不支持多 unit 精确选择；scene_ids={','.join(scene_ids)} 被忽略，转整集生成。"
                )
                return await _run_reference_episode(
                    ctx=ctx, script=script, script_filename=script_filename, resume=resume, log=log
                )
            if _is_ad_reference(ctx, script):
                log.append(
                    f"⚠️  reference_video 模式暂不支持多 unit 精确选择；scene_ids={','.join(scene_ids)} 被忽略，转整集生成。"
                )
                return await _run_ad_reference_episode(ctx=ctx, script_filename=script_filename, resume=resume, log=log)

            items, id_field, _chars, _scenes, _props = get_storyboard_items(script)
            content_mode = script.get("content_mode", "narration")

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
            specs, order_map = _build_video_specs(
                items=selected,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                skip_ids=already_done,
                log=log,
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
