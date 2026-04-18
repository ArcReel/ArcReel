"""参考生视频 executor。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.2
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY, SHEET_KEY
from lib.db.base import DEFAULT_USER_ID
from lib.image_utils import compress_image_bytes
from lib.reference_video import render_prompt_for_backend
from lib.reference_video.errors import MissingReferenceError
from lib.script_models import ReferenceResource

logger = logging.getLogger(__name__)


def _load_unit_context(
    *,
    project_path: Path,
    script_file: str,
    unit_id: str,
) -> tuple[dict, dict, dict]:
    """读取 project.json + 指定 episode 剧本 + 目标 unit。"""
    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    script_rel = script_file.removeprefix("scripts/")
    script = json.loads((project_path / "scripts" / script_rel).read_text(encoding="utf-8"))
    units = script.get("video_units") or []
    unit = next((u for u in units if u.get("unit_id") == unit_id), None)
    if unit is None:
        raise ValueError(f"unit not found: {unit_id}")
    return project, script, unit


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


# 供应商能力上限（与 Spec §附录B + PROVIDER_REGISTRY 对齐）
_PROVIDER_LIMITS: dict[tuple[str, str | None], dict[str, int]] = {
    # (provider, model_prefix) → limits；None 代表同 provider 所有模型共享
    ("gemini", "veo"): {"max_refs": 3, "max_duration": 8},
    ("openai", "sora"): {"max_refs": 1, "max_duration": 12},
    ("grok", None): {"max_refs": 7, "max_duration": 15},
    ("ark", None): {"max_refs": 9, "max_duration": 15},
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
    for src in source_paths:
        raw = src.read_bytes()
        compressed = compress_image_bytes(raw, max_long_edge=long_edge, quality=quality)
        tmp = tempfile.NamedTemporaryFile(
            prefix="refvid-",
            suffix=".jpg",
            delete=False,
        )
        try:
            tmp.write(compressed)
        finally:
            tmp.close()
        temp_paths.append(Path(tmp.name))
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
    """占位：下一个 Task 会补齐压缩 + 渲染 + backend 调用 + 更新元数据。"""
    raise NotImplementedError("execute_reference_video_task: filled in next task")
