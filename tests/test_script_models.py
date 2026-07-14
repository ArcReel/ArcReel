import pytest
from pydantic import ValidationError

from lib.script_models import (
    AdEpisodeScript,
    AdShot,
    Composition,
    Dialogue,
    DramaEpisodeScript,
    DramaScene,
    DramaVideoPrompt,
    ImagePrompt,
    NarrationEpisodeScript,
    NarrationSegment,
    Utterance,
    VideoPrompt,
)


def _image_prompt() -> ImagePrompt:
    return ImagePrompt(
        scene="场景",
        composition=Composition(shot_type="Medium Shot", lighting="暖光", ambiance="薄雾"),
    )


def _video_prompt() -> VideoPrompt:
    return VideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声")


def _drama_video_prompt() -> DramaVideoPrompt:
    return DramaVideoPrompt(action="转身", camera_motion="Static", ambiance_audio="风声")


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
            image_prompt=_image_prompt(),
            video_prompt=_drama_video_prompt(),
        )
        assert scene.scenes == ["庙宇"]
        assert scene.props == ["玉佩"]
        assert not hasattr(scene, "clues_in_scene")

    def test_drama_video_prompt_has_no_dialogue_field(self):
        """drama 用无-dialogue 变体：video_prompt 不携带 dialogue 字段（台词迁入 utterances）。"""
        assert "dialogue" not in DramaVideoPrompt.model_fields
        # narration / ad 共享的 VideoPrompt 仍保留 dialogue
        assert "dialogue" in VideoPrompt.model_fields

    def test_drama_scene_utterances_defaults_empty(self):
        """未提供 utterances 时默认空数组（无口播场景）。"""
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["王"],
            image_prompt=_image_prompt(),
            video_prompt=_drama_video_prompt(),
        )
        assert scene.utterances == []

    def test_drama_scene_utterances_round_trips_ordered(self):
        """utterances 按时序接受 dialogue / voiceover 混合条目并 round-trip 不丢、不重排。"""
        utterances = [
            Utterance(kind="voiceover", text="多年以后，她仍记得那个夜晚。"),
            Utterance(kind="dialogue", speaker="王", text="你来了。"),
            Utterance(kind="voiceover", text="那是命运的开端。"),
        ]
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["王"],
            image_prompt=_image_prompt(),
            video_prompt=_drama_video_prompt(),
            utterances=utterances,
        )
        dumped = scene.model_dump()
        assert dumped["utterances"] == [
            {"kind": "voiceover", "speaker": None, "text": "多年以后，她仍记得那个夜晚。"},
            {"kind": "dialogue", "speaker": "王", "text": "你来了。"},
            {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
        ]
        assert DramaScene.model_validate(dumped).utterances == utterances

    def test_utterance_dialogue_requires_speaker(self):
        """kind ⇄ speaker：dialogue 必带非空 speaker，缺失 / 空白则校验失败。"""
        with pytest.raises(ValidationError):
            Utterance(kind="dialogue", text="台词无说话人")
        with pytest.raises(ValidationError):
            Utterance(kind="dialogue", speaker="   ", text="空白说话人")

    def test_utterance_voiceover_rejects_speaker(self):
        """kind ⇄ speaker：voiceover 不得带 speaker。"""
        with pytest.raises(ValidationError):
            Utterance(kind="voiceover", speaker="王", text="画外音不该有说话人")

    def test_utterance_voiceover_blank_speaker_normalized_to_none(self):
        """voiceover 的空白 speaker 归一为 None（既可写 null 也可写 ""）。"""
        assert Utterance(kind="voiceover", speaker="", text="旁白").speaker is None
        assert Utterance(kind="voiceover", text="旁白").speaker is None

    def test_drama_scene_migrates_legacy_dialogue_and_voiceover(self):
        """存量 drama 读时迁移：旧 video_prompt.dialogue + voiceover 合成 utterances 并剥离旧字段。"""
        legacy = {
            "scene_id": "E1S01",
            "characters_in_scene": ["王"],
            "image_prompt": {
                "scene": "s",
                "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
            },
            "video_prompt": {
                "action": "a",
                "camera_motion": "Static",
                "ambiance_audio": "x",
                "dialogue": [{"speaker": "王", "line": "你来了。"}],
            },
            "voiceover": ["多年以后，她仍记得那个夜晚。"],
        }
        scene = DramaScene.model_validate(legacy)
        # dialogue 段在前、voiceover 段在后（确定性 best-effort）
        assert scene.utterances == [
            Utterance(kind="dialogue", speaker="王", text="你来了。"),
            Utterance(kind="voiceover", text="多年以后，她仍记得那个夜晚。"),
        ]
        # 旧字段被剥离：video_prompt 无 dialogue、场景无 voiceover 属性
        assert not hasattr(scene, "voiceover")
        assert "dialogue" not in scene.video_prompt.model_dump()

    def test_drama_scene_migrates_speakerless_legacy_dialogue_to_voiceover(self):
        """缺说话人的旧台词归为无说话人 voiceover（保内容、不编造 speaker、不致校验失败）。"""
        legacy = {
            "scene_id": "E1S01",
            "characters_in_scene": ["王"],
            "image_prompt": {
                "scene": "s",
                "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
            },
            "video_prompt": {
                "action": "a",
                "camera_motion": "Static",
                "ambiance_audio": "x",
                "dialogue": [{"speaker": "", "line": "无主台词"}],
            },
        }
        scene = DramaScene.model_validate(legacy)
        assert scene.utterances == [Utterance(kind="voiceover", text="无主台词")]

    def test_drama_scene_rejects_dialogue_in_video_prompt_for_new_data(self):
        """新数据（utterances 已在）不再迁移：video_prompt 残留 dialogue 触发 extra='forbid'。"""
        with pytest.raises(ValidationError):
            DramaScene.model_validate(
                {
                    "scene_id": "E1S01",
                    "characters_in_scene": ["王"],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {
                        "action": "a",
                        "camera_motion": "Static",
                        "ambiance_audio": "x",
                        "dialogue": [{"speaker": "王", "line": "x"}],
                    },
                    "utterances": [],
                }
            )

    def test_drama_scene_rejects_unknown_field(self):
        """extra='forbid' 守卫仍生效：utterances 不放松未知字段拒绝。"""
        with pytest.raises(ValidationError):
            DramaScene.model_validate(
                {
                    "scene_id": "E1S01",
                    "characters_in_scene": ["王"],
                    "image_prompt": {
                        "scene": "s",
                        "composition": {"shot_type": "Medium Shot", "lighting": "l", "ambiance": "a"},
                    },
                    "video_prompt": {"action": "a", "camera_motion": "Static", "ambiance_audio": "x"},
                    "utterances": [],
                    "hallucinated_field": "x",
                }
            )

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
            image_prompt=_image_prompt(),
            video_prompt=_drama_video_prompt(),
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
                    image_prompt=_image_prompt(),
                    video_prompt=_drama_video_prompt(),
                )
            ],
        )

        assert narration.content_mode == "narration"
        assert drama.content_mode == "drama"
        assert drama.scenes[0].duration_seconds == 8


