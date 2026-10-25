"""v18→v19 迁移：每集记下条目编号用过的最大号，此后的新增不再取回删掉的编号（#3430）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.edit_timeline import EditTimelineService
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v18_to_v19_item_id_high_water import migrate_v18_to_v19
from lib.script.script_editor import new_item_id
from tests.legacy_project_shapes import write_legacy_item_id_reuse_project


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _structure_bytes(project_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project_dir).as_posix(): path.read_bytes()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file() and path.name != "project.json" and ".bak." not in path.name
    }


async def test_schema_18_project_records_the_highest_number_without_renaming_anything(tmp_path: Path) -> None:
    project_dir = write_legacy_item_id_reuse_project(tmp_path / "projects")
    legacy = _read_json(project_dir / "project.json")
    assert legacy["schema_version"] == 18
    files_before = _structure_bytes(project_dir)

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json") == {
        **legacy,
        # 剧本到 E1S2，版本历史到 E1S5，删除记录到 E1S7，剪辑时间线的片段到 E1S9
        "episodes": [{**entry, "item_id_high_water": 9} for entry in legacy["episodes"]],
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    assert _structure_bytes(project_dir) == files_before

    pm = ProjectManager(tmp_path)
    # 样本剧本按当前模型自检不过，批量编辑会拒绝；直接走同一取号规则追加一条（批量编辑的发号见其测试）
    with pm.locked_script(project_dir.name, "episode_1.json", validate=False) as script:
        floor = pm.item_id_floor(pm.load_project(project_dir.name), script, 1)
        added = {**script["segments"][-1], "segment_id": new_item_id(script, floor=floor), "generated_assets": {}}
        script["segments"].append(added)
    assert added["segment_id"] == "E1S10"
    assert pm.reserve_item_ids(project_dir.name, 1, "segments", 1) == ["E1S11"]
    readout = await EditTimelineService(pm).read(project_dir.name, "tl-00000e19")
    statuses = {clip.id: (clip.unit_id, clip.status) for clip in readout.clips}
    assert statuses["c2"] == ("E1S9", "unit_deleted")


def test_rerunning_the_step_keeps_the_recorded_number(tmp_path: Path) -> None:
    project_dir = write_legacy_item_id_reuse_project(tmp_path)
    migrate_v18_to_v19(project_dir)
    migrated = (project_dir / "project.json").read_bytes()

    migrate_v18_to_v19(project_dir)

    assert (project_dir / "project.json").read_bytes() == migrated
