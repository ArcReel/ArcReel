"""v2→v3 迁移：分集账本版本盖章。

只写 schema_version，episodes 内容逐字不动。v2 项目的分集条目没有位置记录
（source_range），账本坐标绑定的是具体源文内容，机械反推出的坐标不足以承担
「切法调整」的重造职责——升级后这些集照常消费（剧本 / 媒体 / 状态 / 导出不看
位置记录），要重新规划则走一次全量重置（``reset_episode_planning``）进入新机制。
"""

from __future__ import annotations

from pathlib import Path

from lib.json_io import atomic_write_json, load_json


def migrate_v2_to_v3(project_dir: Path) -> None:
    """v2→v3 文件级迁移。单次原子写，天然崩溃可重试（要么旧值要么新值，无半态）。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # 与 runner 的版本读取同口径做 int 归一化：历史 project.json 可能存字符串版本号
    if int(data.get("schema_version") or 0) >= 3:
        return
    data["schema_version"] = 3
    atomic_write_json(pj, data)
