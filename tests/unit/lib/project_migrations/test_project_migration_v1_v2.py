"""v1→v2 迁移：legacy provider 名归一化 + image_backend 拆分；纯函数 + 幂等 + 文件级版本守卫。"""

import json
from pathlib import Path

from lib.project_migrations.v1_to_v2_normalize_providers import (
    migrate_project_dict,
    migrate_v1_to_v2,
)


class TestMigrateProjectDictPureFunction:
    def test_normalizes_legacy_provider_names(self):
        before = {
            "schema_version": 1,
            "video_backend": "seedance/seedance-1-0-pro",
            "text_backend_script": "gemini/gemini-2.5-pro",
            "text_backend_overview": "aistudio/gemini-2.5-flash",
            "text_backend_style": "vertex/gemini-2.5-pro",
        }
        after = migrate_project_dict(before)
        assert after["video_backend"] == "ark/seedance-1-0-pro"
        assert after["text_backend_script"] == "gemini-aistudio/gemini-2.5-pro"
        assert after["text_backend_overview"] == "gemini-aistudio/gemini-2.5-flash"
        assert after["text_backend_style"] == "gemini-vertex/gemini-2.5-pro"

    def test_splits_legacy_image_backend_into_two_slots(self):
        after = migrate_project_dict({"image_backend": "seedance/x"})
        assert after["image_provider_t2i"] == "ark/x"
        assert after["image_provider_i2i"] == "ark/x"
        assert "image_backend" not in after

    def test_split_does_not_overwrite_existing_slots(self):
        after = migrate_project_dict(
            {
                "image_backend": "seedance/legacy",
                "image_provider_t2i": "openai/gen-1",
            }
        )
        assert after["image_provider_t2i"] == "openai/gen-1"  # 已存在不覆盖
        assert after["image_provider_i2i"] == "ark/legacy"  # 缺失槽由 legacy 拆分填补
        assert "image_backend" not in after

    def test_normalizes_bare_provider_name_without_model(self):
        after = migrate_project_dict({"video_backend": "seedance"})
        assert after["video_backend"] == "ark"

    def test_strips_whitespace_before_normalizing(self):
        """带空白的 legacy 名也须归一化（先 strip 再比对别名表），否则残留未规范值。"""
        after = migrate_project_dict(
            {"video_backend": " seedance / seedance-1-0-pro ", "text_backend_script": " gemini "}
        )
        assert after["video_backend"] == "ark/seedance-1-0-pro"
        assert after["text_backend_script"] == "gemini-aistudio"

    def test_slash_without_model_yields_provider_only(self):
        """带斜杠但缺 model（如 "gemini /"）归一化为纯 provider，不留尾斜杠非规范串。"""
        after = migrate_project_dict({"video_backend": "seedance /", "text_backend_script": "gemini/"})
        assert after["video_backend"] == "ark"
        assert after["text_backend_script"] == "gemini-aistudio"

    def test_deletes_legacy_image_backend_key(self):
        after = migrate_project_dict({"image_backend": "openai/gpt-image-1"})
        assert "image_backend" not in after

    def test_does_not_mutate_input(self):
        before = {"image_backend": "seedance/x"}
        migrate_project_dict(before)
        assert before == {"image_backend": "seedance/x"}  # 原 dict 不变

    def test_idempotent(self):
        before = {
            "video_backend": "seedance/x",
            "image_backend": "vertex/y",
            "text_backend_script": "gemini/z",
        }
        once = migrate_project_dict(before)
        twice = migrate_project_dict(once)
        assert twice == once

    def test_canonical_input_unchanged(self):
        before = {"video_backend": "ark/seedance-1-0-pro", "image_provider_t2i": "openai/gen-1"}
        after = migrate_project_dict(before)
        assert after == before


class TestMigrateV1ToV2File:
    def _write(self, tmp_path: Path, data: dict) -> Path:
        d = tmp_path / "demo"
        d.mkdir()
        (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return d

    def test_bumps_schema_version_and_rewrites(self, tmp_path: Path):
        d = self._write(tmp_path, {"schema_version": 1, "image_backend": "seedance/x"})
        migrate_v1_to_v2(d)
        data = json.loads((d / "project.json").read_text(encoding="utf-8"))
        assert data["schema_version"] == 2
        assert data["image_provider_t2i"] == "ark/x"
        assert "image_backend" not in data

    def test_version_guard_skips_already_v2(self, tmp_path: Path):
        d = self._write(tmp_path, {"schema_version": 2, "image_backend": "seedance/x"})
        migrate_v1_to_v2(d)
        data = json.loads((d / "project.json").read_text(encoding="utf-8"))
        # 已是 v2 → 不重复迁移，legacy 字段原样保留（不会被再次处理）
        assert data["image_backend"] == "seedance/x"

    def test_missing_project_json_is_noop(self, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        migrate_v1_to_v2(tmp_path / "empty")  # 不抛错
