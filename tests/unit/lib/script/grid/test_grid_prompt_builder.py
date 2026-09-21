"""Tests for lib/script/grid/prompt_builder.py"""

import pytest

from lib.script.grid.prompt_builder import (
    _compute_panel_aspect,
    _extract_action,
    _extract_image_desc,
    build_grid_prompt,
    pending_grid_prompt_ids,
    project_grid_image_prompt,
)


class TestExtractImageDesc:
    def _scene_dict(self, scene_text, composition):
        return {
            "scene_id": "S1",
            "image_prompt": {"scene": scene_text, "composition": composition},
        }

    def test_dict_prompt_joins_scene_and_composition(self):
        scene = self._scene_dict("a hero stands", {"shot_type": "medium", "lighting": "natural"})
        result = _extract_image_desc(scene)
        assert "a hero stands" in result
        assert "medium" in result
        assert "natural" in result

    def test_composition_key_order_is_a_semantic_noop(self):
        first = self._scene_dict("a hero stands", {"shot_type": "medium", "lighting": "natural"})
        reordered = self._scene_dict("a hero stands", {"lighting": "natural", "shot_type": "medium"})

        assert _extract_image_desc(reordered) == _extract_image_desc(first)

    @pytest.mark.parametrize(("raw_value", "expected"), [(0, "0"), (False, "False")])
    def test_composition_preserves_falsy_scalar_values(self, raw_value, expected):
        assert project_grid_image_prompt({"scene": "a hero stands", "composition": {"value": raw_value}}) == {
            "scene": "a hero stands",
            "composition": {"value": expected},
        }

    def test_string_prompt_returns_as_is(self):
        scene = {"scene_id": "S1", "image_prompt": "plain text prompt"}
        result = _extract_image_desc(scene)
        assert result == "plain text prompt"

    def test_dict_prompt_missing_scene_key(self):
        scene = {"scene_id": "S1", "image_prompt": {"composition": {"lighting": "bright"}}}
        result = _extract_image_desc(scene)
        assert "bright" in result

    def test_empty_image_prompt(self):
        scene = {"scene_id": "S1", "image_prompt": ""}
        result = _extract_image_desc(scene)
        assert result == ""

    def test_pending_image_prompt_refused(self):
        """机械转换后 image_prompt 为 None：不能渲染成字面量 "None"，投影拒绝。"""
        with pytest.raises(ValueError, match="pending"):
            project_grid_image_prompt(None)
        with pytest.raises(ValueError, match="pending"):
            _extract_image_desc({"scene_id": "S1", "image_prompt": None})


class TestPendingGridPromptIds:
    def test_lists_pending_and_empty_cells_in_script_order(self):
        scenes = [
            {"scene_id": "S1", "image_prompt": {"scene": "ok"}},
            {"scene_id": "S2", "image_prompt": None},
            {"scene_id": "S3"},
            {"scene_id": "S4", "image_prompt": ""},
        ]
        assert pending_grid_prompt_ids(scenes, "scene_id") == ["S2", "S3", "S4"]


class TestExtractAction:
    def test_dict_video_prompt_returns_action(self):
        scene = {"video_prompt": {"action": "walks away", "camera_motion": "pan"}}
        result = _extract_action(scene)
        assert result == "walks away"

    def test_string_video_prompt_returns_as_is(self):
        scene = {"video_prompt": "character runs fast"}
        result = _extract_action(scene)
        assert result == "character runs fast"

    def test_dict_missing_action_returns_empty(self):
        scene = {"video_prompt": {"camera_motion": "zoom"}}
        result = _extract_action(scene)
        assert result == ""

    def test_pending_video_prompt_returns_empty(self):
        assert _extract_action({"video_prompt": None}) == ""
        assert _extract_action({}) == ""


