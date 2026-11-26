"""资产级联重命名端到端测试：真实 ProjectManager 走 扫描 → 校验 → 落盘 全路径。

覆盖四类资产、各 content_mode 骨架的引用改写（引用数组 / speaker / mention）、script_plan 草稿、
关联文件与版本历史迁移、NFC/NFD 冲突拒绝与 dry-run 预览一致性，以及复用同一套扫描的删除前引用预览。speaker 与 mention 不在
DataValidator 引用扫描范围内，须直接断言改写结果，不能只看校验无新增 error。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from arcreel_market_core.validation_messages import ValidationMessage
from lib.infra.validation_messages import ValidationResult
from lib.project.asset_rename import (
    renamed_file_stem,
    rewrite_entry_paths,
    rewrite_payload_references,
)
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import _rename_agnostic_errors
from tests.asset_rename_scripts import ad_script, drama_script, make_reference_script, narration_script


class TestRewritePayloadReferences:
    def test_only_matching_type_rewritten(self) -> None:
        payload = narration_script()
        count = rewrite_payload_references(payload, "scene", "场景A", "新场景")
        assert count == 1
        segment = payload["segments"][0]
        assert segment["scenes"] == ["新场景"]
        assert segment["characters_in_segment"] == ["角色A"]

    def test_drama_speaker_rewritten_voiceover_untouched(self) -> None:
        payload = drama_script()
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        scene = payload["scenes"][0]
        assert scene["characters_in_scene"] == ["新角色"]
        assert scene["utterances"][0]["speaker"] == "新角色"
        assert scene["utterances"][1]["speaker"] is None
        assert count == 2

    def test_padded_names_share_the_asset_comparison_key(self) -> None:
        payload = drama_script()
        scene = payload["scenes"][0]
        scene["characters_in_scene"] = [" 角色A "]
        scene["utterances"][0]["speaker"] = " 角色A "

        count = rewrite_payload_references(payload, "character", "角色A", "新角色")

        assert scene["characters_in_scene"] == ["新角色"]
        assert scene["utterances"][0]["speaker"] == "新角色"
        assert count == 2

    def test_ad_dialogue_speaker_and_products(self) -> None:
        payload = ad_script()
        assert rewrite_payload_references(payload, "product", "商品A", "新商品") == 1
        assert payload["shots"][0]["products_in_shot"] == ["新商品"]
        assert rewrite_payload_references(payload, "character", "角色A", "新角色") == 2
        shot = payload["shots"][0]
        assert shot["characters_in_shot"] == ["新角色"]
        assert shot["video_prompt"]["dialogue"][0]["speaker"] == "新角色"

    def test_new_asset_targets_follow_the_type_they_point_at(self) -> None:
        """并入项的 target 指向同类资产、衍生项的指向本体角色；衍生改名不碰只写本体名的 target。"""
        payload = {
            "new_assets": [
                {"type": "character", "name": "小A", "decision": "merge", "target": "角色A"},
                {"type": "character", "name": "夜装", "decision": "derivative", "target": "角色A"},
                {"type": "scene", "name": "村头", "decision": "merge", "target": "角色A"},
                {"type": "character", "name": "新人", "decision": "register", "target": "角色A"},
            ]
        }

        assert rewrite_payload_references(payload, "character", "角色A/夜装", "角色A/夜行衣") == 0
        assert rewrite_payload_references(payload, "character", "角色A", "主角甲") == 2
        assert [item["target"] for item in payload["new_assets"]] == ["主角甲", "主角甲", "角色A", "角色A"]

    def test_narration_video_prompt_dialogue_speaker(self) -> None:
        payload = narration_script()
        payload["segments"][0]["video_prompt"]["dialogue"] = [
            {"speaker": "角色A", "line": "台词"},
            {"speaker": "路人", "line": "别的台词"},
        ]
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        dialogue = payload["segments"][0]["video_prompt"]["dialogue"]
        assert dialogue[0]["speaker"] == "新角色"
        assert dialogue[1]["speaker"] == "路人"
        assert count == 2

    def test_ad_reference_unit_mention(self) -> None:
        payload = ad_script()
        payload["video_units"] = [
            {
                "unit_id": "E1U1",
                "text": "@[商品A] 特写",
                "duration_seconds": 5,
            }
        ]
        count = rewrite_payload_references(payload, "product", "商品A", "新商品")
        assert payload["video_units"][0]["text"] == "@[新商品] 特写"
        assert count == 2

    def test_reference_units_and_mentions(self) -> None:
        payload = make_reference_script()
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        assert payload["video_units"][0]["text"] == "@[新角色] 走进 @[场景A]"
        assert count == 1

    def test_storyboard_scene_mentions(self) -> None:
        """分镜画面描述里的 ``@[名称]`` 指认引用字段里的资产，改名后须仍指向同一资产。"""
        payload = drama_script()
        payload["scenes"][0]["image_prompt"]["scene"] = "@[角色A] 站在 @[场景A] 门口"
        text_form = narration_script()
        text_form["segments"][0]["image_prompt"] = "@[角色A] 回头"

        assert rewrite_payload_references(payload, "character", "角色A", "新角色") == 3
        assert rewrite_payload_references(text_form, "character", "角色A", "新角色") == 2
        assert payload["scenes"][0]["image_prompt"]["scene"] == "@[新角色] 站在 @[场景A] 门口"
        assert text_form["segments"][0]["image_prompt"] == "@[新角色] 回头"

    def test_nfd_text_forms_matched(self) -> None:
        nfd = unicodedata.normalize("NFD", "café")
        payload = narration_script()
        payload["segments"][0]["characters_in_segment"] = [nfd]
        count = rewrite_payload_references(payload, "character", "café", "咖啡师")
        assert count == 1
        assert payload["segments"][0]["characters_in_segment"] == ["咖啡师"]

    def test_legacy_embedded_equivalent_keys_collapsed(self) -> None:
        """内嵌镜像里 NFC / NFD 并存时一并收编，留一条会顶着旧名带失效 sheet 路径残留。"""
        nfd = unicodedata.normalize("NFD", "café")
        payload = narration_script(
            characters={
                nfd: {"character_sheet": f"characters/{nfd}.png"},
                "café": {"character_sheet": "characters/café.png"},
            }
        )
        rewrite_payload_references(payload, "character", "café", "咖啡师")
        assert list(payload["characters"]) == ["咖啡师"]
        assert payload["characters"]["咖啡师"]["character_sheet"] == "characters/咖啡师.png"

    def test_legacy_embedded_encoding_only_rename_collapses(self) -> None:
        """纯改编码形式的改名：胜出 key 已等于新名，另一条等价 key 仍须一并收编。"""
        nfd = unicodedata.normalize("NFD", "café")
        payload = narration_script(
            characters={
                nfd: {"character_sheet": f"characters/{nfd}.png"},
                "café": {"character_sheet": "characters/café.png"},
            }
        )
        rewrite_payload_references(payload, "character", nfd, "café")
        assert list(payload["characters"]) == ["café"]
        assert payload["characters"]["café"]["character_sheet"] == "characters/café.png"

    def test_legacy_embedded_characters_rekeyed(self) -> None:
        payload = narration_script(characters={"角色A": {"character_sheet": "characters/角色A.png"}})
        rewrite_payload_references(payload, "character", "角色A", "新角色")
        assert "角色A" not in payload["characters"]
        assert payload["characters"]["新角色"]["character_sheet"] == "characters/新角色.png"


class TestRenameAgnosticErrors:
    """错误指纹折叠的边界：折回新名只认确定形态，不做任意子串替换。"""

    @staticmethod
    def _fingerprints(*messages: ValidationMessage) -> set[Any]:
        result = ValidationResult(valid=False, error_messages=list(messages))
        return set(_rename_agnostic_errors(result, "角色A", "甲").keys())

    def test_folds_exact_name_param(self) -> None:
        renamed = ValidationMessage("val_asset_format_object", {"asset_type": "角色", "name": "甲"})
        original = ValidationMessage("val_asset_format_object", {"asset_type": "角色", "name": "角色A"})
        assert self._fingerprints(renamed) == self._fingerprints(original)

    def test_folds_bracketed_field_path(self) -> None:
        renamed = ValidationMessage("val_invalid_path", {"field": "characters[甲].character_sheet"})
        original = ValidationMessage("val_invalid_path", {"field": "characters[角色A].character_sheet"})
        assert self._fingerprints(renamed) == self._fingerprints(original)

    def test_keeps_unrelated_value_merely_containing_the_new_name(self) -> None:
        """新名只是无关文本的子串时不得折叠，否则改写后真正新增的错误会被静默吞掉。"""
        renamed = ValidationMessage("val_invalid_path", {"field": "assets/甲板/图.png"})
        original = ValidationMessage("val_invalid_path", {"field": "assets/角色A板/图.png"})
        assert self._fingerprints(renamed) != self._fingerprints(original)


def test_path_field_outside_migrated_dirs_is_left_alone() -> None:
    """迁移范围外的路径不改写：那里的文件不会被搬，改了字段就把一条有效引用指成空。"""
    spec = ASSET_SPECS["product"]
    entry = {
        "product_sheet": "thumbnails/商品A.png",
        "reference_images": ["thumbnails/商品A_1.png", "products/商品A_1.png", "products/refs/商品A_2.png"],
    }

    assert rewrite_entry_paths(entry, spec, "商品A", "新品甲") == 1
    assert entry["product_sheet"] == "thumbnails/商品A.png"
    assert entry["reference_images"] == [
        "thumbnails/商品A_1.png",
        "products/商品A_1.png",
        "products/refs/新品甲_2.png",
    ]


class TestRenamedFileStem:
    def test_exact_match_without_sequence(self) -> None:
        assert renamed_file_stem("商品A", "商品A", "爆款") == "爆款"
        assert renamed_file_stem("商品A_1", "商品A", "爆款") is None

    def test_sequence_form_is_renamed(self) -> None:
        assert renamed_file_stem("商品A_3", "商品A", "爆款", allow_sequence=True) == "爆款_3"
        assert renamed_file_stem("商品A_x", "商品A", "爆款", allow_sequence=True) is None

    def test_sequence_mode_rejects_stem_equal_to_name(self) -> None:
        """序号目录里的文件都是 ``名_<n>``，stem 恰好等于资产名的文件属于别人。"""
        assert renamed_file_stem("商品A", "商品A", "爆款", allow_sequence=True) is None

    def test_name_ending_in_digits_does_not_claim_sibling_sequence_file(self) -> None:
        """Cup_1 与 Cup 并存：Cup 的第 1 张原图 Cup_1 不是商品 Cup_1 的文件。"""
        assert renamed_file_stem("Cup_1", "Cup_1", "X", allow_sequence=True) is None
        assert renamed_file_stem("Cup_1_1", "Cup_1", "X", allow_sequence=True) == "X_1"
        assert renamed_file_stem("Cup_1", "Cup", "X", allow_sequence=True) == "X_1"
        assert renamed_file_stem("Cup_1_1", "Cup", "X", allow_sequence=True) is None

    def test_sequence_mode_matches_nfd_stem(self) -> None:
        nfd = unicodedata.normalize("NFD", "café_2")
        assert renamed_file_stem(nfd, "café", "x", allow_sequence=True) == "x_2"


def test_entry_paths_and_files_for_name_ending_in_digits(tmp_path: Path) -> None:
    """资产名以 ``_数字`` 结尾时，路径字段改写与文件扫描都不认兄弟资产的序号文件。"""
    from lib.project.asset_rename import asset_files_named, plan_asset_file_renames

    spec = ASSET_SPECS["product"]
    refs = tmp_path / "products" / "refs"
    refs.mkdir(parents=True)
    (refs / "Cup_1.png").write_bytes(b"cup")
    (refs / "Cup_1_1.png").write_bytes(b"cup1")

    assert [f.name for f in asset_files_named(tmp_path, spec, "Cup_1")] == ["Cup_1_1.png"]
    assert [(src.name, dst.name) for src, dst in plan_asset_file_renames(tmp_path, spec, "Cup_1", "X")] == [
        ("Cup_1_1.png", "X_1.png")
    ]
    entry = {"reference_images": ["products/refs/Cup_1.png"]}
    assert rewrite_entry_paths(entry, spec, "Cup_1", "X") == 0
    assert entry["reference_images"] == ["products/refs/Cup_1.png"]
    cup_entry = {"reference_images": ["products/refs/Cup_1.png"]}
    assert rewrite_entry_paths(cup_entry, spec, "Cup", "Y") == 1
    assert cup_entry["reference_images"] == ["products/refs/Y_1.png"]
