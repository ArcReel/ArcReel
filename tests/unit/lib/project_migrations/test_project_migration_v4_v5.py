"""v4→v5 迁移：生成模式二值化与宫格开关重编码；版本守卫、幂等、集级字段剔除。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations.v4_to_v5_generation_route import migrate_project_dict, migrate_v4_to_v5


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestMigrateProjectDict:
    def test_grid_recoded_to_storyboard_with_flag(self):
        after = migrate_project_dict({"generation_mode": "grid"})
        assert after["generation_mode"] == "storyboard"
        assert after["grid_storyboard"] is True

    def test_missing_mode_backfilled_as_storyboard(self):
        after = migrate_project_dict({"title": "T"})
        assert after["generation_mode"] == "storyboard"
        assert after["grid_storyboard"] is False

    @pytest.mark.parametrize("dirty", [None, "", "single", 42, [], {}, {"value": "grid"}])
    def test_dirty_mode_backfilled_as_storyboard(self, dirty):
        """非二值脏值（含不可哈希值）一律落显式 storyboard，不抛异常中断迁移。"""
        after = migrate_project_dict({"generation_mode": dirty})
        assert after["generation_mode"] == "storyboard"
        assert after["grid_storyboard"] is False

    def test_reference_video_preserved(self):
        after = migrate_project_dict({"generation_mode": "reference_video"})
        assert after["generation_mode"] == "reference_video"
        assert after["grid_storyboard"] is False

    def test_episode_level_overrides_stripped(self):
        after = migrate_project_dict(
            {
                "generation_mode": "storyboard",
                "episodes": [
                    {"episode": 1, "title": "A", "script_file": "scripts/episode_1.json", "generation_mode": "grid"},
                    {"episode": 2, "title": "B", "script_file": "scripts/episode_2.json"},
                ],
            }
        )
        assert all("generation_mode" not in ep for ep in after["episodes"])
        assert after["episodes"][0]["script_file"] == "scripts/episode_1.json"
        assert after["episodes"][1]["title"] == "B"

    def test_non_dict_episode_entries_preserved(self):
        after = migrate_project_dict({"episodes": ["dirty", {"episode": 1, "generation_mode": "grid"}]})
        assert after["episodes"][0] == "dirty"
        assert "generation_mode" not in after["episodes"][1]

    def test_unrelated_fields_preserved(self):
        after = migrate_project_dict({"generation_mode": "grid", "title": "T", "video_backend": "ark/m"})
        assert after["title"] == "T"
        assert after["video_backend"] == "ark/m"

    def test_idempotent(self):
        once = migrate_project_dict(
            {"generation_mode": "grid", "episodes": [{"episode": 1, "generation_mode": "reference_video"}]}
        )
        twice = migrate_project_dict(once)
        assert twice == once
        assert twice["generation_mode"] == "storyboard"
        assert twice["grid_storyboard"] is True


class TestMigrateV4ToV5File:
    def test_bumps_schema_version_and_migrates(self, tmp_path: Path):
        d = _write(
            tmp_path,
            {
                "schema_version": 4,
                "generation_mode": "grid",
                "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
            },
        )
        migrate_v4_to_v5(d)
        data = _load(d)
        assert data["schema_version"] == 5
        assert data["generation_mode"] == "storyboard"
        assert data["grid_storyboard"] is True

    def test_does_not_touch_script_files(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 4, "generation_mode": "grid"})
        scripts = d / "scripts"
        scripts.mkdir()
        script_body = json.dumps({"episode": 1, "generation_mode": "reference_video", "segments": []})
        (scripts / "episode_1.json").write_text(script_body, encoding="utf-8")
        migrate_v4_to_v5(d)
        assert (scripts / "episode_1.json").read_text(encoding="utf-8") == script_body

    def test_version_guard_skips_already_v5(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 5, "generation_mode": "grid"})
        migrate_v4_to_v5(d)
        data = _load(d)
        # 已是 v5：不动（该形态不应存在，但守卫职责只看版本号）
        assert data["generation_mode"] == "grid"

    def test_missing_project_json_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        migrate_v4_to_v5(d)
        assert not (d / "project.json").exists()

    def test_string_schema_version_is_normalized(self, tmp_path: Path):
        """历史 project.json 可能存字符串版本号，守卫做 int 归一化而非抛 TypeError。"""
        d = _write(tmp_path, {"schema_version": "4", "generation_mode": "grid"})
        migrate_v4_to_v5(d)
        data = _load(d)
        assert data["schema_version"] == 5
        assert data["generation_mode"] == "storyboard"