class TestComputePanelAspect:
    def test_grid_16_9_2x2(self):
        assert _compute_panel_aspect("16:9", 2, 2) == "16:9"

    def test_grid_9_16_2x2(self):
        assert _compute_panel_aspect("9:16", 2, 2) == "9:16"

    def test_grid_4_3_3rows_2cols(self):
        assert _compute_panel_aspect("4:3", 3, 2) == "2:1"

    def test_grid_3_4_2rows_3cols(self):
        assert _compute_panel_aspect("3:4", 2, 3) == "1:2"

    def test_grid_16_9_3x3(self):
        assert _compute_panel_aspect("16:9", 3, 3) == "16:9"

    def test_grid_9_16_3x3(self):
        assert _compute_panel_aspect("9:16", 3, 3) == "9:16"


class TestBuildGridPrompt:
    def _scene(self, sid, scene_text, action):
        return {
            "scene_id": sid,
            "image_prompt": {
                "scene": scene_text,
                "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
            },
            "video_prompt": {
                "action": action,
                "camera_motion": "static",
                "ambiance_audio": "quiet",
                "dialogue": [],
            },
        }

    def test_basic_4_scenes(self):
        scenes = [self._scene(f"S{i}", f"scene{i}", f"action{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "2×2" in prompt
        assert "scene1" in prompt
        assert "scene4" in prompt

    def test_includes_placeholders(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 6)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=3, cols=2, style="anime", style_description=""
        )
        assert "空占位" in prompt

    def test_references_lead_with_a_numbered_declaration_and_replace_mentions(self):
        from pathlib import Path

        from lib.artifacts.visual_artifact_provenance import VisualReference

        scenes = [self._scene(f"S{i}", f"@[角色A]在s{i}", f"a{i}") for i in range(1, 5)]
        scenes[1]["image_prompt"] = "@[角色A]与@[路人]对视"
        references = [
            VisualReference(
                path=Path("characters/角色A.png"), role="asset_sheet", logical_type="character", logical_id="角色A"
            ),
            VisualReference(path=Path("scenes/酒馆.png"), role="asset_sheet", logical_type="scene", logical_id="酒馆"),
        ]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="x", style_description="", references=references
        )
        assert prompt.startswith("Reference_Images: 图1为角色参考图；图2为场景参考图。\n\n你是一位专业的分镜画师。")
        assert "图1在s1" in prompt
        assert "图1与路人对视" in prompt
        assert "@[" not in prompt
        assert "角色A" not in prompt.split("\n", 1)[1]

    def test_string_prompts(self):
        scenes = [{"scene_id": f"S{i}", "image_prompt": f"text{i}", "video_prompt": f"vid{i}"} for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "text1" in prompt

    def test_without_references_there_is_no_declaration_and_mentions_fall_back_to_names(self):
        scenes = [self._scene(f"S{i}", f"@[角色Z]在s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "Reference_Images" not in prompt
        assert prompt.startswith("你是一位专业的分镜画师。")
        assert "角色Z在s1" in prompt
        assert "@[" not in prompt

    def test_grid_dimensions_in_header(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=3, style="realistic", style_description=""
        )
        assert "2×3" in prompt

    def test_style_and_style_description_render_as_style_lines(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=2,
            cols=2,
            style="cyberpunk neon",
            style_description="冷色霓虹，湿润街面",
        )
        lines = prompt.splitlines()
        assert lines.count("Style: cyberpunk neon") == 1
        assert lines.count("Visual style: 冷色霓虹，湿润街面") == 1

    def test_blank_style_leaves_no_style_lines_or_gaps(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="", style_description="")
        assert "Style:" not in prompt
        assert "\n\n\n" not in prompt

    def test_avoid_line_is_image_negative_prompt_plus_grid_exclusions(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert prompt.splitlines()[-1] == (
            "Avoid: 水印、多余文字、Logo、边框、画格间隙或留白、合并 / 缺失 / 错位的画格、连续全景（非分格）"
        )
        for dropped in (
            "禁止出现以下任何元素",
            "模糊",
            "低画质",
            "噪点",
            "拼贴感",
            "纯色背景条",
            "画格大小不一致",
            "画格比例不一致",
        ):
            assert dropped not in prompt

    def test_avoid_line_still_closes_the_prompt_when_a_cell_repeats_it(self):
        """宫格没有纯文本回贴形态，模版不开启判重：画格正文里的同形行不吞掉收尾的 Avoid 行。"""
        avoid = "Avoid: 水印、多余文字、Logo、边框、画格间隙或留白、合并 / 缺失 / 错位的画格、连续全景（非分格）"
        scenes = [{"scene_id": "S1", "image_prompt": f"主体描述\n{avoid}", "video_prompt": "a1"}]

        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )

        assert prompt.endswith(f"\n\n{avoid}")

    def test_single_scene_chunk_lists_cells_without_blank_lines(self):
        scenes = [self._scene("S1", "s1", "a1")]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        cells = prompt.split("【各格内容】\n", 1)[1].split("\n\n", 1)[0].splitlines()
        assert cells == [
            "格0（row1 col1）— S1开场：",
            "  s1；ambiance: calm，lighting: natural，shot_type: medium",
            "格1（row1 col2）— 空占位：纯灰色背景，无任何内容",
            "格2（row2 col1）— 空占位：纯灰色背景，无任何内容",
            "格3（row2 col2）— 空占位：纯灰色背景，无任何内容",
        ]

    def test_single_scene_chunk_omits_transition_frame_rule(self):
        scenes = [self._scene("S1", "s1", "a1")]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "过渡帧" not in prompt
        assert "- 格0 是第一个场景的开场画面\n- 相邻格之间" in prompt

    def test_multi_scene_chunk_lists_transition_frame_range(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 4)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "- 格1~格2 是相邻场景的过渡帧" in prompt

    def test_no_placeholders_when_exact_fit(self):
        # 4 scenes, 2x2 grid -> no placeholders needed (4 content cells: open, trans, trans, close)
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "空占位" not in prompt

    def test_grid_aspect_ratio_in_layout(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=2,
            cols=2,
            style="realistic",
            style_description="",
            grid_aspect_ratio="16:9",
        )
        assert "16:9" in prompt

    def test_non_square_layout_panel_aspect_ratio(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 7)]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=3,
            cols=2,
            style="realistic",
            style_description="",
            grid_aspect_ratio="4:3",
        )
        assert "4:3" in prompt
        assert _compute_panel_aspect("4:3", 3, 2) in prompt

    @pytest.mark.parametrize(("side", "n_scenes"), [(4, 14), (5, 25)])
    def test_large_square_layout_describes_every_cell(self, side: int, n_scenes: int):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, n_scenes + 1)]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=side,
            cols=side,
            style="realistic",
            style_description="",
            grid_aspect_ratio="16:9",
        )
        total = side * side
        assert f"{side}×{side} 宫格布局" in prompt
        assert f"恰好 {side} 行 {side} 列，共 {total} 个画格" in prompt
        # 方形切分下单格比例等于整图比例
        assert "每个画格比例：16:9" in prompt
        for idx in range(total):
            assert f"格{idx}（row{idx // side + 1} col{idx % side + 1}）" in prompt
        assert prompt.count("空占位") == total - n_scenes

    def test_more_scenes_than_cells_fails_loud(self):
        # 超员场景在成图中没有对应画格，调用方应先按 max_cell_count 切块
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 13)]
        with pytest.raises(ValueError, match="切块"):
            build_grid_prompt(
                scenes=scenes, id_field="scene_id", rows=3, cols=3, style="realistic", style_description=""
            )

    def test_anti_structural_constraints(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "不得合并画格" in prompt
        assert "不得遗漏画格" in prompt
        assert "不得错位排列" in prompt
        assert "紧密排列" in prompt
