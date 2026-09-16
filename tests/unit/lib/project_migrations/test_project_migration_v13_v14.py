"""v13→v14：剥「画风：」前缀、解析遗留短标签并展开快照；版本守卫与幂等。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations.v13_to_v14_legacy_style_values import migrate_project_dict, migrate_v13_to_v14
from lib.style_templates import resolve_template_prompt


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestStylePrefix:
    @pytest.mark.parametrize("prefixed", ["画风：真人电视剧风格，大师级构图", "画风: 真人电视剧风格，大师级构图"])
    def test_prefix_is_stripped_for_both_colons(self, prefixed: str):
        assert migrate_project_dict({"style": prefixed})["style"] == "真人电视剧风格，大师级构图"

    def test_surrounding_whitespace_is_stripped(self):
        assert migrate_project_dict({"style": "  画风: 国风3D  "})["style"] == "国风3D"

    def test_compound_word_is_not_a_prefix(self):
        """``anim_arcane`` 的「油画三渲二画风：」是复合词，不是可删前缀。"""
        style = "油画三渲二画风：参考《双城之战》(Fortiche / Arcane Style)画风"
        assert migrate_project_dict({"style": style})["style"] == style

    def test_prefix_is_stripped_even_when_a_template_is_already_selected(self):
        after = migrate_project_dict({"style": "画风：真人电视剧风格", "style_template_id": "live_premium_drama"})
        assert after["style"] == "真人电视剧风格"
        assert after["style_template_id"] == "live_premium_drama"


class TestLegacyShortLabel:
    @pytest.mark.parametrize(
        ("label", "template_id"),
        [("Photographic", "live_premium_drama"), ("Anime", "anim_kyoto"), ("3D Animation", "anim_3d_cg")],
    )
    def test_label_resolves_to_its_template_and_expands_the_snapshot(self, label: str, template_id: str):
        after = migrate_project_dict({"style": label})
        assert after["style_template_id"] == template_id
        assert after["style"] == resolve_template_prompt(template_id)

    def test_reference_image_wins_over_the_label(self):
        after = migrate_project_dict(
            {"style": "Photographic", "style_image": "reference.png", "style_description": "已分析"}
        )
        assert after["style_template_id"] is None
        assert after["style"] == ""
        assert after["style_image"] == "reference.png"

    def test_existing_template_id_keeps_the_label_untouched(self):
        """已有 ``style_template_id`` 说明用户此后选过风格，那次选择是准的。"""
        after = migrate_project_dict({"style": "Photographic", "style_template_id": "anim_kyoto"})
        assert after["style_template_id"] == "anim_kyoto"
        assert after["style"] == "Photographic"

    def test_free_text_style_is_untouched(self):
        after = migrate_project_dict({"style": "某种自由文本"})
        assert after["style"] == "某种自由文本"
        assert "style_template_id" not in after

    def test_free_text_style_whitespace_is_untouched(self):
        assert migrate_project_dict({"style": "  某种自由文本  "})["style"] == "  某种自由文本  "


class TestMigrateProjectDict:
    @pytest.mark.parametrize("style", [None, 7, ["画风：写实"]])
    def test_non_string_style_is_preserved(self, style):
        """手编脏值不在迁移里修复，其结构错误由 DataValidator 另行报告。"""
        assert migrate_project_dict({"style": style})["style"] == style

    def test_missing_style_field_is_not_added(self):
        assert "style" not in migrate_project_dict({"title": "T"})

    def test_unrelated_fields_preserved(self):
        after = migrate_project_dict({"title": "T", "style": "画风：写实", "video_backend": "ark/m"})
        assert after["title"] == "T"
        assert after["video_backend"] == "ark/m"

    def test_does_not_mutate_the_input(self):
        before = {"style": "Photographic"}
        migrate_project_dict(before)
        assert before == {"style": "Photographic"}

    def test_idempotent(self):
        once = migrate_project_dict({"style": "画风：写实"})
        assert migrate_project_dict(once) == once

    def test_idempotent_for_a_resolved_label(self):
        once = migrate_project_dict({"style": "Photographic"})
        assert migrate_project_dict(once) == once


class TestMigrateV13ToV14File:
    def test_bumps_schema_version_and_migrates(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 13, "style": "画风：写实"})
        migrate_v13_to_v14(d)
        data = _load(d)
        assert data["schema_version"] == 14
        assert data["style"] == "写实"

    def test_skips_already_current_project(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 14, "style": "画风：写实"})
        migrate_v13_to_v14(d)
        assert _load(d)["style"] == "画风：写实"

    def test_missing_project_file_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        migrate_v13_to_v14(d)
        assert not (d / "project.json").exists()

    def test_non_object_project_file_rejected(self, tmp_path: Path):
        d = tmp_path / "demo"
        d.mkdir()
        (d / "project.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match=r"project\.json 必须是对象"):
            migrate_v13_to_v14(d)

    def test_unmigratable_style_needs_no_manifest(self, tmp_path: Path):
        """风格值不变的项目不做目标态规划，因而没有产物清单也能走完这一步。"""
        d = _write(tmp_path, {"schema_version": 13, "style": "写实"})
        migrate_v13_to_v14(d)
        assert _load(d) == {"schema_version": 14, "style": "写实"}
