import pytest
from pydantic import ValidationError

from lib.script_models import (
    Composition,
    Dialogue,
    DramaEpisodeScript,
    DramaScene,
    ImagePrompt,
    NarrationEpisodeScript,
    NarrationSegment,
    VideoPrompt,
)


class TestScriptModels:
    def test_narration_segment_defaults_and_validation(self):
        segment = NarrationSegment(
            segment_id="E1S01",
            duration_seconds=4,
            novel_text="原文",
            characters_in_segment=["姜月茴"],
            scenes=[],
            props=["玉佩"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(
                    shot_type="Medium Shot",
                    lighting="暖光",
                    ambiance="薄雾",
                ),
            ),
            video_prompt=VideoPrompt(
                action="转身",
                camera_motion="Static",
                ambiance_audio="风声",
                dialogue=[Dialogue(speaker="姜月茴", line="等等")],
            ),
        )

        assert segment.transition_to_next == "cut"
        assert segment.generated_assets.status == "pending"
        assert segment.scenes == []
        assert segment.props == ["玉佩"]
        assert not hasattr(segment, "clues_in_segment")

    def test_drama_scene_has_scenes_and_props_fields(self):
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["王"],
            scenes=["庙宇"],
            props=["玉佩"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
            ),
            video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        )
        assert scene.scenes == ["庙宇"]
        assert scene.props == ["玉佩"]
        assert not hasattr(scene, "clues_in_scene")

    def test_duration_accepts_any_positive_int_within_range(self):
        """duration_seconds 接受 1-60 范围内任意整数。"""
        segment = NarrationSegment(
            segment_id="E1S01",
            duration_seconds=10,  # 之前会被 DurationSeconds 拒绝
            novel_text="原文",
            characters_in_segment=["姜月茴"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
            ),
            video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
        )
        assert segment.duration_seconds == 10

    def test_duration_rejects_out_of_range(self):
        """duration_seconds 拒绝范围外的值。"""
        with pytest.raises(ValidationError):
            NarrationSegment(
                segment_id="E1S01",
                duration_seconds=0,
                novel_text="原文",
                characters_in_segment=["姜月茴"],
                image_prompt=ImagePrompt(
                    scene="场景",
                    composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
                ),
                video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
            )
        with pytest.raises(ValidationError):
            NarrationSegment(
                segment_id="E1S01",
                duration_seconds=61,
                novel_text="原文",
                characters_in_segment=["姜月茴"],
                image_prompt=ImagePrompt(
                    scene="场景",
                    composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
                ),
                video_prompt=VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声"),
            )

    def test_drama_scene_default_duration_is_8(self):
        """DramaScene 的默认 duration_seconds 仍为 8。"""
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["姜月茴"],
            image_prompt=ImagePrompt(
                scene="场景",
                composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
            ),
            video_prompt=VideoPrompt(action="前进", camera_motion="Static", ambiance_audio="雨声"),
        )
        assert scene.duration_seconds == 8

    def test_episode_models_build_successfully(self):
        narration = NarrationEpisodeScript(
            title="第一集",
            summary="摘要",
            novel={"title": "小说", "chapter": "1"},
            segments=[],
        )
        drama = DramaEpisodeScript(
            title="第一集",
            summary="摘要",
            novel={"title": "小说", "chapter": "1"},
            scenes=[
                DramaScene(
                    scene_id="E1S01",
                    characters_in_scene=["姜月茴"],
                    image_prompt=ImagePrompt(
                        scene="场景",
                        composition=Composition(
                            shot_type="Medium Shot",
                            lighting="暖光",
                            ambiance="薄雾",
                        ),
                    ),
                    video_prompt=VideoPrompt(
                        action="前进",
                        camera_motion="Static",
                        ambiance_audio="雨声",
                    ),
                )
            ],
        )

        assert narration.content_mode == "narration"
        assert drama.content_mode == "drama"
        assert drama.scenes[0].duration_seconds == 8


class TestLLMSchemaExclusion:
    """LLM 看到的 JSON schema 必须排除 note / generated_assets / duration_override / 顶层 duration_seconds。"""

    def _walk(self, obj, *, path=""):
        """遍历 schema 树，yield (path, key) 对所有 properties 键。"""
        if isinstance(obj, dict):
            if "properties" in obj and isinstance(obj["properties"], dict):
                for key in obj["properties"]:
                    yield (path, key)
            for k, v in obj.items():
                yield from self._walk(v, path=f"{path}/{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                yield from self._walk(item, path=f"{path}[{i}]")

    def _all_keys(self, schema):
        return {key for _, key in self._walk(schema)}

    def test_narration_schema_excludes_runtime_fields(self):
        from lib.script_models import NarrationEpisodeScript

        keys = self._all_keys(NarrationEpisodeScript.model_json_schema())
        for forbidden in ("note", "generated_assets"):
            assert forbidden not in keys, f"{forbidden} 不应出现在 LLM schema 中"
        # 顶层 duration_seconds 由 caller 重算
        assert "duration_seconds" not in NarrationEpisodeScript.model_json_schema()["properties"]

    def test_drama_schema_excludes_runtime_fields(self):
        from lib.script_models import DramaEpisodeScript

        keys = self._all_keys(DramaEpisodeScript.model_json_schema())
        for forbidden in ("note", "generated_assets"):
            assert forbidden not in keys
        assert "duration_seconds" not in DramaEpisodeScript.model_json_schema()["properties"]

    def test_reference_video_schema_excludes_runtime_fields(self):
        from lib.script_models import ReferenceVideoScript

        keys = self._all_keys(ReferenceVideoScript.model_json_schema())
        for forbidden in ("note", "generated_assets", "duration_override"):
            assert forbidden not in keys
        assert "duration_seconds" not in ReferenceVideoScript.model_json_schema()["properties"]

    def test_runtime_fields_still_validate_in_python(self):
        """虽然 LLM 看不到，但 Python 端仍能 model_validate 含这些字段的旧数据（向后兼容）。"""
        from lib.script_models import NarrationSegment

        seg = NarrationSegment.model_validate(
            {
                "segment_id": "E1S1",
                "duration_seconds": 4,
                "novel_text": "x",
                "characters_in_segment": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                "note": "用户标注",
                "generated_assets": {"status": "completed", "video_clip": "videos/x.mp4"},
            }
        )
        assert seg.note == "用户标注"
        assert seg.generated_assets.status == "completed"
