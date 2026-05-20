"""参考视频 episode script 的并发读-改-写竞态防护测试（issue #334）。

覆盖 `ProjectManager.locked_script` 在两类并发写者同时操作 `video_units` 时不丢更新：
  1. 追加新 unit（对应 router 的 add_unit）
  2. 回写已有 unit 的 generated_assets（对应 executor 的 _update_unit_assets）
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lib.project_manager import ProjectManager


def _seed_reference_video_project(pm: ProjectManager, name: str, n_units: int) -> None:
    """创建项目 + 一个 reference_video 模式的 episode_1 剧本，预置 n_units 个 unit。"""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
            "episodes": [],
            "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
        },
    )
    script = {
        "episode": 1,
        "title": "Episode 1",
        "content_mode": "drama",
        "generation_mode": "reference_video",
        "video_units": [
            {
                "unit_id": f"E1U{i}",
                "shots": [],
                "references": [],
                "duration_seconds": 4,
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
            for i in range(1, n_units + 1)
        ],
    }
    pm.save_script(name, script, "episode_1.json")


class TestReferenceVideoConcurrentRMW:
    def test_concurrent_append_and_asset_writeback_preserve_all(self, tmp_path: Path) -> None:
        """并发追加新 unit 与回写已有 unit 资产，二者都不丢。"""
        pm = ProjectManager(tmp_path)
        name = "proj-refvid-rmw"
        n_existing = 12
        _seed_reference_video_project(pm, name, n_existing)

        appenders = 12  # 追加 E1U13..E1U24
        total = n_existing + appenders
        barrier = threading.Barrier(total)

        def _append(idx: int) -> None:
            """模拟 add_unit：locked_script 内 fresh-load → append → save。"""
            barrier.wait()
            with pm.locked_script(name, "episode_1.json") as script:
                # 直接按 schema 访问，缺字段即报错（fail-loud），不用 setdefault 掩盖损坏
                script["video_units"].append(
                    {
                        "unit_id": f"E1U{idx}",
                        "shots": [],
                        "references": [],
                        "duration_seconds": 4,
                        "generated_assets": {"video_clip": None, "status": "pending"},
                    }
                )

        def _writeback(unit_id: str) -> None:
            """模拟 executor 的 _update_unit_assets：定位 unit 写 generated_assets。"""
            barrier.wait()
            with pm.locked_script(name, "episode_1.json") as script:
                for u in script["video_units"]:
                    if u.get("unit_id") == unit_id:
                        ga = u.setdefault("generated_assets", {})
                        ga["video_clip"] = f"reference_videos/{unit_id}.mp4"
                        ga["status"] = "completed"
                        break

        with ThreadPoolExecutor(max_workers=total) as pool:
            futures = [pool.submit(_append, i) for i in range(n_existing + 1, total + 1)]
            futures += [pool.submit(_writeback, f"E1U{i}") for i in range(1, n_existing + 1)]
            for fut in as_completed(futures):
                fut.result()

        loaded = pm.load_script(name, "episode_1.json")
        units = {u["unit_id"]: u for u in loaded["video_units"]}

        # 所有追加的 unit 都在
        assert len(units) == total, f"期望 {total} 个 unit，实际 {sorted(units)}"
        for i in range(n_existing + 1, total + 1):
            assert f"E1U{i}" in units, f"追加的 E1U{i} 丢失"

        # 所有已有 unit 的资产回写都生效
        for i in range(1, n_existing + 1):
            ga = units[f"E1U{i}"].get("generated_assets") or {}
            assert ga.get("video_clip") == f"reference_videos/E1U{i}.mp4", f"E1U{i} 资产回写丢失：{ga}"
            assert ga.get("status") == "completed"
