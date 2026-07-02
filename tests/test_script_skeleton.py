"""剧本骨架注册表与规范/取证双解析器的集中矩阵测试。

四象限：
- 表自洽：四行齐全、字段合法、import 期校验触发
- 规范解析全组合：3 content_mode × storyboard/grid/reference_video（含 ad 骨架恒 shots）
- 取证阶梯逐台阶
- fail-loud：未知/缺失 content_mode 抛 ValueError（原「未知落 drama」语义已反转）
"""

from __future__ import annotations

import pytest

from lib import script_skeleton
from lib.script_skeleton import (
    SKELETONS,
    Skeleton,
    resolve_declared_kind,
    resolve_script_kind,
)


@pytest.mark.unit
class TestRegistrySelfConsistency:
    def test_four_kinds_complete(self):
        # 键即条目数组键，四骨架齐全。
        assert set(SKELETONS) == {"segments", "scenes", "shots", "video_units"}

    def test_id_fields(self):
        assert SKELETONS["segments"].id_field == "segment_id"
        assert SKELETONS["scenes"].id_field == "scene_id"
        assert SKELETONS["shots"].id_field == "shot_id"
        assert SKELETONS["video_units"].id_field == "unit_id"

    def test_chars_fields_declared_or_explicitly_absent(self):
        assert SKELETONS["segments"].chars_field == "characters_in_segment"
        assert SKELETONS["scenes"].chars_field == "characters_in_scene"
        assert SKELETONS["shots"].chars_field == "characters_in_shot"
        # video_units 无逐条角色名单（角色以 references 中 character 条目形态存在）：
        # 表如实声明缺位（None），不给假字段名。
        assert SKELETONS["video_units"].chars_field is None

    def test_real_registry_passes_validation(self):
        script_skeleton._validate_registry()  # 不抛即通过

    def test_missing_kind_fails_fast(self, monkeypatch):
        broken = {k: v for k, v in SKELETONS.items() if k != "video_units"}
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()

    def test_empty_id_field_fails_fast(self, monkeypatch):
        broken = dict(SKELETONS)
        broken["segments"] = Skeleton("", "characters_in_segment")
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()

    def test_empty_chars_field_fails_fast(self, monkeypatch):
        # None 合法（显式缺位），空串非法。
        broken = dict(SKELETONS)
        broken["scenes"] = Skeleton("scene_id", "")
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()


@pytest.mark.unit
class TestDeclaredResolver:
    """规范解析全组合：(content_mode, generation_mode) → kind。"""

    @pytest.mark.parametrize("generation_mode", [None, "storyboard", "grid_4", "grid_6", "grid_9"])
    def test_storyboard_and_grid_paths(self, generation_mode):
        # 非 reference 生成路径：content_mode → 内容骨架，不随 storyboard/grid 变。
        assert resolve_declared_kind("narration", generation_mode) == "segments"
        assert resolve_declared_kind("drama", generation_mode) == "scenes"
        assert resolve_declared_kind("ad", generation_mode) == "shots"

    def test_reference_video_routes_narration_drama_to_video_units(self):
        assert resolve_declared_kind("narration", "reference_video") == "video_units"
        assert resolve_declared_kind("drama", "reference_video") == "video_units"

    @pytest.mark.parametrize("generation_mode", [None, "storyboard", "grid_4", "reference_video"])
    def test_ad_is_shots_regardless_of_generation_mode(self, generation_mode):
        # ad 骨架唯一：不随生成路径变（含 reference_video）。
        assert resolve_declared_kind("ad", generation_mode) == "shots"

    @pytest.mark.parametrize("content_mode", [None, "", "reference_video", "unknown"])
    @pytest.mark.parametrize("generation_mode", [None, "reference_video"])
    def test_unknown_or_missing_content_mode_raises(self, content_mode, generation_mode):
        # fail-loud：不静默落 drama/narration。
        with pytest.raises(ValueError):
            resolve_declared_kind(content_mode, generation_mode)


@pytest.mark.unit
class TestScriptResolver:
    """取证解析阶梯逐台阶（判别顺序 1→4 + 终兜底）。"""

    def test_step1_video_units_alone(self):
        # video_units 在场且 segments/scenes/shots 都不在 → reference。
        assert resolve_script_kind({"video_units": []}) == "video_units"

    def test_step1_floating_video_units_does_not_hijack_storyboard(self):
        # 游离 video_units 不抢走 storyboard 脚本的判别。
        assert resolve_script_kind({"video_units": [], "segments": [], "content_mode": "narration"}) == "segments"

    def test_step2_content_mode_authority(self):
        assert resolve_script_kind({"content_mode": "ad"}) == "shots"
        assert resolve_script_kind({"content_mode": "drama"}) == "scenes"
        assert resolve_script_kind({"content_mode": "narration", "segments": []}) == "segments"

    def test_step3_narration_falls_back_to_scenes_key(self):
        # content_mode=narration 但数据落 scenes 键（无 segments）→ 回退 scenes。
        assert resolve_script_kind({"content_mode": "narration", "scenes": []}) == "scenes"

    def test_step4_key_existence_inference_when_content_mode_absent(self):
        assert resolve_script_kind({"scenes": []}) == "scenes"
        assert resolve_script_kind({"shots": []}) == "shots"

    def test_final_fallback_segments(self):
        assert resolve_script_kind({}) == "segments"
