"""v11→v12 迁移：给存量角色补空衍生表；未开启衍生能力的资产不受影响；版本守卫与幂等。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations.v11_to_v12_character_derivatives import migrate_project_dict, migrate_v11_to_v12


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestMigrateProjectDict:
    def test_characters_get_empty_derivative_table(self):
        after = migrate_project_dict({"characters": {"阿岚": {"description": "少女"}, "老陈": {"description": "老人"}}})
        assert after["characters"]["阿岚"]["derivatives"] == {}
        assert after["characters"]["老陈"]["derivatives"] == {}

    def test_existing_derivatives_preserved(self):
        table = {"战斗装": {"description": "换上黑色重甲", "character_sheet": ""}}
        after = migrate_project_dict({"characters": {"阿岚": {"description": "少女", "derivatives": table}}})
        assert after["characters"]["阿岚"]["derivatives"] == table

    def test_types_without_the_capability_are_untouched(self):
        after = migrate_project_dict(
            {
                "scenes": {"茶楼": {"description": "旧楼"}},
                "props": {"折扇": {"description": "竹骨"}},
                "products": {"面霜": {"description": "保湿"}},
            }
        )
        assert "derivatives" not in after["scenes"]["茶楼"]
        assert "derivatives" not in after["props"]["折扇"]
        assert "derivatives" not in after["products"]["面霜"]

    @pytest.mark.parametrize("characters", ["dirty", ["阿岚"], {"阿岚": "dirty"}])
    def test_unreadable_character_data_is_skipped(self, characters):
        """手编脏值不在迁移里修复，其结构错误由 DataValidator 另行报告。"""
        assert migrate_project_dict({"characters": characters})["characters"] == characters

    def test_non_object_derivative_table_preserved(self):
        """脏衍生表原样留给 DataValidator 报告，迁移不覆盖用户手写的内容。"""
        after = migrate_project_dict({"characters": {"阿岚": {"description": "少女", "derivatives": "dirty"}}})
        assert after["characters"]["阿岚"]["derivatives"] == "dirty"

    def test_unrelated_fields_preserved(self):
        after = migrate_project_dict({"title": "T", "video_backend": "ark/m"})
        assert after["title"] == "T"
        assert after["video_backend"] == "ark/m"

    def test_does_not_mutate_the_input(self):
        before = {"characters": {"阿岚": {"description": "少女"}}}
        migrate_project_dict(before)
        assert before == {"characters": {"阿岚": {"description": "少女"}}}

    def test_idempotent(self):
        once = migrate_project_dict({"characters": {"阿岚": {"description": "少女"}}})
        assert migrate_project_dict(once) == once


class TestMigrateV11ToV12File:
    def test_bumps_schema_version_and_migrates(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 11, "characters": {"阿岚": {"description": "少女"}}})
        migrate_v11_to_v12(d)
        data = _load(d)
        assert data["schema_version"] == 12
        assert data["characters"]["阿岚"]["derivatives"] == {}

    def test_skips_already_current_project(self, tmp_path: Path):
        table = {"战斗装": {"description": "换上黑色重甲", "character_sheet": ""}}
        d = _write(tmp_path, {"schema_version": 12, "characters": {"阿岚": {"derivatives": table}}})
        migrate_v11_to_v12(d)
        assert _load(d)["characters"]["阿岚"]["derivatives"] == table

    def test_string_schema_version_normalized(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": "11", "characters": {}})
        migrate_v11_to_v12(d)
        assert _load(d)["schema_version"] == 12

    def test_missing_project_file_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        migrate_v11_to_v12(d)
        assert not (d / "project.json").exists()

    def test_non_object_project_file_rejected(self, tmp_path: Path):
        d = tmp_path / "demo"
        d.mkdir()
        (d / "project.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match=r"project\.json 必须是对象"):
            migrate_v11_to_v12(d)
