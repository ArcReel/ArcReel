"""v3→v4 迁移：旧任务级文本 backend 键 → 档位键；版本守卫、幂等、新旧值状态组合。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations.v3_to_v4_text_tiers import migrate_project_dict, migrate_v3_to_v4


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestMigrateProjectDict:
    def test_script_to_complex(self):
        after = migrate_project_dict({"text_backend_script": "gemini-aistudio/pro"})
        assert after["text_backend_complex"] == "gemini-aistudio/pro"
        assert "text_backend_script" not in after
        assert "text_backend_simple" not in after

    @pytest.mark.parametrize(
        ("overview", "style", "expected_simple"),
        [
            ("gemini-aistudio/a", None, "gemini-aistudio/a"),
            (None, "gemini-aistudio/b", "gemini-aistudio/b"),
            ("gemini-aistudio/a", "gemini-aistudio/a", "gemini-aistudio/a"),
            # 两者不同：取 style 的值（保 vision）
            ("gemini-aistudio/a", "gemini-aistudio/b", "gemini-aistudio/b"),
        ],
    )
    def test_simple_combinations(self, overview, style, expected_simple):
        data = {}
        if overview is not None:
            data["text_backend_overview"] = overview
        if style is not None:
            data["text_backend_style"] = style
        after = migrate_project_dict(data)
        assert after["text_backend_simple"] == expected_simple
        assert "text_backend_overview" not in after
        assert "text_backend_style" not in after

    def test_no_legacy_keys_is_noop(self):
        after = migrate_project_dict({"title": "T"})
        assert after == {"title": "T"}

    def test_does_not_overwrite_existing_tier_keys(self):
        after = migrate_project_dict(
            {
                "text_backend_script": "gemini-aistudio/old",
                "text_backend_style": "gemini-aistudio/old-style",
                "text_backend_complex": "gemini-aistudio/new",
                "text_backend_simple": "gemini-aistudio/new-simple",
            }
        )
        assert after["text_backend_complex"] == "gemini-aistudio/new"
        assert after["text_backend_simple"] == "gemini-aistudio/new-simple"
        assert "text_backend_script" not in after

    def test_dirty_values_treated_as_unset(self):
        """null / 空串 / 非字符串脏值不产出档位键，但旧键仍被清除。"""
        after = migrate_project_dict(
            {"text_backend_script": None, "text_backend_overview": "  ", "text_backend_style": 42}
        )
        assert "text_backend_complex" not in after
        assert "text_backend_simple" not in after
        assert "text_backend_script" not in after
        assert "text_backend_overview" not in after
        assert "text_backend_style" not in after

    def test_unrelated_fields_preserved(self):
        after = migrate_project_dict(
            {"title": "T", "video_backend": "ark/m", "text_backend_script": "gemini-aistudio/p"}
        )
        assert after["title"] == "T"
        assert after["video_backend"] == "ark/m"

    def test_idempotent(self):
        once = migrate_project_dict({"text_backend_script": "gemini-aistudio/p", "title": "T"})
        twice = migrate_project_dict(once)
        assert twice == once


class TestMigrateV3ToV4File:
    def test_bumps_schema_version_and_migrates(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 3, "text_backend_script": "gemini-aistudio/pro"})
        migrate_v3_to_v4(d)
        data = _load(d)
        assert data["schema_version"] == 4
        assert data["text_backend_complex"] == "gemini-aistudio/pro"
        assert "text_backend_script" not in data

    def test_version_guard_skips_already_v4(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 4, "text_backend_script": "gemini-aistudio/pro"})
        migrate_v3_to_v4(d)
        data = _load(d)
        # 已是 v4：不动（旧键残留由该版本自身负责，不重复迁移）
        assert data["text_backend_script"] == "gemini-aistudio/pro"

    def test_missing_project_json_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        migrate_v3_to_v4(d)
        assert not (d / "project.json").exists()

    def test_string_schema_version_is_normalized(self, tmp_path: Path):
        """历史 project.json 可能存字符串版本号，守卫做 int 归一化而非抛 TypeError。"""
        d = _write(tmp_path, {"schema_version": "3", "text_backend_script": "gemini-aistudio/pro"})
        migrate_v3_to_v4(d)
        data = _load(d)
        assert data["schema_version"] == 4
        assert data["text_backend_complex"] == "gemini-aistudio/pro"

    def test_string_schema_version_guard_skips_v4(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": "4", "text_backend_script": "gemini-aistudio/pro"})
        migrate_v3_to_v4(d)
        data = _load(d)
        assert data["text_backend_script"] == "gemini-aistudio/pro"
