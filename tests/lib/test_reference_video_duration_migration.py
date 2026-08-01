"""per-shot 时长 → unit 时长的存量迁移。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.reference_video.duration_migration import migrate_script_unit_durations, migrate_unit_durations


def _legacy_unit(unit_id: str = "E1U1", shots: list[int] | None = None, **extra) -> dict:
    unit = {
        "unit_id": unit_id,
        "shots": [{"duration": d, "text": f"镜头{i}"} for i, d in enumerate(shots or [3, 5], start=1)],
        "references": [],
        "duration_seconds": sum(shots or [3, 5]),
        "duration_override": False,
    }
    unit.update(extra)
    return unit


@pytest.mark.unit
class TestMigrateUnitDurations:
    def test_sums_shot_durations_into_unit_and_strips_shot_field(self):
        units = [_legacy_unit(shots=[3, 5])]
        changed, warnings = migrate_unit_durations(units)
        assert changed is True
        assert warnings == []
        assert units[0]["duration_seconds"] == 8
        assert units[0]["shots"] == [{"text": "镜头1"}, {"text": "镜头2"}]
        assert "duration_override" not in units[0]

    def test_manual_duration_wins_over_shot_sum(self):
        """收编前 override 单元的 duration_seconds 就是用户手填的申请秒数，迁移不得被镜头值改写。"""
        unit = _legacy_unit(shots=[1], duration_seconds=12, duration_override=True)
        changed, _warnings = migrate_unit_durations([unit])
        assert changed is True
        assert unit["duration_seconds"] == 12

    def test_falls_back_to_shot_sum_when_unit_duration_is_dirty(self):
        unit = _legacy_unit(shots=[4, 6], duration_seconds=None)
        migrate_unit_durations([unit])
        assert unit["duration_seconds"] == 10

    def test_clamps_out_of_range_sum_with_warning(self):
        unit = _legacy_unit(shots=[15, 15, 15, 15], duration_seconds=120)
        _changed, warnings = migrate_unit_durations([unit])
        assert unit["duration_seconds"] == 60
        assert any("合理区间" in w for w in warnings)

    def test_takes_slot_when_supported_durations_given(self):
        unit = _legacy_unit(shots=[3, 5])
        _changed, warnings = migrate_unit_durations([unit], supported_durations=[4, 6, 12])
        assert unit["duration_seconds"] == 12
        assert any("档位" in w for w in warnings)

    def test_slot_clamps_down_when_over_largest(self):
        unit = _legacy_unit(shots=[15, 15])
        migrate_unit_durations([unit], supported_durations=[4, 8])
        assert unit["duration_seconds"] == 8

    def test_is_idempotent(self):
        units = [_legacy_unit()]
        migrate_unit_durations(units)
        after_first = json.dumps(units, ensure_ascii=False, sort_keys=True)
        changed, _warnings = migrate_unit_durations(units)
        assert changed is False
        assert json.dumps(units, ensure_ascii=False, sort_keys=True) == after_first

    def test_leaves_already_migrated_units_untouched(self):
        units = [{"unit_id": "E1U1", "shots": [{"text": "x"}], "duration_seconds": 8}]
        changed, warnings = migrate_unit_durations(units)
        assert (changed, warnings) == (False, [])

    def test_tolerates_dirty_shapes(self):
        units = ["not a dict", {"unit_id": "E1U1", "shots": "bad", "duration_override": False}]
        changed, _warnings = migrate_unit_durations(units)
        assert changed is True  # duration_override 被剥离
        assert migrate_unit_durations("not a list") == (False, [])

    def test_does_not_invent_duration_when_no_source(self):
        unit = {"unit_id": "E1U1", "shots": [{"duration": 0, "text": "x"}]}
        migrate_unit_durations([unit])
        assert "duration_seconds" not in unit

    def test_script_entry_only_touches_reference_skeleton(self):
        narration = {"segments": [{"segment_id": "E1S1", "duration_seconds": 4}]}
        assert migrate_script_unit_durations(narration) == (False, [])
        assert migrate_script_unit_durations("not a dict") == (False, [])


def _write_legacy_project(tmp_path: Path) -> tuple[ProjectManager, dict]:
    projects_root = tmp_path / "projects"
    (projects_root / "demo" / "scripts").mkdir(parents=True)
    (projects_root / "demo" / "project.json").write_text(
        json.dumps(
            {
                "title": "T",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            }
        ),
        encoding="utf-8",
    )
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "novel": {"title": "t", "chapter": "c"},
        "video_units": [_legacy_unit()],
    }
    (projects_root / "demo" / "scripts" / "episode_1.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8"
    )
    return ProjectManager(projects_root), script


@pytest.mark.integration
class TestLoadScriptMigration:
    def test_load_script_migrates_and_persists_once(self, tmp_path: Path):
        pm, _script = _write_legacy_project(tmp_path)
        script_path = tmp_path / "projects" / "demo" / "scripts" / "episode_1.json"

        loaded = pm.load_script("demo", "episode_1.json")
        assert loaded["video_units"][0]["shots"] == [{"text": "镜头1"}, {"text": "镜头2"}]
        assert loaded["video_units"][0]["duration_seconds"] == 8

        # 迁移结果落盘：二次加载读到的已是新格式，不再触发改写
        on_disk = json.loads(script_path.read_text(encoding="utf-8"))
        assert "duration" not in on_disk["video_units"][0]["shots"][0]
        assert "duration_override" not in on_disk["video_units"][0]
        mtime = script_path.stat().st_mtime_ns
        pm.load_script("demo", "episode_1.json")
        assert script_path.stat().st_mtime_ns == mtime

    def test_migration_does_not_touch_metadata_timestamps(self, tmp_path: Path):
        """迁移只做格式收编，不应刷新 updated_at 或写入 metadata（那是保存路径的职责）。"""
        pm, _script = _write_legacy_project(tmp_path)
        loaded = pm.load_script("demo", "episode_1.json")
        assert "metadata" not in loaded

    def test_locked_script_sees_migrated_shape(self, tmp_path: Path):
        pm, _script = _write_legacy_project(tmp_path)
        with pm.locked_script("demo", "episode_1.json") as script:
            unit = script["video_units"][0]
            assert unit["duration_seconds"] == 8
            assert all("duration" not in s for s in unit["shots"])

    def test_already_migrated_load_takes_no_script_lock(self, tmp_path: Path):
        """收编完成后读剧本回到无锁路径：迁移不应给每次读盘都加上一把排他锁。"""
        pm, _script = _write_legacy_project(tmp_path)
        scripts_dir = tmp_path / "projects" / "demo" / "scripts"
        pm.load_script("demo", "episode_1.json")
        (scripts_dir / ".episode_1.json.lock").unlink(missing_ok=True)

        pm.load_script("demo", "episode_1.json")
        assert not (scripts_dir / ".episode_1.json.lock").exists()

    def test_missing_script_fails_before_creating_lock_artifacts(self, tmp_path: Path):
        """剧本不存在时照旧 fail-loud，且不为一次落空的读盘留下目录与锁文件。"""
        pm, _script = _write_legacy_project(tmp_path)
        project_dir = tmp_path / "projects" / "demo"
        with pytest.raises(FileNotFoundError):
            pm.load_script("demo", "episode_404.json")
        assert not (project_dir / "scripts" / ".episode_404.json.lock").exists()


@pytest.mark.integration
@pytest.mark.parametrize("filename", ["episode_1.json", "scripts/episode_1.json"])
def test_load_script_migration_survives_filename_aliases(tmp_path: Path, filename: str):
    pm, _script = _write_legacy_project(tmp_path)
    loaded = pm.load_script("demo", filename)
    assert loaded["video_units"][0]["duration_seconds"] == 8
