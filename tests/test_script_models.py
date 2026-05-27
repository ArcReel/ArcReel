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
            novel={"title": "小说", "chapter": "1"},
            segments=[],
        )
        drama = DramaEpisodeScript(
            title="第一集",
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

    def test_schema_excludes_scene_type_summary_content_mode_novel_transition(self):
        """LLM 不该看到 scene_type / summary / content_mode / novel / transition_to_next。

        前 4 个由 _add_metadata 注入或彻底无消费；transition_to_next 由 Pydantic default="cut"
        兜底,FE PATCH 路径独立。
        """
        from lib.script_models import (
            DramaEpisodeScript,
            NarrationEpisodeScript,
            ReferenceVideoScript,
        )

        for model in (NarrationEpisodeScript, DramaEpisodeScript, ReferenceVideoScript):
            schema = model.model_json_schema()
            keys = self._all_keys(schema)
            top_props = set(schema["properties"].keys())
            assert "summary" not in top_props, f"{model.__name__} 顶层不应有 summary"
            assert "novel" not in top_props, f"{model.__name__} 顶层不应有 novel"
            assert "content_mode" not in top_props, f"{model.__name__} 顶层不应有 content_mode"
            assert "scene_type" not in keys, f"{model.__name__} 不应有 scene_type"
            assert "transition_to_next" not in keys, f"{model.__name__} 不应有 transition_to_next"


class TestRuntimeBackwardCompat:
    """LLM schema 隐藏的字段在 Python 端 model_validate 时仍能接受旧数据,并由 default 兜底。"""

    def test_drama_scene_accepts_legacy_scene_type_field(self):
        """存量项目里残留 scene_type 字段不该让 model_validate 炸。"""
        scene = DramaScene.model_validate(
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "characters_in_scene": ["王"],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                "scene_type": "对话",
            }
        )
        assert scene.scene_id == "E1S01"
        assert not hasattr(scene, "scene_type")

    def test_narration_segment_accepts_legacy_clues_in_segment_field(self):
        """v0→v1 migration 删的 clues_in_segment 残留时 model_validate 不该炸。"""
        segment = NarrationSegment.model_validate(
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "原文",
                "characters_in_segment": ["王"],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                "clues_in_segment": ["玉佩"],
            }
        )
        assert segment.segment_id == "E1S01"
        assert not hasattr(segment, "clues_in_segment")

    def test_drama_scene_accepts_legacy_clues_in_scene_field(self):
        """v0→v1 migration 删的 clues_in_scene 残留时 model_validate 不该炸。"""
        scene = DramaScene.model_validate(
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "characters_in_scene": ["王"],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                "clues_in_scene": ["玉佩"],
            }
        )
        assert scene.scene_id == "E1S01"
        assert not hasattr(scene, "clues_in_scene")

    def test_episode_models_validate_without_optional_fields(self):
        """LLM 不写 content_mode / novel / summary 时,model_validate 仍应成功并用 default 兜底。"""
        drama = DramaEpisodeScript.model_validate(
            {
                "title": "第一集",
                "scenes": [
                    {
                        "scene_id": "E1S01",
                        "characters_in_scene": ["A"],
                        "image_prompt": {
                            "scene": "s",
                            "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                        },
                        "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                    }
                ],
            }
        )
        assert drama.content_mode == "drama"
        assert drama.novel.title == ""
        assert drama.novel.chapter == ""

        narration = NarrationEpisodeScript.model_validate(
            {
                "title": "第一集",
                "segments": [],
            }
        )
        assert narration.content_mode == "narration"
        assert narration.novel.title == ""

    def test_segment_transition_to_next_defaults_to_cut(self):
        """LLM 不写 transition_to_next 时,default='cut' 兜底。"""
        seg = NarrationSegment.model_validate(
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "x",
                "characters_in_segment": [],
                "image_prompt": {
                    "scene": "s",
                    "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                },
                "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
            }
        )
        assert seg.transition_to_next == "cut"


class TestGeneratedAssetsTemplateContract:
    """GeneratedAssets 模型与 create_generated_assets() dict 模板必须保持字段一致。

    模型开 extra="forbid" 后,运行时回写若出现模型未声明的字段,会被 _guard_no_worse
    在 before/after 差集中检测为 extra_forbidden 拒整集写盘——例如视频生成完成后
    reference_video_tasks 在 ga 上写 "video_thumbnail" 时整集拒。本测试守住「模板
    写入字段⊆模型声明字段」契约。
    """

    def test_template_dict_validates_against_generated_assets_model(self):
        from lib.project_manager import ProjectManager
        from lib.script_models import GeneratedAssets

        # 不抛即通过——template 任何 key 不在模型字段集时 extra="forbid" 会抛 ValidationError
        GeneratedAssets.model_validate(ProjectManager.create_generated_assets())
        GeneratedAssets.model_validate(ProjectManager.create_generated_assets("drama"))

    def test_video_thumbnail_runtime_write_passes_strict_validation(self):
        """reference_video_tasks 在视频生成后会写 ga['video_thumbnail'],模型必须接受。"""
        from lib.script_models import GeneratedAssets

        GeneratedAssets.model_validate(
            {
                "storyboard_image": "scenes/E1S01.png",
                "video_clip": "videos/E1S01.mp4",
                "video_thumbnail": "thumbnails/E1S01.jpg",
                "video_uri": "https://example/v",
                "status": "completed",
            }
        )
