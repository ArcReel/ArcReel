"""v4→v5 迁移：生成路线收缩为二值，宫格降为 grid_storyboard 开关。

四项职责：
- ``generation_mode == "grid"`` 重编码为 ``storyboard + grid_storyboard=true``（宫格不是路线，
  只是分镜图的生产方式）；
- ``generation_mode`` 缺失或非二值脏值补写显式 ``storyboard``（与迁移前读侧对未知值回退
  storyboard 的口径一致，行为不变）；
- 剔除全部集级 ``episodes[].generation_mode`` 覆盖字段（路线一律按项目定轴）；
- 不触碰剧本文件。
"""

from __future__ import annotations

from pathlib import Path

from lib.json_io import atomic_write_json, load_json

_ROUTE_MODES = {"storyboard", "reference_video"}


def migrate_project_dict(project: dict) -> dict:
    """纯函数：把 v4 形态的 project dict 转为 v5 形态。幂等。

    不改 schema_version（由文件级 migrate 提交时写入）。
    """
    data = dict(project)

    mode = data.get("generation_mode")
    if mode == "grid":
        data["generation_mode"] = "storyboard"
        data["grid_storyboard"] = True
    elif mode not in _ROUTE_MODES:
        data["generation_mode"] = "storyboard"
    data.setdefault("grid_storyboard", False)

    episodes = data.get("episodes")
    if isinstance(episodes, list):
        data["episodes"] = [
            {k: v for k, v in ep.items() if k != "generation_mode"} if isinstance(ep, dict) else ep for ep in episodes
        ]

    return data


def migrate_v4_to_v5(project_dir: Path) -> None:
    """v4→v5 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # 与 runner 的版本读取同口径做 int 归一化：历史 project.json 可能存字符串版本号
    if int(data.get("schema_version") or 0) >= 5:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = 5
    atomic_write_json(pj, migrated)
