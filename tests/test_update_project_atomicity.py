"""Test that concurrent update_project calls do not lose each other's writes."""

import asyncio
import json
from pathlib import Path

from lib.project_manager import ProjectManager


def _make_project(tmp_path: Path, characters: dict) -> str:
    """Create a minimal project.json and return the project name."""
    project_name = "test-proj"
    project_dir = tmp_path / project_name
    project_dir.mkdir()
    project_file = project_dir / "project.json"
    project_file.write_text(
        json.dumps(
            {
                "characters": characters,
                "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return project_name


class TestUpdateProjectAtomicity:
    """Verify update_project serialises concurrent writes correctly."""

    def test_sequential_updates_preserve_all(self, tmp_path: Path):
        """Two sequential updates should both be visible."""
        pm = ProjectManager(tmp_path)
        name = _make_project(
            tmp_path,
            {
                "alice": {"description": "A", "character_sheet": ""},
                "bob": {"description": "B", "character_sheet": ""},
            },
        )

        pm.update_project(
            name, lambda p: p["characters"]["alice"].__setitem__("character_sheet", "characters/alice.png")
        )
        pm.update_project(name, lambda p: p["characters"]["bob"].__setitem__("character_sheet", "characters/bob.png"))

        result = pm.load_project(name)
        assert result["characters"]["alice"]["character_sheet"] == "characters/alice.png"
        assert result["characters"]["bob"]["character_sheet"] == "characters/bob.png"

    def test_concurrent_updates_preserve_all(self, tmp_path: Path):
        """Simulate the race: multiple async tasks update different characters concurrently.

        Without atomic update_project, this test would fail because the last
        writer would overwrite the other's change (lost-update problem).
        """
        pm = ProjectManager(tmp_path)
        chars = {f"char_{i}": {"description": f"Desc {i}", "character_sheet": ""} for i in range(10)}
        name = _make_project(tmp_path, chars)

        async def _update(char_id: str):
            # Simulate async image generation delay
            await asyncio.sleep(0.01)
            pm.update_project(
                name,
                lambda p, cid=char_id: p["characters"][cid].__setitem__("character_sheet", f"characters/{cid}.png"),
            )

        async def _run_all():
            await asyncio.gather(*[_update(f"char_{i}") for i in range(10)])

        asyncio.run(_run_all())

        result = pm.load_project(name)
        for i in range(10):
            assert result["characters"][f"char_{i}"]["character_sheet"] == f"characters/char_{i}.png", (
                f"char_{i} character_sheet was lost — concurrent write race condition"
            )

    def test_old_read_modify_write_loses_update(self, tmp_path: Path):
        """Demonstrate the bug: old load→modify→save pattern loses updates."""
        pm = ProjectManager(tmp_path)
        name = _make_project(
            tmp_path,
            {
                "alice": {"description": "A", "character_sheet": ""},
                "bob": {"description": "B", "character_sheet": ""},
            },
        )

        # Simulate two tasks both reading the same initial version
        snapshot_a = pm.load_project(name)
        snapshot_b = pm.load_project(name)

        # Task A finishes first and writes back
        snapshot_a["characters"]["alice"]["character_sheet"] = "characters/alice.png"
        pm.save_project(name, snapshot_a)

        # Task B finishes second and writes back its stale snapshot
        snapshot_b["characters"]["bob"]["character_sheet"] = "characters/bob.png"
        pm.save_project(name, snapshot_b)

        result = pm.load_project(name)
        # Alice's change is lost! This demonstrates the bug.
        assert result["characters"]["alice"]["character_sheet"] == "", (
            "Expected alice's sheet to be lost (demonstrating the bug)"
        )
        assert result["characters"]["bob"]["character_sheet"] == "characters/bob.png"

    def test_update_project_metadata_updated(self, tmp_path: Path):
        """update_project should bump the updated_at timestamp."""
        pm = ProjectManager(tmp_path)
        name = _make_project(tmp_path, {"a": {"character_sheet": ""}})

        pm.update_project(name, lambda p: p["characters"]["a"].__setitem__("character_sheet", "x.png"))

        result = pm.load_project(name)
        assert result["metadata"]["updated_at"] != "2025-01-01"

    def test_update_project_returns_migrated_dict(self, tmp_path: Path):
        """update_project 应在单次调用内应用读时迁移并返回最终 dict（无需二次 load_project）。

        覆盖读时迁移 _migrate_legacy_style（持久化）。image_backend → 双字段拆分已下沉到
        启动期 v1→v2 项目迁移，不再走读时网。
        """
        project_name = "migrate-proj"
        project_dir = tmp_path / project_name
        project_dir.mkdir()
        (project_dir / "project.json").write_text(
            json.dumps(
                {
                    "characters": {"a": {"character_sheet": ""}},
                    "style": "Anime",  # legacy 值，应迁移为 style_template_id
                    "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        pm = ProjectManager(tmp_path)

        returned = pm.update_project(
            project_name, lambda p: p["characters"]["a"].__setitem__("character_sheet", "x.png")
        )

        # 返回值即迁移后的 dict
        assert returned["style_template_id"] == "anim_kyoto"
        assert returned["characters"]["a"]["character_sheet"] == "x.png"

        # 与随后 load_project 的结果一致（持久化迁移已落盘）
        reloaded = pm.load_project(project_name)
        assert reloaded["style_template_id"] == returned["style_template_id"]
