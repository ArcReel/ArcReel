"""v2→v3 迁移：episodes 列表回填为分集账本 + 顶层 planning_cursor。

回填逻辑在 ``lib.episode_ledger.backfill_episode_ledger``（可重跑纯函数，规划
工具后续吸收漂移时复用），本模块只做版本守卫与原子提交。回填对文件系统只读，
唯一写盘是一次原子替换 project.json：中途崩溃 schema_version 仍为 2，下次启动
整体重跑，无半态。``source/_remaining.txt`` 保留——旧拆分流程仍以它为下一集
源文件，物理废除随流程切换进行。
"""

from __future__ import annotations

from pathlib import Path

from lib.episode_ledger import backfill_episode_ledger
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
    migrated = backfill_episode_ledger(project_dir, data)
    migrated["schema_version"] = 3
    atomic_write_json(pj, migrated)
