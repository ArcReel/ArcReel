"""剧本骨架注册表与规范/取证双解析器的集中矩阵测试。

五象限：
- 表自洽：四行齐全、字段合法、import 期校验触发
- 规范解析全组合：3 content_mode × storyboard/grid/reference_video
- 取证阶梯逐台阶
- fail-loud：未知/缺失 content_mode 抛 ValueError（原「未知落 drama」语义已反转）
- 路线闸门：跨族失配拒绝、族内差异放行
"""

from __future__ import annotations

import pytest

from lib import script_skeleton
from lib.script_skeleton import (
    SKELETONS,
    Skeleton,
    SkeletonRouteMismatchError,
    ensure_route_skeleton,
    resolve_declared_kind,
    resolve_kind_items,
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

    def test_reference_video_routes_all_content_modes_to_video_units(self):
        assert resolve_declared_kind("narration", "reference_video") == "video_units"
        assert resolve_declared_kind("drama", "reference_video") == "video_units"
        assert resolve_declared_kind("ad", "reference_video") == "video_units"

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

    def test_step1_floating_video_units_hijack_guard_without_content_mode(self):
        # 游离 video_units + segments 并存但无 content_mode：step1 守卫仍挡住 video_units 抢判，
        # 缺 content_mode 落键存在性阶梯（step4/终兜底）返回 segments。覆盖历史脏数据 storyboard
        # 脚本（被误塞游离 video_units、无 content_mode 戳）不被误判为 reference 的取证路径。
        assert resolve_script_kind({"video_units": [], "segments": []}) == "segments"

    def test_step2_content_mode_authority(self):
        assert resolve_script_kind({"content_mode": "ad"}) == "shots"
        assert resolve_script_kind({"content_mode": "drama"}) == "scenes"
        assert resolve_script_kind({"content_mode": "narration", "segments": []}) == "segments"

    def test_step3_narration_falls_back_to_scenes_key(self):
        # content_mode=narration 但数据落 scenes 键（无 segments）→ 回退 scenes。
        assert resolve_script_kind({"content_mode": "narration", "scenes": []}) == "scenes"

    def test_residual_generation_mode_field_is_ignored(self):
        # 存量剧本残留的路线戳是未知字段：取证解析只看数据形状，按 segments 返回，编辑能力不丢失。
        script = {"content_mode": "narration", "generation_mode": "reference_video", "segments": []}
        assert resolve_script_kind(script) == "segments"

    def test_step4_key_existence_inference_when_content_mode_absent(self):
        assert resolve_script_kind({"scenes": []}) == "scenes"
        assert resolve_script_kind({"shots": []}) == "shots"

    def test_final_fallback_segments(self):
        assert resolve_script_kind({}) == "segments"


@pytest.mark.unit
class TestRouteSkeletonGate:
    """路线闸门：剧本骨架与项目路线跨族即拒，族内差异放行。"""

    def test_matched_reference_route_passes(self):
        script = {"content_mode": "narration", "video_units": []}
        assert ensure_route_skeleton(script, "narration", "reference_video") == "video_units"

    def test_matched_storyboard_route_passes(self):
        script = {"content_mode": "drama", "scenes": []}
        assert ensure_route_skeleton(script, "drama", "storyboard") == "scenes"

    def test_ad_reference_route_expects_video_units(self):
        script = {"content_mode": "ad", "video_units": []}
        assert ensure_route_skeleton(script, "ad", "reference_video") == "video_units"

    def test_ad_reference_route_rejects_legacy_shots_skeleton(self):
        script = {"content_mode": "ad", "shots": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "ad", "reference_video")
        assert (exc.value.expected, exc.value.actual) == ("video_units", "shots")

    def test_narration_data_in_scenes_key_is_not_a_mismatch(self):
        # 族内历史形态（narration 数据落 scenes 键）照实返回，不当失配拒绝。
        script = {"content_mode": "narration", "scenes": []}
        assert ensure_route_skeleton(script, "narration", "storyboard") == "scenes"

    def test_reference_route_passes_with_residual_storyboard_array(self):
        # 参考路线剧本残留分镜族数组：取证解析按形状优先答 segments，但生成侧读的是 video_units，
        # 残留数组不参与投票，闸门须放行（与费用估算按 units 计价同口径）。
        script = {
            "content_mode": "narration",
            "video_units": [{"unit_id": "E1U1"}],
            "segments": [{"segment_id": "E1S1"}],
        }
        assert ensure_route_skeleton(script, "narration", "reference_video") == "video_units"

    def test_storyboard_route_passes_with_residual_unit_array(self):
        # 反向：分镜路线剧本残留 video_units，分镜数组在场即放行。
        script = {"content_mode": "narration", "segments": [{"segment_id": "E1S1"}], "video_units": []}
        assert ensure_route_skeleton(script, "narration", "storyboard") == "segments"

    def test_unit_script_on_storyboard_route_is_rejected(self):
        script = {"content_mode": "narration", "video_units": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "storyboard")
        assert exc.value.expected == "segments"
        assert exc.value.actual == "video_units"
        # 结构报错 + 重拆指引，并说明查看/编辑/导出不受影响。
        assert "骨架" in str(exc.value)
        assert "重新拆分" in str(exc.value)
        assert "查看" in str(exc.value)

    def test_storyboard_script_on_reference_route_is_rejected(self):
        script = {"content_mode": "narration", "segments": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual == "segments"
        assert "split-reference-video-units" in str(exc.value)

    def test_storyboard_route_rejects_script_without_any_skeleton_array(self):
        # 三个分镜键全缺：resolve_script_kind 会按 content_mode 合成 segments，若据此放行，
        # 分镜图入队会落进"所有片段的分镜图都已生成"的假成功。判据是键在场性，故拒绝。
        script = {"content_mode": "narration", "title": "第一集"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "storyboard")
        assert exc.value.expected == "segments"
        assert exc.value.actual is None
        # 无骨架数组与「有数组但属另一族」分开措辞，不报出「要求 segments、当前 segments」。
        assert "没有任何骨架数组" in str(exc.value)
        assert "当前剧本是" not in str(exc.value)

    def test_reference_route_rejects_script_without_any_skeleton_array(self):
        script = {"content_mode": "narration"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual is None
        assert "没有任何骨架数组" in str(exc.value)

    def test_ad_route_rejects_script_without_any_skeleton_array(self):
        script = {"content_mode": "ad"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "ad", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual is None

    def test_empty_skeleton_array_is_present_and_passes(self):
        # 键在场即放行——空数组是"已拆分但没有条目"，与"根本没拆分"不同，不由本闸门拒绝。
        assert ensure_route_skeleton({"content_mode": "narration", "segments": []}, "narration", "storyboard") == (
            "segments"
        )

    def test_unknown_content_mode_still_fails_loud(self):
        with pytest.raises(ValueError):
            ensure_route_skeleton({"segments": []}, None, "storyboard")


@pytest.mark.unit
class TestKindItemsAccessor:
    """条目访问唯一入口：种类判别的两条来路 × 条目值原样返回。"""

    def test_forensic_kind_when_not_given(self):
        # kind 缺省 → 走取证解析，与 resolve_script_kind 同一判别。
        script = {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]}
        items, id_field, kind = resolve_kind_items(script)
        assert kind == "scenes"
        assert id_field == SKELETONS["scenes"].id_field
        assert items == [{"scene_id": "E1S01"}]

    def test_explicit_kind_skips_forensics(self):
        # 显式 kind 覆盖数据形状：取证解析会判 segments，指定 video_units 则按后者取值。
        script = {"content_mode": "narration", "segments": [], "video_units": [{"unit_id": "E1U01"}]}
        items, id_field, kind = resolve_kind_items(script, kind="video_units")
        assert (kind, id_field) == ("video_units", SKELETONS["video_units"].id_field)
        assert items == [{"unit_id": "E1U01"}]

    def test_id_field_per_kind(self):
        # 四骨架各自的条目身份不被通用化。
        for kind, expected in (
            ("segments", "segment_id"),
            ("scenes", "scene_id"),
            ("shots", "shot_id"),
            ("video_units", "unit_id"),
        ):
            assert resolve_kind_items({}, kind=kind)[1] == expected

    def test_items_returned_by_reference(self):
        # 返回的是 script 内实际引用，就地编辑对调用方生效。
        items_in_script: list[dict[str, object]] = []
        script = {"content_mode": "narration", "segments": items_in_script}
        items, _id_field, _kind = resolve_kind_items(script)
        assert items is items_in_script

    def test_missing_key_yields_none_not_empty_list(self):
        # 不把缺失兜底成空数组——「键缺失」与「键在场但空」由调用方各自定策略。
        items, id_field, kind = resolve_kind_items({"content_mode": "narration"})
        assert (items, kind) == (None, "segments")
        assert id_field == "segment_id"

    def test_non_list_value_returned_as_is(self):
        # 脏值原样上交，不在此 fail-loud、也不静默降级。
        assert resolve_kind_items({"content_mode": "narration", "segments": None})[0] is None
        assert resolve_kind_items({"content_mode": "drama", "scenes": "E1S01"})[0] == "E1S01"
