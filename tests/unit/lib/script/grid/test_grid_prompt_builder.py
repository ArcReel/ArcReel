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
        """Mechanically converted image_prompt=None must not render as literal "None"; projection rejects it."""
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
        assert "empty placeholder" in prompt

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
        assert prompt.startswith(
            "Reference_Images: 图1: character reference images; 图2: scene reference images.\n\nYou are a professional storyboard artist."
        )
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
        assert prompt.startswith("You are a professional storyboard artist.")
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
            "Avoid: watermarks, extra text, logos, borders, gaps or whitespace between panels, merged / missing / misaligned panels, a continuous panorama (instead of separate panels)"
        )
        for dropped in (
            "Any of the following elements are prohibited",
            "blur",
            "low quality",
            "noise",
            "collage feel",
            "solid-color background strips",
            "inconsistent panel sizes",
            "inconsistent panel aspect ratios",
        ):
            assert dropped not in prompt

    def test_avoid_line_still_closes_the_prompt_when_a_cell_repeats_it(self):
        """Grids lack plain-text copy-back, so templates disable deduplication; matching cell-body lines must not swallow the final Avoid line."""
        avoid = "Avoid: 水印、多余文字、Logo、边框、画格间隙或留白、合并 / 缺失 / 错位的画格、连续全景（非分格）"
        scenes = [{"scene_id": "S1", "image_prompt": f"主体描述\n{avoid}", "video_prompt": "a1"}]

        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )

        assert avoid in prompt
        assert prompt.endswith(
            "\n\nAvoid: watermarks, extra text, logos, borders, gaps or whitespace between panels, "
            "merged / missing / misaligned panels, a continuous panorama (instead of separate panels)"
        )

    def test_single_scene_chunk_lists_cells_without_blank_lines(self):
        scenes = [self._scene("S1", "s1", "a1")]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        cells = prompt.split("[Panel content]\n", 1)[1].split("\n\n", 1)[0].splitlines()
        assert cells == [
            "Panel 0 (row1 col1) - S1 opening:",
            "  s1；ambiance: calm，lighting: natural，shot_type: medium",
            "Panel 1 (row1 col2) - empty placeholder: plain gray background, no content",
            "Panel 2 (row2 col1) - empty placeholder: plain gray background, no content",
            "Panel 3 (row2 col2) - empty placeholder: plain gray background, no content",
        ]

    def test_single_scene_chunk_omits_transition_frame_rule(self):
        scenes = [self._scene("S1", "s1", "a1")]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "transition frames" not in prompt
        assert "- Panel 0 is the first scene’s opening image\n- Adjacent panels" in prompt

    def test_multi_scene_chunk_lists_transition_frame_range(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 4)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "- Panels 1~2 are transition frames between adjacent scenes" in prompt

    def test_no_placeholders_when_exact_fit(self):
        # 4 scenes, 2x2 grid -> no placeholders needed (4 content cells: open, trans, trans, close)
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", style_description=""
        )
        assert "empty placeholder" not in prompt

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
        assert f"strict {side}×{side} grid" in prompt
        assert f"Exactly {side} rows and {side} columns, {total} panels in total" in prompt
        # Square splitting makes cell and full-image ratios equal.
        assert "Each panel’s aspect ratio: 16:9" in prompt
        for idx in range(total):
            assert f"Panel {idx} (row{idx // side + 1} col{idx % side + 1})" in prompt
        assert prompt.count("empty placeholder") == total - n_scenes

    def test_more_scenes_than_cells_fails_loud(self):
        # Excess scenes have no image cells; callers must chunk by max_cell_count first.
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
        assert "Do not merge" in prompt
        assert "Do not merge, omit," in prompt
        assert "or misalign panels" in prompt
        assert "pack them tightly" in prompt
