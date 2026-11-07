"""Concurrent read-modify-write protection for reference-to-video scripts.

Verify `ProjectManager.locked_script` preserves updates when two kinds of concurrent writer modify `video_units`:
  1. Append new units (router add_unit).
  2. Write generated_assets into existing units (executor _update_unit_assets).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION


def _seed_reference_video_project(pm: ProjectManager, name: str, n_units: int) -> None:
    """Create a project with one reference-to-video episode_1 script, prefilled with n_units units."""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
            # Production projects always use current schemas; script saves register artifacts in manifests.
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
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
                "text": "镜头",
                "duration_seconds": 4,
                "generated_assets": {"video_clip": None, "status": "pending"},
            }
            for i in range(1, n_units + 1)
        ],
    }
    pm.save_script(name, script, "episode_1.json", validate=False)  # Simple units for concurrent RMW.


class TestReferenceVideoConcurrentRMW:
    def test_concurrent_append_and_asset_writeback_preserve_all(self, tmp_path: Path) -> None:
        """Concurrent unit appends and existing-unit asset writebacks preserve both updates."""
        pm = ProjectManager(tmp_path)
        name = "proj-refvid-rmw"
        n_existing = 12
        _seed_reference_video_project(pm, name, n_existing)

        appenders = 12  # Append E1U13..E1U24.
        total = n_existing + appenders
        barrier = threading.Barrier(total)

        def _append(idx: int) -> None:
            """Simulate add_unit: fresh-load, append, and save inside locked_script."""
            barrier.wait()
            with pm.locked_script(name, "episode_1.json", validate=False) as script:
                # Access schema fields directly, failing loudly when absent; do not hide corruption with setdefault.
                script["video_units"].append(
                    {
                        "unit_id": f"E1U{idx}",
                        "text": "镜头",
                        "duration_seconds": 4,
                        "generated_assets": {"video_clip": None, "status": "pending"},
                    }
                )

        def _writeback(unit_id: str) -> None:
            """Simulate executor _update_unit_assets: locate the unit and write generated_assets."""
            barrier.wait()
            with pm.locked_script(name, "episode_1.json", validate=False) as script:
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

        # All appended units remain.
        assert len(units) == total, f"期望 {total} 个 unit，实际 {sorted(units)}"
        for i in range(n_existing + 1, total + 1):
            assert f"E1U{i}" in units, f"追加的 E1U{i} 丢失"

        # All existing-unit asset writebacks take effect.
        for i in range(1, n_existing + 1):
            ga = units[f"E1U{i}"].get("generated_assets") or {}
            assert ga.get("video_clip") == f"reference_videos/E1U{i}.mp4", f"E1U{i} 资产回写丢失：{ga}"
            assert ga.get("status") == "completed"
