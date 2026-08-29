"""v10→v11 迁移：补写项目级角色声音绑定方式，存量项目行为不变。

新项目默认按 ``voice_style`` 提示词软约束（``prompt``），参考音频改为可选增强。存量项目里
已经给角色设过 ``reference_audio`` 的，此前一路走的就是参考音频直传，补写 ``prompt`` 会让
它们下一次生成静默换一种声音口径，故按「任一角色设过参考音频 → ``reference_audio``，否则
``prompt``」补写：两条分支都如实保留迁移前的实际行为。

只改 ``project.json`` 一个字段，不触碰剧本、草稿与产物清单：绑定方式只影响下一次渲染与产物
时效判定，不改写任何已落盘产物的身份。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.character_voice import DEFAULT_CHARACTER_VOICE_BINDING, PROJECT_FIELD, VALID_CHARACTER_VOICE_BINDINGS
from lib.json_io import atomic_write_json, load_json

_TARGET_VERSION = 11


def _any_character_has_reference_audio(project: dict[str, Any]) -> bool:
    """项目里是否有任一角色设过参考音频。

    project.json 是明文文件，角色桶与条目可能被手编成非 dict；解析不出的形状一律当作「没设」，
    与迁移前读侧对同一批脏值的口径一致（读不出的 ``reference_audio`` 本就挂不上音频）。
    """
    characters = project.get("characters")
    if not isinstance(characters, dict):
        return False
    for entry in characters.values():
        if isinstance(entry, dict) and isinstance(entry.get("reference_audio"), str) and entry["reference_audio"]:
            return True
    return False


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    """纯函数：把 v10 形态的 project dict 转为 v11 形态。幂等。

    已带合法取值的项目原样保留（含手工先写好该字段的情形）；不改 schema_version（由文件级
    migrate 提交时写入）。
    """
    data = dict(project)
    existing = data.get(PROJECT_FIELD)
    if isinstance(existing, str) and existing in VALID_CHARACTER_VOICE_BINDINGS:
        return data
    data[PROJECT_FIELD] = (
        "reference_audio" if _any_character_has_reference_audio(data) else DEFAULT_CHARACTER_VOICE_BINDING
    )
    return data


def migrate_v10_to_v11(project_dir: Path) -> None:
    """v10→v11 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    if not isinstance(data, dict):
        raise ValueError("project.json 必须是对象")
    # 与 runner 的版本读取同口径做 int 归一化：历史 project.json 可能存字符串版本号
    if int(data.get("schema_version") or 0) >= _TARGET_VERSION:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = _TARGET_VERSION
    atomic_write_json(pj, migrated)


__all__ = ["migrate_project_dict", "migrate_v10_to_v11"]
