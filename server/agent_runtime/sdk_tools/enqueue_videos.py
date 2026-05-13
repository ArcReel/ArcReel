"""SDK MCP tools for video generation (episode / scene / all / selected)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait,
    enqueue_and_wait,
)
from lib.project_manager import ProjectManager
from lib.prompt_utils import is_structured_video_prompt, video_prompt_to_yaml
from server.agent_runtime.sdk_tools._context import ToolContext, validate_script_filename


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


def _items_from_script(script: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    content_mode = script.get("content_mode", "narration")
    if content_mode == "narration" and "segments" in script:
        return script["segments"], "segment_id", "characters_in_segment"
    return script.get("scenes", []), "scene_id", "characters_in_scene"


def _is_reference_script(script: dict[str, Any]) -> bool:
    if script.get("content_mode") == "reference_video":
        return True
    return bool(script.get("video_units"))


def _get_supported_durations(project: dict[str, Any]) -> list[int]:
    durations = project.get("_supported_durations")
    if durations and isinstance(durations, list):
        return durations
    video_backend = project.get("video_backend")
    if video_backend and isinstance(video_backend, str) and "/" in video_backend:
        try:
            from lib.config.registry import PROVIDER_REGISTRY

            provider_id, model_id = video_backend.split("/", 1)
            provider_meta = PROVIDER_REGISTRY.get(provider_id)
            if provider_meta:
                model_info = provider_meta.models.get(model_id)
                if model_info and model_info.supported_durations:
                    return list(model_info.supported_durations)
        except ImportError:
            pass
    raise ValueError(
        "supported_durations 无法解析：project 缺 _supported_durations 且 video_backend 不在 PROVIDER_REGISTRY 中"
    )


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def _checkpoint_path(project_dir: Path, episode: int) -> Path:
    return project_dir / "videos" / f".checkpoint_ep{episode}.json"


def _load_checkpoint(project_dir: Path, episode: int) -> dict[str, Any] | None:
    p = _checkpoint_path(project_dir, episode)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save_checkpoint(project_dir: Path, episode: int, completed: list[str], started_at: str) -> None:
    p = _checkpoint_path(project_dir, episode)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "episode": episode,
                "completed_scenes": completed,
                "started_at": started_at,
                "updated_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_checkpoint(project_dir: Path, episode: int) -> None:
    p = _checkpoint_path(project_dir, episode)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# Spec / scan helpers (平移自原 generate_video.py)
# ---------------------------------------------------------------------------


def _build_video_specs(
    *,
    items: list[dict[str, Any]],
    id_field: str,
    content_mode: str,
    script_filename: str,
    project_dir: Path,
    project: dict[str, Any] | None,
    skip_ids: list[str] | None,
    log: list[str],
) -> tuple[list[BatchTaskSpec], dict[str, int]]:
    proj = project or {}
    item_type = "片段" if content_mode == "narration" else "场景"
    default_duration = proj.get("default_duration") or (4 if content_mode == "narration" else 8)
    supported = _get_supported_durations(proj)
    skip_set = set(skip_ids or [])

    specs: list[BatchTaskSpec] = []
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

        duration = item.get("duration_seconds", default_duration)
        if duration not in supported:
            raise ValueError(f"duration={duration}s 不在模型 supported_durations={supported} 内")

        specs.append(
            BatchTaskSpec(
                task_type="video",
                media_type="video",
                resource_id=item_id,
                payload={
                    "prompt": prompt,
                    "script_file": script_filename,
                    "duration_seconds": int(duration),
                },
                script_file=script_filename,
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
) -> tuple[list[BatchTaskSpec], dict[str, int]]:
    skip_set = set(skip_ids or [])
    specs: list[BatchTaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = unit["unit_id"]
        if unit_id in skip_set:
            continue
        if not unit.get("shots"):
            log.append(f"⚠️  {unit_id} 没有 shots，跳过")
            continue
        specs.append(
            BatchTaskSpec(
                task_type="reference_video",
                media_type="video",
                resource_id=unit_id,
                payload={"script_file": script_filename},
                script_file=script_filename,
            )
        )
        order_map[unit_id] = idx
    return specs, order_map


def _scan_completed_items(
    items: list[dict[str, Any]],
    id_field: str,
    completed_scenes: list[str],
    videos_dir: Path,
) -> tuple[list[Path | None], list[str]]:
    ordered_paths: list[Path | None] = [None] * len(items)
    already_done: list[str] = []
    for idx, item in enumerate(items):
        item_id = item.get(id_field, item.get("scene_id", f"item_{idx}"))
        video_output = videos_dir / f"scene_{item_id}.mp4"
        if item_id in completed_scenes and video_output.exists():
            ordered_paths[idx] = video_output
            already_done.append(item_id)
        elif item_id in completed_scenes:
            completed_scenes.remove(item_id)
    return ordered_paths, already_done


async def _submit_with_checkpoint(
    *,
    project_name: str,
    project_dir: Path,
    specs: list[BatchTaskSpec],
    order_map: dict[str, int],
    ordered_paths: list[Path | None],
    completed: list[str],
    save_fn: Callable[[], None],
    log: list[str],
) -> list[BatchTaskResult]:
    """Run a batch and update checkpoint per success. Returns failures."""

    def on_success(br: BatchTaskResult) -> None:
        result = br.result or {}
        relative_path = result.get("file_path") or f"videos/scene_{br.resource_id}.mp4"
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


# ---------------------------------------------------------------------------
# Episode / scene / all / selected handlers
# ---------------------------------------------------------------------------


async def _generate_reference_episode(
    *,
    ctx: ToolContext,
    script: dict[str, Any],
    script_filename: str,
    episode: int,
    resume: bool,
    log: list[str],
) -> list[Path]:
    project_dir = ctx.project_path
    units = script.get("video_units") or []
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")

    completed: list[str] = []
    started_at = datetime.now().isoformat()
    if resume:
        ckpt = _load_checkpoint(project_dir, episode)
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
        if candidate.exists():
            ordered_paths[idx] = candidate
            already_done.append(unit_id)
            if unit_id not in completed:
                completed.append(unit_id)
        elif unit_id in completed:
            completed.remove(unit_id)

    specs, order_map = _build_reference_specs(
        units=units,
        script_filename=script_filename,
        skip_ids=already_done,
        log=log,
    )
    if specs:
        failures = await _submit_with_checkpoint(
            project_name=ctx.project_name,
            project_dir=project_dir,
            specs=specs,
            order_map=order_map,
            ordered_paths=ordered_paths,
            completed=completed,
            save_fn=lambda: _save_checkpoint(project_dir, episode, completed, started_at),
            log=log,
        )
        if failures:
            raise RuntimeError(f"{len(failures)} 个 unit 生成失败")

    final = [p for p in ordered_paths if p is not None]
    if not final:
        raise RuntimeError("没有生成任何 video_unit")
    _clear_checkpoint(project_dir, episode)
    return final


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
            episode = ProjectManager.resolve_episode_from_script(script, script_filename)

            if _is_reference_script(script):
                paths = await _generate_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    episode=episode,
                    resume=resume,
                    log=log,
                )
                header = f"第 {episode} 集参考视频生成完成，共 {len(paths)} 个 unit"
                return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}

            project = ctx.pm.load_project(ctx.project_name)
            content_mode = script.get("content_mode", "narration")
            items, id_field, _ = _items_from_script(script)
            if not items:
                raise ValueError(f"第 {episode} 集剧本为空：{script_filename}")

            completed: list[str] = []
            started_at = datetime.now().isoformat()
            if resume:
                ckpt = _load_checkpoint(project_dir, episode)
                if ckpt:
                    completed = ckpt.get("completed_scenes", [])
                    started_at = ckpt.get("started_at", started_at)

            videos_dir = project_dir / "videos"
            videos_dir.mkdir(parents=True, exist_ok=True)
            ordered_paths, already_done = _scan_completed_items(items, id_field, completed, videos_dir)
            specs, order_map = _build_video_specs(
                items=items,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
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
                    save_fn=lambda: _save_checkpoint(project_dir, episode, completed, started_at),
                    log=log,
                )
                if failures:
                    raise RuntimeError(f"{len(failures)} 个视频生成失败（使用 resume=true 续传）")

            scene_videos = [p for p in ordered_paths if p is not None]
            _clear_checkpoint(project_dir, episode)
            header = f"第 {episode} 集视频生成完成，共 {len(scene_videos)} 个片段"
            return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": "\n".join(["generate_video_episode 失败: " + str(exc), *log])}],
                "is_error": True,
            }

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
                episode = ProjectManager.resolve_episode_from_script(script, script_filename)
                paths = await _generate_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    episode=episode,
                    resume=False,
                    log=log,
                )
                header = f"第 {episode} 集参考视频生成完成，共 {len(paths)} 个 unit"
                return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}

            project = ctx.pm.load_project(ctx.project_name)
            content_mode = script.get("content_mode", "narration")
            items, id_field, _ = _items_from_script(script)
            item = next((s for s in items if s.get(id_field) == scene_id or s.get("scene_id") == scene_id), None)
            if not item:
                raise ValueError(f"场景/片段 '{scene_id}' 不存在")

            storyboard_image = item.get("generated_assets", {}).get("storyboard_image")
            if not storyboard_image:
                raise ValueError(f"场景/片段 '{scene_id}' 没有分镜图，请先运行 generate_storyboards")
            if not (project_dir / storyboard_image).exists():
                raise FileNotFoundError(f"分镜图不存在: {project_dir / storyboard_image}")

            prompt = _get_video_prompt(item)
            default_duration = project.get("default_duration") or (4 if content_mode == "narration" else 8)
            duration = item.get("duration_seconds", default_duration)
            supported = _get_supported_durations(project)
            if duration not in supported:
                raise ValueError(f"duration={duration}s 不在模型 supported_durations={supported} 内")

            queued = await enqueue_and_wait(
                project_name=ctx.project_name,
                task_type="video",
                media_type="video",
                resource_id=scene_id,
                payload={
                    "prompt": prompt,
                    "script_file": script_filename,
                    "duration_seconds": int(duration),
                },
                script_file=script_filename,
                source="skill",
            )
            result = queued.get("result") or {}
            rel = result.get("file_path") or f"videos/scene_{scene_id}.mp4"
            output_path = project_dir / rel
            return {"content": [{"type": "text", "text": f"✅ 视频已保存: {output_path}"}]}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"generate_video_scene 失败: {exc}"}],
                "is_error": True,
            }

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
                episode = ProjectManager.resolve_episode_from_script(script, script_filename)
                paths = await _generate_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    episode=episode,
                    resume=False,
                    log=log,
                )
                header = f"第 {episode} 集参考视频生成完成，共 {len(paths)} 个 unit"
                return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}

            project = ctx.pm.load_project(ctx.project_name)
            content_mode = script.get("content_mode", "narration")
            items, id_field, _ = _items_from_script(script)
            pending = [it for it in items if not (it.get("generated_assets") or {}).get("video_clip")]
            if not pending:
                return {"content": [{"type": "text", "text": "✨ 所有场景/片段的视频都已生成"}]}

            specs, _order_map = _build_video_specs(
                items=pending,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
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
            return {
                "content": [{"type": "text", "text": "\n".join([f"generate_video_all 失败: {exc}", *log])}],
                "is_error": True,
            }

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
            scene_ids: list[str] = list(args["scene_ids"])
            resume = bool(args.get("resume"))

            project_dir = ctx.project_path
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if _is_reference_script(script):
                episode = ProjectManager.resolve_episode_from_script(script, script_filename)
                log.append(
                    f"⚠️  reference_video 模式暂不支持多 unit 精确选择；scene_ids={','.join(scene_ids)} 被忽略，转整集生成。"
                )
                paths = await _generate_reference_episode(
                    ctx=ctx,
                    script=script,
                    script_filename=script_filename,
                    episode=episode,
                    resume=resume,
                    log=log,
                )
                header = f"第 {episode} 集参考视频生成完成，共 {len(paths)} 个 unit"
                return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}

            project = ctx.pm.load_project(ctx.project_name)
            content_mode = script.get("content_mode", "narration")
            items, id_field, _ = _items_from_script(script)

            items_by_id: dict[str, dict[str, Any]] = {}
            for item in items:
                items_by_id[item.get(id_field, "")] = item
                if "scene_id" in item:
                    items_by_id[item["scene_id"]] = item

            selected: list[dict[str, Any]] = []
            for sid in scene_ids:
                if sid in items_by_id:
                    selected.append(items_by_id[sid])
                else:
                    log.append(f"⚠️  场景/片段 '{sid}' 不存在，跳过")
            if not selected:
                raise ValueError("没有找到任何有效的场景/片段")

            scenes_hash = hashlib.md5(",".join(scene_ids).encode()).hexdigest()[:8]
            checkpoint_path = project_dir / "videos" / f".checkpoint_selected_{scenes_hash}.json"
            completed: list[str] = []
            started_at = datetime.now().isoformat()
            if resume and checkpoint_path.exists():
                ckpt = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                completed = ckpt.get("completed_scenes", [])
                started_at = ckpt.get("started_at", started_at)

            videos_dir = project_dir / "videos"
            videos_dir.mkdir(parents=True, exist_ok=True)
            ordered_paths, already_done = _scan_completed_items(selected, id_field, completed, videos_dir)
            specs, order_map = _build_video_specs(
                items=selected,
                id_field=id_field,
                content_mode=content_mode,
                script_filename=script_filename,
                project_dir=project_dir,
                project=project,
                skip_ids=already_done,
                log=log,
            )

            def _save() -> None:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint_path.write_text(
                    json.dumps(
                        {
                            "scene_ids": scene_ids,
                            "completed_scenes": completed,
                            "started_at": started_at,
                            "updated_at": datetime.now().isoformat(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            if specs:
                failures = await _submit_with_checkpoint(
                    project_name=ctx.project_name,
                    project_dir=project_dir,
                    specs=specs,
                    order_map=order_map,
                    ordered_paths=ordered_paths,
                    completed=completed,
                    save_fn=_save,
                    log=log,
                )
                if failures:
                    raise RuntimeError(f"{len(failures)} 个视频生成失败（使用 resume=true 续传）")

            final_results = [p for p in ordered_paths if p is not None]
            if checkpoint_path.exists():
                checkpoint_path.unlink()
            header = f"generate_video_selected 完成：{len(final_results)} 个"
            return {"content": [{"type": "text", "text": "\n".join([header, *log])}]}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": "\n".join([f"generate_video_selected 失败: {exc}", *log])}],
                "is_error": True,
            }

    return _handler


__all__ = [
    "generate_video_episode_tool",
    "generate_video_scene_tool",
    "generate_video_all_tool",
    "generate_video_selected_tool",
]
