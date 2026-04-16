"""v0→v1 迁移：拆分 clues → scenes + props；删除 importance；级联剧本 JSON。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _split_clues(clues: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    scenes: dict[str, dict] = {}
    props: dict[str, dict] = {}
    for name, data in clues.items():
        clue_type = (data.get("type") or "prop").lower()
        new_item: dict[str, Any] = {
            "description": data.get("description", ""),
        }
        # 保留生成图路径但换字段名
        sheet = data.get("clue_sheet")
        if clue_type == "location":
            if sheet:
                new_item["scene_sheet"] = sheet.replace("clues/", "scenes/", 1)
            scenes[name] = new_item
        else:
            if sheet:
                new_item["prop_sheet"] = sheet.replace("clues/", "props/", 1)
            props[name] = new_item
    return scenes, props


def _relocate_clue_files(project_dir: Path, old_clues: dict[str, dict]) -> None:
    clues_dir = project_dir / "clues"
    if not clues_dir.exists():
        return
    scenes_dir = project_dir / "scenes"
    props_dir = project_dir / "props"
    scenes_dir.mkdir(exist_ok=True)
    props_dir.mkdir(exist_ok=True)

    for name, data in old_clues.items():
        clue_type = (data.get("type") or "prop").lower()
        target = scenes_dir if clue_type == "location" else props_dir
        for ext in ("png", "jpg", "jpeg", "webp"):
            src = clues_dir / f"{name}.{ext}"
            if src.exists():
                shutil.move(str(src), str(target / f"{name}.{ext}"))

    # 清理空 clues 目录（即使有残余未知文件也保留，避免误删）
    try:
        clues_dir.rmdir()
    except OSError:
        pass

    # versions/clues 同样按原 clue type 分流
    versions_clues = project_dir / "versions" / "clues"
    if versions_clues.exists():
        for name, data in old_clues.items():
            clue_type = (data.get("type") or "prop").lower()
            target_versions = project_dir / "versions" / ("scenes" if clue_type == "location" else "props")
            target_versions.mkdir(parents=True, exist_ok=True)
            for file in versions_clues.glob(f"{name}*"):
                shutil.move(str(file), str(target_versions / file.name))
        try:
            versions_clues.rmdir()
        except OSError:
            pass


def _migrate_scripts(project_dir: Path, old_clues: dict[str, dict]) -> None:
    """把剧本里每条 scene/segment 的 clues[] 拆为 scenes[] + props[]"""
    scripts_dir = project_dir / "scripts"
    if not scripts_dir.exists():
        return

    def kind(clue_name: str) -> str:
        data = old_clues.get(clue_name, {})
        return "scene" if (data.get("type") or "prop").lower() == "location" else "prop"

    for sp in scripts_dir.glob("*.json"):
        try:
            data = _load_json(sp)
        except Exception:
            continue
        if data.get("schema_version", 0) >= 1:
            continue

        for bucket_key in ("scenes", "segments"):
            items = data.get(bucket_key) or []
            for item in items:
                old = item.pop("clues", None)
                if old is None:
                    continue
                scenes_list: list[str] = []
                props_list: list[str] = []
                for nm in old:
                    (scenes_list if kind(nm) == "scene" else props_list).append(nm)
                item["scenes"] = scenes_list
                item["props"] = props_list

        data["schema_version"] = 1
        _atomic_write_json(sp, data)


def _reconstruct_old_clues_from_v1(data: dict) -> dict[str, dict]:
    """从 v1 schema 反推 old_clues（用于半迁移项目的自愈补跑）。

    scene → type=location，prop → type=prop；clue_sheet 字段无法恢复（已删除），但
    _relocate_clue_files 仅依赖 name + type 分流文件，不依赖 clue_sheet。
    """
    old: dict[str, dict] = {}
    for name in data.get("scenes") or {}:
        old[name] = {"type": "location"}
    for name in data.get("props") or {}:
        old[name] = {"type": "prop"}
    return old


def migrate_v0_to_v1(project_dir: Path) -> None:
    """v0→v1 迁移。幂等 + 半迁移自愈。

    顺序：先搬文件 → 再改剧本 → 最后升 schema_version。这样任一步崩溃时
    schema_version 仍是 0，下次启动会重新尝试（不会因"已升版本"而跳过丢图）。
    """
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = _load_json(pj)
    current_version = data.get("schema_version", 0)

    # 自愈：schema_version>=1 但 clues/ 仍存在 → 补跑文件/剧本迁移
    if current_version >= 1:
        clues_dir = project_dir / "clues"
        if clues_dir.is_dir() and any(clues_dir.iterdir()):
            old_clues = _reconstruct_old_clues_from_v1(data)
            _relocate_clue_files(project_dir, old_clues)
            _migrate_scripts(project_dir, old_clues)
        return

    old_clues: dict[str, dict] = data.get("clues") or {}
    scenes, props = _split_clues(old_clues)

    # 1. 先搬文件（任一步失败时 schema_version 仍为 0，重启会重试）
    _relocate_clue_files(project_dir, old_clues)

    # 2. 级联剧本
    _migrate_scripts(project_dir, old_clues)

    # 3. 最后更新 project.json（原子写，schema_version 升级作为"提交"标志）
    data["scenes"] = scenes
    data["props"] = props
    data.pop("clues", None)
    data["schema_version"] = 1
    _atomic_write_json(pj, data)
