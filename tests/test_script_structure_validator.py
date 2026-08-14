"""剧本结构校验器（纯函数）测试。

只断言外部行为：喂入纯 dict，断言返回的 ValidationResult。逐条覆盖三种模式的合法/非法
判定与模式判别，不 patch 私有方法。
"""

from __future__ import annotations

import pytest

from lib.script_structure_validator import validate_script_structure

pytestmark = pytest.mark.unit


def _segment(segment_id: str = "E1S01", duration: int = 4) -> dict:
    return {
        "segment_id": segment_id,
        "duration_seconds": duration,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _narration(segments: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "creation_type": "narration",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment()],
    }


def _scene(scene_id: str = "E1S01", duration: int = 8) -> dict:
    return {
        "scene_id": scene_id,
        "duration_seconds": duration,
        "characters_in_scene": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _drama(scenes: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "creation_type": "drama",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": scenes if scenes is not None else [_scene()],
    }


def _unit(unit_id: str = "E1U1", shots: list[dict] | None = None, duration: int | None = None, **extra) -> dict:
    shots = shots if shots is not None else [{"text": "镜头1"}, {"text": "镜头2"}]
    unit = {
        "unit_id": unit_id,
        "shots": shots,
        "references": [],
        "duration_seconds": duration if duration is not None else 8,
    }
    unit.update(extra)
    return unit


def _reference(units: list[dict] | None = None, creation_type: str = "narration") -> dict:
    return {
        "title": "标题",
        "creation_type": creation_type,
        "generation_mode": "reference_video",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit()],
    }


class TestValidScripts:
    def test_valid_narration(self):
        assert validate_script_structure(_narration()).valid

    def test_valid_drama(self):
        assert validate_script_structure(_drama()).valid

    def test_valid_reference_video(self):
        assert validate_script_structure(_reference()).valid


class TestModeDetection:
    def test_video_units_only_picks_reference_model(self):
        """video_units 唯一存在(无 segments/scenes)时走 ReferenceVideoScript,不论
        creation_type 标记是什么——按数据形状路由(动作 5 引入)。

        reference 剧本只有 video_units、无 segments;若误判为 NarrationEpisodeScript 会因缺
        segments 而 invalid。结果 valid 证明判别走了 ReferenceVideoScript。
        """
        script = _reference(creation_type="narration")
        assert script.get("creation_type") == "narration"
        assert validate_script_structure(script).valid

    def test_partial_migration_segments_picks_narration_model(self):
        """partial migration:generation_mode='reference_video' 但数据还在 segments,
        应按数据形状走 NarrationEpisodeScript 而非让 generation_mode 单向赢——
        若强制 ReferenceVideoScript 校验会因 video_units 缺失 invalid。
        """
        # 用 _narration() 的 segments,但带 generation_mode='reference_video' 标记(partial migration)
        script = _narration()
        script["generation_mode"] = "reference_video"
        assert validate_script_structure(script).valid

    def test_stray_video_units_do_not_hijack_storyboard_script(self):
        """历史脏数据：narration 脚本被误塞游离 video_units。video_units 与 segments 并存且无
        显式 reference 模式时，判别不应抢到 ReferenceVideoScript（会因缺合法 units 拒写真实
        segments），而应按 creation_type 走 Narration（多余 video_units 键被 extra=ignore 忽略）。
        """
        script = _narration()
        script["video_units"] = [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}]
        assert validate_script_structure(script).valid

    def test_drama_detected_by_scenes(self):
        assert validate_script_structure(_drama()).valid

    def test_empty_scenes_drama_detected_by_content_mode(self):
        """空场景 drama（scenes=[]，结构合法）应按 creation_type 判到 Drama，而非靠列表真值落回 Narration。

        scenes 无 min_length 约束，空列表合法（对应「先建空 drama 再逐步填充场景」流程）。
        若用 script.get("scenes") 真值判别，[] falsy 会误落 NarrationEpisodeScript 而被拒写。
        """
        script = _drama(scenes=[])
        assert validate_script_structure(script).valid

    def test_drama_detected_by_scenes_key_when_content_mode_absent(self):
        # 无 creation_type、有 scenes 键（即便空）：按键存在推断 Drama
        script = _drama(scenes=[])
        del script["creation_type"]
        assert validate_script_structure(script).valid

    def test_narration_is_default_fallback(self):
        # 无 creation_type、无 scenes/video_units：回退 NarrationEpisodeScript
        script = _narration()
        del script["creation_type"]
        assert validate_script_structure(script).valid


class TestInvalidNarration:
    def test_missing_required_top_field(self):
        script = _narration()
        del script["title"]
        result = validate_script_structure(script)
        assert not result.valid
        assert any("title" in e for e in result.errors)

    def test_missing_segment_required_field(self):
        seg = _segment()
        del seg["novel_text"]
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("novel_text" in e for e in result.errors)

    def test_duration_below_range(self):
        result = validate_script_structure(_narration([_segment(duration=0)]))
        assert not result.valid

    def test_duration_above_range(self):
        result = validate_script_structure(_narration([_segment(duration=61)]))
        assert not result.valid

    def test_video_prompt_wrong_shape(self):
        seg = _segment()
        seg["video_prompt"] = "纯字符串而非对象"
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("video_prompt" in e for e in result.errors)

    def test_image_prompt_missing_composition(self):
        seg = _segment()
        seg["image_prompt"] = {"scene": "只有 scene"}
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("composition" in e for e in result.errors)


class TestInvalidReferenceVideo:
    def test_duration_out_of_structural_range_rejected(self):
        # unit 时长是唯一真相、与镜头无关，只受结构合理性区间约束
        result = validate_script_structure(_reference([_unit(duration=9999)]))
        assert not result.valid
        assert any("duration" in e.lower() for e in result.errors)

    def test_duration_independent_of_shot_count(self):
        assert validate_script_structure(_reference([_unit(duration=12)])).valid

    def test_empty_shots_rejected(self):
        unit = _unit(shots=[], duration=8)
        result = validate_script_structure(_reference([unit]))
        assert not result.valid

    def test_too_many_shots_rejected(self):
        shots = [{"text": f"镜头{i}"} for i in range(5)]
        unit = _unit(shots=shots, duration=5)
        result = validate_script_structure(_reference([unit]))
        assert not result.valid


def _ad_shot(shot_id: str = "E1S01", duration: int = 3) -> dict:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _ad(shots: list[dict] | None = None) -> dict:
    return {
        "title": "短片",
        "creation_type": "ad",
        "novel": {"title": "", "chapter": ""},
        "shots": shots if shots is not None else [_ad_shot()],
    }


class TestAdScripts:
    def test_valid_ad(self):
        assert validate_script_structure(_ad()).valid

    def test_ad_detected_by_content_mode(self):
        """ad 剧本按 AdEpisodeScript 校验，不落 narration/drama 模型。"""
        bad = _ad()
        del bad["shots"][0]["voiceover_text"]
        result = validate_script_structure(bad)
        assert not result.valid
        assert any("voiceover_text" in e for e in result.errors)

    def test_ad_detected_by_shots_key_when_content_mode_absent(self):
        script = _ad()
        del script["creation_type"]
        assert validate_script_structure(script).valid

    def test_resolve_kind_and_items_for_ad(self):
        from lib.script_editor import resolve_items
        from lib.script_skeleton import resolve_script_kind

        script = _ad()
        assert resolve_script_kind(script) == "shots"
        items, id_field, kind = resolve_items(script)
        assert (id_field, kind) == ("shot_id", "shots")
        assert items[0]["shot_id"] == "E1S01"
