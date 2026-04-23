"""参考生视频 executor。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.2
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from lib.asset_types import BUCKET_KEY, SHEET_KEY
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.image_utils import compress_image_bytes
from lib.reference_video import render_prompt_for_backend
from lib.reference_video.errors import MissingReferenceError, RequestPayloadTooLargeError
from lib.script_models import ReferenceResource
from lib.thumbnail import extract_video_thumbnail
from server.services.generation_tasks import get_media_generator, get_project_manager

logger = logging.getLogger(__name__)


def _resolve_unit_references(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> list[Path]:
    """把 unit.references 转成绝对路径列表（按 references 顺序）。

    Raises:
        MissingReferenceError: 任一 reference 在 project.json 对应 bucket 缺失或 sheet 不存在。
    """
    missing: list[tuple[str, str]] = []
    resolved: list[Path] = []
    for ref in references:
        rtype = ref.get("type")
        rname = ref.get("name")
        if rtype not in BUCKET_KEY:
            missing.append((str(rtype), str(rname)))
            continue
        bucket = project.get(BUCKET_KEY[rtype]) or {}
        item = bucket.get(rname)
        sheet_rel = item.get(SHEET_KEY[rtype]) if isinstance(item, dict) else None
        if not sheet_rel:
            missing.append((rtype, rname))
            continue
        path = project_path / sheet_rel
        if not path.exists():
            missing.append((rtype, rname))
            continue
        resolved.append(path)

    if missing:
        raise MissingReferenceError(missing=missing)
    return resolved


def _compress_references_to_tempfiles(
    source_paths: list[Path],
    *,
    long_edge: int = 2048,
    quality: int = 85,
) -> list[Path]:
    """把每张 sheet 压到 JPEG bytes 并写入 NamedTemporaryFile，返回 Path 列表。

    调用方须在 finally 里对每个返回 Path 调用 .unlink(missing_ok=True)。
    """
    temp_paths: list[Path] = []
    try:
        for src in source_paths:
            tmp = tempfile.NamedTemporaryFile(
                prefix="refvid-",
                suffix=".jpg",
                delete=False,
            )
            tmp_path = Path(tmp.name)
            temp_paths.append(tmp_path)
            try:
                raw = src.read_bytes()
                compressed = compress_image_bytes(raw, max_long_edge=long_edge, quality=quality)
                tmp.write(compressed)
            finally:
                tmp.close()
    except Exception:
        # 任何阶段失败都立刻清理已创建的 temp files，避免磁盘泄露
        for p in temp_paths:
            with contextlib.suppress(Exception):
                p.unlink(missing_ok=True)
        raise
    return temp_paths


def _render_unit_prompt(unit: dict) -> str:
    """拼接 unit.shots[*].text 为单一 prompt，再用 shot_parser 把 @X 替成 [图N]。"""
    shots = unit.get("shots") or []
    raw = "\n".join(str(s.get("text", "")) for s in shots)
    references = [ReferenceResource(type=r["type"], name=r["name"]) for r in (unit.get("references") or [])]
    return render_prompt_for_backend(raw, references)


def _apply_provider_constraints(
    *,
    provider: str,
    model: str | None,
    max_refs: int | None,
    max_duration: int | None,
    references: list[Path],
    duration_seconds: int,
) -> tuple[list[Path], int, list[dict]]:
    """按供应商上限裁剪 references / duration；回传 warnings（i18n key + 参数）。

    `max_refs` / `max_duration` 由调用方从 `ConfigResolver.video_capabilities_for_project`
    取得（model 粒度，单一真相源）；任意一项为 None 表示不做对应裁剪。
    """
    warnings: list[dict] = []

    new_duration = duration_seconds
    if max_duration is not None and duration_seconds > max_duration:
        new_duration = max_duration
        warnings.append(
            {
                "key": "ref_duration_exceeded",
                "params": {
                    "duration": duration_seconds,
                    "model": model or provider,
                    "max_duration": max_duration,
                },
            }
        )

    new_refs = list(references)
    if max_refs is not None and len(references) > max_refs:
        new_refs = references[:max_refs]
        # Sora 单图走专门的 warning key，其他走通用
        if provider.lower() == "openai" and (model or "").lower().startswith("sora") and max_refs == 1:
            warnings.append({"key": "ref_sora_single_ref", "params": {}})
        else:
            warnings.append(
                {
                    "key": "ref_too_many_images",
                    "params": {
                        "count": len(references),
                        "model": model or provider,
                        "max_count": max_refs,
                    },
                }
            )

    return new_refs, new_duration, warnings


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """处理一个 reference_video unit 的生成。

    resource_id 即 unit_id（E{集}U{序号}）。
    """
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for reference_video task")

    # 1. 加载上下文（阻塞 IO，线程池）
    def _load():
        pm = get_project_manager()
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        script = pm.load_script(project_name, script_file)
        units = script.get("video_units") or []
        unit = next((u for u in units if u.get("unit_id") == resource_id), None)
        if unit is None:
            raise ValueError(f"unit not found: {resource_id}")
        return project, project_path, unit

    project, project_path, unit = await asyncio.to_thread(_load)

    # 2. 解析 references（缺图直接失败）
    source_refs = _resolve_unit_references(project, project_path, unit.get("references") or [])

    # 3. 构造 generator（拿到 video_backend 名字后才能做 provider 特判）
    generator = await get_media_generator(project_name, payload=payload, user_id=user_id)
    backend = getattr(generator, "_video_backend", None)
    provider_name = getattr(backend, "name", "") if backend else ""
    model_name = getattr(backend, "model", "") if backend else ""

    # 4. 解析 model 粒度能力上限（单一真相源：model.supported_durations）。
    #    失败时 fallback 到 None（不裁剪，交由 backend 自行报错），与
    #    ScriptGenerator._fetch_video_capabilities 的口径保持一致。
    #
    #    注意：caps 基于 `project.json.video_backend` 解析；但自定义 provider 的 model
    #    被禁用时，`_create_custom_backend` 会静默回退到默认启用 model（见
    #    server/services/generation_tasks.py:99-122）。为避免"按旧模型 clamp、按新模型生成"
    #    的错位，下面校验 caps.model 与 backend.model 是否一致；不一致就 skip clamp，
    #    把决策推给 backend 自报错。根治需要 `VideoCapabilities` 协议暴露 `max_duration`，
    #    本 PR 范围内先缓解。
    max_refs: int | None = None
    max_duration: int | None = None
    try:
        resolver = ConfigResolver(async_session_factory)
        caps = await resolver.video_capabilities_for_project(project)
        caps_model = caps.get("model")
        if model_name and caps_model and caps_model != model_name:
            logger.warning(
                "project.json video_backend model (%s) 与实际 backend model (%s) 不一致，"
                "跳过 executor clamp 以避免按错误模型裁剪（常见于自定义模型禁用回退）。",
                caps_model,
                model_name,
            )
        else:
            max_refs = caps.get("max_reference_images")
            max_duration = caps.get("max_duration")
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，跳过 executor clamp：%s", exc)

    # 5. Provider 特判：裁 refs + duration
    base_duration = int(unit.get("duration_seconds") or 8)
    constrained_refs, effective_duration, warnings = _apply_provider_constraints(
        provider=provider_name,
        model=model_name,
        max_refs=max_refs,
        max_duration=max_duration,
        references=source_refs,
        duration_seconds=base_duration,
    )

    # 5.1 解析 resolution（与分镜视频流 generation_tasks.py 保持同一优先级：
    #     project.video_model_settings[model].resolution > provider 默认值）。
    #     若直接回退到 MediaGenerator 的硬编码默认 "1080p"，会被 xai_sdk 拒绝
    #     （VideoResolutionMap 仅支持 480p/720p）。
    # TODO(T14): 改用 resolve_resolution() 替代此处的临时 fallback
    _PROVIDER_DEFAULT_RESOLUTION = {"gemini": "1080p", "ark": "720p", "grok": "720p", "openai": "720p"}
    video_model_settings = project.get("video_model_settings") or {}
    model_resolution_setting = video_model_settings.get(model_name, {}) if model_name else {}
    resolution = model_resolution_setting.get("resolution") or _PROVIDER_DEFAULT_RESOLUTION.get(provider_name, "1080p")

    # 6. 渲染 prompt（@→[图N]）。必须按 `constrained_refs` 的长度裁 `unit.references`
    #    再渲染，保证 [图N] 的 1-based 索引与 backend 实际收到的 reference_images
    #    长度严格对齐；否则裁剪后的 `@clipped_name` 会被替成 `[图N]` 指向不存在的图。
    unit_for_prompt = unit
    unit_refs = unit.get("references") or []
    if len(constrained_refs) < len(unit_refs):
        unit_for_prompt = {**unit, "references": unit_refs[: len(constrained_refs)]}
    rendered_prompt = _render_unit_prompt(unit_for_prompt)

    # 7. 压缩到临时文件（2048px/q=85）→ 首次调用
    tmp_refs: list[Path] = await asyncio.to_thread(_compress_references_to_tempfiles, constrained_refs)
    output_path: Path | None = None
    version = 0
    video_uri: str | None = None
    try:
        try:
            output_path, version, _, video_uri = await generator.generate_video_async(
                prompt=rendered_prompt,
                resource_type="reference_videos",
                resource_id=resource_id,
                reference_images=tmp_refs,
                aspect_ratio=project.get("aspect_ratio", "9:16"),
                duration_seconds=effective_duration,
                resolution=resolution,
            )
        except RequestPayloadTooLargeError:
            # 二次压缩重试（1024px/q=70）
            for p in tmp_refs:
                p.unlink(missing_ok=True)
            tmp_refs = await asyncio.to_thread(
                _compress_references_to_tempfiles,
                constrained_refs,
                long_edge=1024,
                quality=70,
            )
            warnings.append({"key": "ref_payload_too_large", "params": {}})
            output_path, version, _, video_uri = await generator.generate_video_async(
                prompt=rendered_prompt,
                resource_type="reference_videos",
                resource_id=resource_id,
                reference_images=tmp_refs,
                aspect_ratio=project.get("aspect_ratio", "9:16"),
                duration_seconds=effective_duration,
                resolution=resolution,
            )
    finally:
        for p in tmp_refs:
            with contextlib.suppress(Exception):
                p.unlink(missing_ok=True)

    # 8. 首帧缩略图
    if output_path is None:
        raise RuntimeError("generate_video_async returned None output_path")
    thumb_dir = project_path / "reference_videos" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{resource_id}.jpg"
    if await extract_video_thumbnail(output_path, thumb_path):
        thumb_rel = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        thumb_path.unlink(missing_ok=True)
        thumb_rel = None

    # 9. 更新 unit.generated_assets（简单读改写 episode script）
    def _update_unit_assets():
        pm = get_project_manager()
        script = pm.load_script(project_name, script_file)
        for u in script.get("video_units") or []:
            if u.get("unit_id") == resource_id:
                ga = u.setdefault("generated_assets", {})
                ga["video_clip"] = f"reference_videos/{resource_id}.mp4"
                if video_uri:
                    ga["video_uri"] = video_uri
                if thumb_rel:
                    ga["video_thumbnail"] = thumb_rel
                ga["status"] = "completed"
                break
        pm.save_script(project_name, script, script_file)
        return script

    await asyncio.to_thread(_update_unit_assets)

    def _latest_created_at() -> str | None:
        history = generator.versions.get_versions("reference_videos", resource_id) or {}
        versions = history.get("versions") or []
        if not versions:
            return None
        return versions[-1].get("created_at")

    created_at = await asyncio.to_thread(_latest_created_at)

    return {
        "version": version,
        "file_path": f"reference_videos/{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "reference_videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
        "warnings": warnings,
    }
