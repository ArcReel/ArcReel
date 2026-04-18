"""参考生视频 executor。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.2
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lib.asset_types import BUCKET_KEY, SHEET_KEY
from lib.db.base import DEFAULT_USER_ID
from lib.reference_video.errors import MissingReferenceError

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


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """占位：下一个 Task 会补齐压缩 + 渲染 + backend 调用 + 更新元数据。"""
    raise NotImplementedError("execute_reference_video_task: filled in next task")
