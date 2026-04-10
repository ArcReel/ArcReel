"""Tests for lib/grid/prompt_builder.py"""

from lib.grid.prompt_builder import _compute_panel_aspect, _extract_action, _extract_image_desc, build_grid_prompt


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
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "2×2" in prompt
        assert "格0" in prompt
        assert "格3" in prompt
        assert "首尾帧链式结构" in prompt
        assert "恰好" in prompt
        assert "整体图片比例" in prompt
        assert "每个画格比例" in prompt

    def test_includes_placeholders(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 6)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=3, cols=2, style="anime")
        assert "空占位" in prompt

    def test_reference_mapping(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=2,
            cols=2,
            style="x",
            reference_image_mapping={"图片1": "角色A"},
        )
        assert "图片1" in prompt and "角色A" in prompt

    def test_string_prompts(self):
        scenes = [{"scene_id": f"S{i}", "image_prompt": f"text{i}", "video_prompt": f"vid{i}"} for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "text1" in prompt

    def test_no_reference_mapping_no_reference_section(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "【参考图说明】" not in prompt

    def test_grid_dimensions_in_header(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=3, style="realistic")
        assert "2×3" in prompt

    def test_style_in_prompt(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="cyberpunk neon")
        assert "cyberpunk neon" in prompt

    def test_negative_constraints_present(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "禁止出现以下任何元素" in prompt
        assert "合并的画格" in prompt
        assert "缺失的画格" in prompt
        assert "白色边框" in prompt
        assert "画格大小不一致" in prompt

    def test_transition_frames_contain_arrow(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        # Transition frames should contain "→"
        assert "→" in prompt

    def test_total_cell_count_in_header(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "恰好 4 个等大画格" in prompt

    def test_no_placeholders_when_exact_fit(self):
        # 4 scenes, 2x2 grid -> no placeholders needed (4 content cells: open, trans, trans, close)
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "空占位" not in prompt

    def test_grid_aspect_ratio_in_layout(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic", grid_aspect_ratio="16:9"
        )
        assert "整体图片比例：16:9" in prompt
        assert "每个画格比例：16:9" in prompt

    def test_grid_6_panel_aspect_ratio(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 7)]
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=3, cols=2, style="realistic", grid_aspect_ratio="4:3"
        )
        assert "整体图片比例：4:3" in prompt
        assert "每个画格比例：2:1" in prompt

    def test_anti_structural_constraints(self):
        scenes = [self._scene(f"S{i}", f"s{i}", f"a{i}") for i in range(1, 5)]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "不得合并画格" in prompt
        assert "不得遗漏画格" in prompt
        assert "不得错位排列" in prompt
        assert "紧密排列" in prompt