class TestAdScriptModels:
    """广告/短片模式剧本骨架：平铺 shots[]，口播文案一等。"""

    def test_ad_shot_carries_section_and_voiceover(self):
        shot = AdShot(
            shot_id="E1S01",
            section="hook",
            duration_seconds=3,
            voiceover_text="三秒钟告诉你为什么离不开它",
            image_prompt=_image_prompt(),
            video_prompt=_video_prompt(),
        )
        assert shot.section == "hook"
        assert shot.voiceover_text == "三秒钟告诉你为什么离不开它"
        assert shot.products_in_shot == []
        assert shot.characters_in_shot == []
        assert shot.scenes == []
        assert shot.props == []
        assert shot.transition_to_next == "cut"
        assert shot.generated_assets.status == "pending"

    def test_ad_shot_requires_voiceover_text_field(self):
        with pytest.raises(ValidationError):
            AdShot.model_validate(
                {
                    "shot_id": "E1S01",
                    "section": "hook",
                    "duration_seconds": 3,
                    "image_prompt": _image_prompt(),
                    "video_prompt": _video_prompt(),
                }
            )

    def test_ad_episode_script_builds_with_shots(self):
        script = AdEpisodeScript(
            title="新品速干杯",
            shots=[
                AdShot(
                    shot_id="E1S01",
                    section="hook",
                    duration_seconds=3,
                    voiceover_text="开场口播",
                    products_in_shot=["速干杯"],
                    image_prompt=_image_prompt(),
                    video_prompt=_video_prompt(),
                )
            ],
        )
        assert script.content_mode == "ad"
        assert script.shots[0].products_in_shot == ["速干杯"]

    def test_ad_shot_rejects_unknown_fields(self):
        with pytest.raises(ValidationError):
            AdShot.model_validate(
                {
                    "shot_id": "E1S01",
                    "section": "hook",
                    "duration_seconds": 3,
                    "voiceover_text": "口播",
                    "image_prompt": _image_prompt(),
                    "video_prompt": _video_prompt(),
                    "hallucinated_field": "x",
                }
            )


