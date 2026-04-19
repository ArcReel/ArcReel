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

from lib.asset_types import BUCKET_KEY, SHEET_KEY
from lib.db.base import DEFAULT_USER_ID
from lib.image_utils import compress_image_bytes
from lib.reference_video import render_prompt_for_backend
from lib.reference_video.errors import MissingReferenceError, RequestPayloadTooLargeError
from lib.reference_video.limits import PROVIDER_MAX_DURATION, PROVIDER_MAX_REFS
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


# 供应商能力上限（数值来源：lib/reference_video/limits.py 单一真相源）。
# 这里保留 (provider, model_prefix) 粒度是因为同一 provider 不同模型上限一致，
# 单键即可命中；若未来出现 provider 内差异，再扩展 model_prefix。
_PROVIDER_LIMITS: dict[tuple[str, str | None], dict[str, int]] = {
    ("gemini", "veo"): {"max_refs": PROVIDER_MAX_REFS["gemini"], "max_duration": PROVIDER_MAX_DURATION["gemini"]},
    ("openai", "sora"): {"max_refs": PROVIDER_MAX_REFS["openai"], "max_duration": PROVIDER_MAX_DURATION["openai"]},
    ("grok", None): {"max_refs": PROVIDER_MAX_REFS["grok"], "max_duration": PROVIDER_MAX_DURATION["grok"]},
    ("ark", None): {"max_refs": PROVIDER_MAX_REFS["ark"], "max_duration": PROVIDER_MAX_DURATION["ark"]},
}


def _lookup_provider_limits(provider: str, model: str | None) -> dict[str, int]:
    """查找供应商 / 模型对应的参考图 + duration 上限。找不到返回空 dict（不裁剪）。"""
    provider = (provider or "").lower()
    model = (model or "").lower()
    for (p, prefix), limits in _PROVIDER_LIMITS.items():
        if p != provider:
            continue
        if prefix is None or model.startswith(prefix):
            return limits
    return {}


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
    references: list[Path],
    duration_seconds: int,
) -> tuple[list[Path], int, list[dict]]:
    """按供应商上限裁剪 references / duration；回传 warnings（i18n key + 参数）。"""
    warnings: list[dict] = []
    limits = _lookup_provider_limits(provider, model)

    new_duration = duration_seconds
    max_duration = limits.get("max_duration")
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
    max_refs = limits.get("max_refs")
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

    # 4. Provider 特判：裁 refs + duration
    base_duration = int(unit.get("duration_seconds") or 8)
    constrained_refs, effective_duration, warnings = _apply_provider_constraints(
        provider=provider_name,
        model=model_name,
        references=source_refs,
        duration_seconds=base_duration,
    )

    # 5. 渲染 prompt（@→[图N]）
    rendered_prompt = _render_unit_prompt(unit)

    # 6. 压缩到临时文件（2048px/q=85）→ 首次调用
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
            )
    finally:
        for p in tmp_refs:
            with contextlib.suppress(Exception):
                p.unlink(missing_ok=True)

    # 7. 首帧缩略图
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

    # 8. 更新 unit.generated_assets（简单读改写 episode script）
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