class TestEnumDriftNormalization:
    """非约束解码通道（代理网关等）下的枚举风格漂移归一。

    schema 的 enum 只有在供应商执行约束解码时才是硬约束；代理网关/兼容通道放任模型
    自由发挥时，枚举值会漂移成大写/小写蛇形（MEDIUM_SHOT / medium_shot）甚至
    词表外近义词（wide_shot / dolly_in / orbit）。校验层做机械归一 + 别名映射 +
    未知值降级默认，避免可挽救的风格漂移让整集剧本生成失败。
    """

    @staticmethod
    def _composition(shot_type: str) -> dict:
        return {"shot_type": shot_type, "lighting": "夕阳侧光", "ambiance": "广场人群环绕"}

    @staticmethod
    def _video_prompt(camera_motion: str, **extra: object) -> dict:
        return {"action": "旋转舞动", "camera_motion": camera_motion, "ambiance_audio": "广场音乐", **extra}

    @pytest.mark.parametrize(
        ("drifted", "expected"),
        [
            ("MEDIUM_SHOT", "Medium Shot"),
            ("medium_shot", "Medium Shot"),
            ("CLOSE_UP", "Close-up"),
            ("MEDIUM_CLOSE_UP", "Medium Close-up"),
            ("medium_long_shot", "Medium Long Shot"),
            ("extreme  close-up", "Extreme Close-up"),
        ],
    )
    def test_shot_type_normalizes_case_and_separators(self, drifted: str, expected: str):
        comp = Composition.model_validate(self._composition(drifted))
        assert comp.shot_type == expected

    @pytest.mark.parametrize(
        ("drifted", "expected"),
        [
            ("ZOOM_OUT", "Zoom Out"),
            ("static", "Static"),
            ("TILT_UP", "Tilt Up"),
            ("pan_right", "Pan Right"),
            ("tracking shot", "Tracking Shot"),
            ("PUSH_IN", "Push In"),
            ("truck-left", "Truck Left"),
            ("pedestal_down", "Pedestal Down"),
            ("orbit", "Orbit"),
        ],
    )
    def test_camera_motion_normalizes_case_and_separators(self, drifted: str, expected: str):
        vp = VideoPrompt.model_validate(self._video_prompt(drifted))
        assert vp.camera_motion == expected

    @pytest.mark.parametrize("drifted", ["WIDE_SHOT", "第一视角特写"])
    def test_out_of_vocab_shot_type_falls_back_to_default_with_warning(self, caplog, drifted: str):
        """词表外值不做语义近义映射（穷举不全），一律降级默认并 warn 保留原值。"""
        with caplog.at_level("WARNING", logger="lib.script_models"):
            comp = Composition.model_validate(self._composition(drifted))
        assert comp.shot_type == "Medium Shot"
        assert drifted in caplog.text

    @pytest.mark.parametrize("drifted", ["dolly_in", "crane_up_spiral"])
    def test_out_of_vocab_camera_motion_falls_back_to_default_with_warning(self, caplog, drifted: str):
        with caplog.at_level("WARNING", logger="lib.script_models"):
            vp = VideoPrompt.model_validate(self._video_prompt(drifted))
        assert vp.camera_motion == "Static"
        assert drifted in caplog.text

    def test_canonical_values_pass_through_unchanged(self):
        comp = Composition.model_validate(self._composition("Over-the-shoulder"))
        assert comp.shot_type == "Over-the-shoulder"
        vp = VideoPrompt.model_validate(self._video_prompt("Pan Left"))
        assert vp.camera_motion == "Pan Left"

    def test_non_string_enum_still_rejected(self):
        with pytest.raises(ValidationError):
            Composition.model_validate(self._composition(123))  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            VideoPrompt.model_validate(self._video_prompt(None))  # type: ignore[arg-type]

    def test_dialogue_null_coerces_to_empty_list(self):
        vp = VideoPrompt.model_validate(self._video_prompt("Static", dialogue=None))
        assert vp.dialogue == []

    def test_llm_schema_still_declares_enum(self):
        """BeforeValidator 不得改变 LLM 侧 schema：enum 仍是约束解码通道的硬约束。"""
        comp_schema = Composition.model_json_schema()
        assert comp_schema["properties"]["shot_type"]["enum"] == [
            "Extreme Close-up",
            "Close-up",
            "Medium Close-up",
            "Medium Shot",
            "Medium Long Shot",
            "Long Shot",
            "Extreme Long Shot",
            "Over-the-shoulder",
            "Point-of-view",
        ]
        vp_schema = VideoPrompt.model_json_schema()
        assert vp_schema["properties"]["camera_motion"]["enum"] == [
            "Static",
            "Pan Left",
            "Pan Right",
            "Tilt Up",
            "Tilt Down",
            "Zoom In",
            "Zoom Out",
            "Push In",
            "Pull Out",
            "Truck Left",
            "Truck Right",
            "Pedestal Up",
            "Pedestal Down",
            "Orbit",
            "Tracking Shot",
            "Shake",
        ]


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
        # utterances 是 LLM 可见的一等字段（drama 口播序列的落点），取代旧 voiceover
        assert "utterances" in keys
        assert "voiceover" not in keys

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

    def test_schema_excludes_hook_and_teaser_including_derived_models(self):
        """hook / next_episode_teaser 由分集账本注入，LLM 不该看到——
        含 build_*_script_model 动态约束子类（response_schema 实际取自它们）。"""
        from lib.script_models import (
            DramaEpisodeScript,
            NarrationEpisodeScript,
            ReferenceVideoScript,
            build_episode_script_model,
            build_reference_video_script_model,
        )

        models = (
            NarrationEpisodeScript,
            DramaEpisodeScript,
            ReferenceVideoScript,
            build_episode_script_model("narration", [4, 6, 8]),
            build_episode_script_model("drama", [4, 6, 8]),
            build_reference_video_script_model([4, 8]),
        )
        for model in models:
            top_props = set(model.model_json_schema()["properties"].keys())
            assert "hook" not in top_props, f"{model.__name__} 顶层不应有 hook"
            assert "next_episode_teaser" not in top_props, f"{model.__name__} 顶层不应有 next_episode_teaser"


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
