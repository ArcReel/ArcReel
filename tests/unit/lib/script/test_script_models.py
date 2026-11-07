import pytest
from pydantic import ValidationError

from lib.script.script_models import (
    _ITEM_FALLBACK_DURATIONS,
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
    NarrationVisualEpisodeScript,
    NarrationVisualSegment,
    Utterance,
    VideoPrompt,
    get_generated_assets,
    item_duration,
    resolve_content_mode,
    script_duration_total,
)


class TestGetGeneratedAssets:
    def test_returns_dict_container_unchanged(self):
        item = {"generated_assets": {"storyboard_image": "storyboards/scene_E1S01.png"}}
        assert get_generated_assets(item) == {"storyboard_image": "storyboards/scene_E1S01.png"}

    @pytest.mark.parametrize("dirty", [["bad"], "bad", 123, None, 0, False])
    def test_normalizes_non_dict_container_to_empty_dict(self, dirty):
        assert get_generated_assets({"generated_assets": dirty}) == {}

    def test_missing_key_returns_empty_dict(self):
        assert get_generated_assets({}) == {}


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
        """Drama uses the nondialogue variant: video_prompt has no dialogue field, moved into utterances."""
        assert "dialogue" not in DramaVideoPrompt.model_fields
        # Narration / ad shared VideoPrompt retains dialogue.
        assert "dialogue" in VideoPrompt.model_fields

    def test_drama_scene_utterances_defaults_empty(self):
        """Absent utterances defaults to an empty array for scenes without speech."""
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["王"],
            image_prompt=_image_prompt(),
            video_prompt=_drama_video_prompt(),
        )
        assert scene.utterances == []

    def test_drama_scene_utterances_round_trips_ordered(self):
        """utterances accepts chronological dialogue / voiceover mixes and round-trips without loss or reordering."""
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
        """kind ⇄ speaker: dialogue requires a nonempty speaker; absent/whitespace values fail validation."""
        with pytest.raises(ValidationError):
            Utterance(kind="dialogue", text="台词无说话人")
        with pytest.raises(ValidationError):
            Utterance(kind="dialogue", speaker="   ", text="空白说话人")

    def test_utterance_voiceover_rejects_speaker(self):
        """kind ⇄ speaker: voiceover must not carry a speaker."""
        with pytest.raises(ValidationError):
            Utterance(kind="voiceover", speaker="王", text="画外音不该有说话人")

    def test_utterance_voiceover_blank_speaker_normalized_to_none(self):
        """Whitespace voiceover speakers normalize to None, accepting null or ""."""
        assert Utterance(kind="voiceover", speaker="", text="旁白").speaker is None
        assert Utterance(kind="voiceover", text="旁白").speaker is None

    def test_drama_scene_migrates_legacy_dialogue_and_voiceover(self):
        """Read-time legacy drama migration combines video_prompt.dialogue and voiceover into utterances and removes old fields."""
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
        # Dialogue precedes voiceover, deterministic best-effort ordering.
        assert scene.utterances == [
            Utterance(kind="dialogue", speaker="王", text="你来了。"),
            Utterance(kind="voiceover", text="多年以后，她仍记得那个夜晚。"),
        ]
        # Strip legacy fields: no video_prompt dialogue or scene voiceover attribute.
        assert not hasattr(scene, "voiceover")
        assert "dialogue" not in scene.video_prompt.model_dump()

    def test_drama_scene_migrates_speakerless_legacy_dialogue_to_voiceover(self):
        """Legacy dialogue without speakers becomes speakerless voiceover, retaining content without inventing speakers or failing validation."""
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
        """Do not migrate new data with utterances already present; leftover video_prompt dialogue triggers extra='forbid'."""
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
        """extra='forbid' still guards unknown fields; utterances does not relax rejection."""
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
        """duration_seconds accepts any integer within 1-60."""
        segment = NarrationSegment(
            segment_id="E1S01",
            duration_seconds=10,  # Previously rejected by DurationSeconds.
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
        """duration_seconds rejects out-of-range values."""
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
        """DramaScene duration_seconds still defaults to 8."""
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


class TestNarrationVisualLayerHasNoDialogue:
    """Narration/commentary segments contain no dialogue (ADR 0089); prompt-authoring visuals cannot produce nonempty dialogue."""

    @staticmethod
    def _visual_segment(**video_prompt_overrides) -> dict:
        video_prompt = {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声", **video_prompt_overrides}
        return {
            "segment_id": "E1S01",
            "image_prompt": _image_prompt().model_dump(),
            "video_prompt": video_prompt,
        }

    def test_response_schema_does_not_expose_dialogue(self):
        schema = NarrationVisualEpisodeScript.model_json_schema()
        video_prompt_refs = [
            definition for definition in schema["$defs"].values() if "camera_motion" in definition.get("properties", {})
        ]
        assert video_prompt_refs
        for definition in video_prompt_refs:
            assert "dialogue" not in definition["properties"]

    def test_rejects_non_empty_dialogue(self):
        with pytest.raises(ValidationError):
            NarrationVisualSegment.model_validate(
                self._visual_segment(dialogue=[{"speaker": "姜月茴", "line": "等等"}])
            )

    @pytest.mark.parametrize("drifted", [[], None])
    def test_tolerates_empty_or_null_dialogue_as_empty(self, drifted):
        segment = NarrationVisualSegment.model_validate(self._visual_segment(dialogue=drifted))
        assert segment.model_dump()["video_prompt"]["dialogue"] == []

    def test_omitted_dialogue_dumps_as_empty(self):
        segment = NarrationVisualSegment.model_validate(self._visual_segment())
        assert segment.model_dump()["video_prompt"]["dialogue"] == []

    def test_stored_narration_segment_still_loads_existing_dialogue(self):
        """Read legacy segments containing dialogue normally; speech admission classifies mixed_speech instead of discarding content at model level."""
        segment = NarrationSegment.model_validate(
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "原文",
                "characters_in_segment": ["姜月茴"],
                "video_prompt": self._visual_segment(dialogue=[{"speaker": "姜月茴", "line": "等等"}])["video_prompt"],
            }
        )
        assert isinstance(segment.video_prompt, VideoPrompt)
        assert segment.video_prompt.dialogue == [Dialogue(speaker="姜月茴", line="等等")]


class TestAdScriptModels:
    """Ad/short-film skeleton: flat shots[] with first-class spoken copy."""

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

    def test_pending_ad_shot_may_omit_prompts(self):
        """Manually added pending-authoring storyboards validate without prompt fields; other storyboards still require both prompts."""
        base = {"shot_id": "E1S02", "section": "demo", "duration_seconds": 4, "voiceover_text": "口播"}

        pending = AdShot.model_validate({**base, "pending_authoring": True})

        assert (pending.image_prompt, pending.video_prompt) == (None, None)
        with pytest.raises(ValidationError):
            AdShot.model_validate(base)

    def test_ad_response_schema_still_requires_prompts(self):
        required = AdEpisodeScript.model_json_schema()["$defs"]["AdShot"]["required"]

        assert {"image_prompt", "video_prompt"} <= set(required)

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


class TestItemDurationCoercion:
    """Normalize invalid script-item durations through one source shared by read calculation, persistence recomputation, and stored estimates.

    Raw dicts saved after validation degradation can bring None/bool/negative/string/absent values to reads. Historically,
    three fallback tables and guards differed: missing None guards caused sum() TypeError and project-list API 5xx.
    Enumerate invalid-value matrices × four skeletons to pin unified behavior after consolidation.
    """

    def test_fallback_table_covers_four_skeletons(self):
        assert _ITEM_FALLBACK_DURATIONS == {"segments": 4, "scenes": 8, "shots": 0, "video_units": 0}

    @pytest.mark.parametrize("kind", ["segments", "scenes", "shots", "video_units"])
    def test_valid_positive_int_passes_through(self, kind: str):
        assert item_duration(kind, {"duration_seconds": 7}) == 7

    @pytest.mark.parametrize("kind", ["segments", "scenes", "shots", "video_units"])
    @pytest.mark.parametrize(
        "dirty",
        [
            {},  # Missing.
            {"duration_seconds": None},  # None
            {"duration_seconds": True},  # bool is an int subclass and must be excluded.
            {"duration_seconds": False},
            {"duration_seconds": 0},  # Nonpositive.
            {"duration_seconds": -5},  # Negative.
            {"duration_seconds": "6"},  # Numeric string.
            {"duration_seconds": "six"},  # Nonnumeric string.
            {"duration_seconds": 4.5},  # Float.
        ],
    )
    def test_dirty_values_fall_back_by_kind(self, kind: str, dirty: dict):
        # Missing and invalid values use skeleton fallback durations (0 for shots/video_units).
        assert item_duration(kind, dirty) == _ITEM_FALLBACK_DURATIONS[kind]

    @pytest.mark.parametrize("kind", ["segments", "scenes", "shots", "video_units"])
    def test_non_dict_item_counts_zero(self, kind: str):
        # Nondict items have no duration semantics; always count 0 without borrowing skeleton fallback.
        for junk in ("junk", 5, None, ["x"]):
            assert item_duration(kind, junk) == 0

    def test_script_total_sums_mixed_dirty_items(self):
        # Mixed invalid items sum without raising: 5 valid + 0 nondict + 4 missing + 4 None + 4 negative = 17.
        items = [
            {"duration_seconds": 5},
            "junk_not_a_dict",
            {},
            {"duration_seconds": None},
            {"duration_seconds": -5},
        ]
        assert script_duration_total("segments", items) == 17

    def test_script_total_video_units_missing_is_zero(self):
        # Pin video_units missing-duration fallback to 0, replacing the historical 8-second else fallback.
        assert script_duration_total("video_units", [{}, {"duration_seconds": None}]) == 0
        assert script_duration_total("video_units", [{"duration_seconds": 6}, {}]) == 6

    @pytest.mark.parametrize("kind", ["segments", "scenes", "shots", "video_units"])
    def test_script_total_non_list_is_zero(self, kind: str):
        # Treat null / truthy-scalar items from degraded persistence as empty; return 0 without raising.
        for junk in (None, "segments", 5, {"a": 1}):
            assert script_duration_total(kind, junk) == 0


class TestEnumDriftNormalization:
    """Normalize enum style drift on unconstrained-decoding channels, such as proxy gateways.

    Schema enum is hard only when providers enforce constrained decoding. Gateways/compatible channels permitting
    free output can drift into upper/lower snake case (MEDIUM_SHOT / medium_shot) or out-of-vocabulary
    synonyms (wide_shot / dolly_in / orbit). Validation mechanically normalizes, maps aliases, and
    defaults unknown values, preventing recoverable style drift from failing whole-episode generation.
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
        """Do not semantically map unknown synonyms, which cannot be exhaustively listed; default all unknown values and warn with the original."""
        with caplog.at_level("WARNING", logger="lib.script.script_models"):
            comp = Composition.model_validate(self._composition(drifted))
        assert comp.shot_type == "Medium Shot"
        assert drifted in caplog.text

    @pytest.mark.parametrize("drifted", ["dolly_in", "crane_up_spiral"])
    def test_out_of_vocab_camera_motion_falls_back_to_default_with_warning(self, caplog, drifted: str):
        with caplog.at_level("WARNING", logger="lib.script.script_models"):
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
            Composition.model_validate(self._composition(123))
        with pytest.raises(ValidationError):
            VideoPrompt.model_validate(self._video_prompt(None))

    def test_dialogue_null_coerces_to_empty_list(self):
        vp = VideoPrompt.model_validate(self._video_prompt("Static", dialogue=None))
        assert vp.dialogue == []

    def test_llm_schema_still_declares_enum(self):
        """BeforeValidator must not change LLM schemas; enum remains hard for constrained-decoding channels."""
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
    """LLM-visible JSON schemas must exclude note / generated_assets / duration_override / top-level duration_seconds."""

    def _walk(self, obj, *, path=""):
        """Traverse schema trees, yielding (path, key) for every properties key."""
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
        from lib.script.script_models import NarrationEpisodeScript

        keys = self._all_keys(NarrationEpisodeScript.model_json_schema())
        for forbidden in ("note", "generated_assets", "end_frame_image", "pending_authoring"):
            assert forbidden not in keys, f"{forbidden} 不应出现在 LLM schema 中"
        # Callers recompute top-level duration_seconds.
        assert "duration_seconds" not in NarrationEpisodeScript.model_json_schema()["properties"]

    def test_drama_schema_excludes_runtime_fields(self):
        from lib.script.script_models import DramaEpisodeScript

        keys = self._all_keys(DramaEpisodeScript.model_json_schema())
        for forbidden in ("note", "generated_assets", "end_frame_image", "pending_authoring"):
            assert forbidden not in keys
        assert "duration_seconds" not in DramaEpisodeScript.model_json_schema()["properties"]
        # utterances is first-class and LLM-visible for drama speech sequences, replacing old voiceover.
        assert "utterances" in keys
        assert "voiceover" not in keys

    def test_ad_schema_excludes_runtime_fields(self):
        from lib.script.script_models import AdEpisodeScript

        keys = self._all_keys(AdEpisodeScript.model_json_schema())
        for forbidden in ("note", "generated_assets", "end_frame_image", "pending_authoring"):
            assert forbidden not in keys

    def test_reference_video_schema_excludes_runtime_fields(self):
        from lib.script.script_models import ReferenceVideoScript

        keys = self._all_keys(ReferenceVideoScript.model_json_schema())
        for forbidden in ("note", "generated_assets", "duration_override", "pending_authoring"):
            assert forbidden not in keys
        assert "duration_seconds" not in ReferenceVideoScript.model_json_schema()["properties"]

    def test_runtime_fields_still_validate_in_python(self):
        """Despite LLM invisibility, Python model_validate still accepts old data with these fields for backward compatibility."""
        from lib.script.script_models import NarrationSegment

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

    def test_schema_excludes_scene_type_summary_content_mode_novel(self):
        """LLMs must not see scene_type / summary / content_mode / novel, injected by _add_metadata or entirely unused."""
        from lib.script.script_models import (
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

    def test_schema_excludes_hook_and_teaser_including_derived_models(self):
        """Episode ledgers inject hook / next_episode_teaser, hidden from LLMs,
        including dynamic build_*_script_model constrained subclasses actually used as response_schema."""
        from lib.script.script_models import (
            DramaEpisodeScript,
            NarrationEpisodeScript,
            ReferenceVideoScript,
            build_episode_script_model,
        )

        models = (
            NarrationEpisodeScript,
            DramaEpisodeScript,
            ReferenceVideoScript,
            build_episode_script_model("narration", [4, 6, 8]),
            build_episode_script_model("drama", [4, 6, 8]),
        )
        for model in models:
            top_props = set(model.model_json_schema()["properties"].keys())
            assert "hook" not in top_props, f"{model.__name__} 顶层不应有 hook"
            assert "next_episode_teaser" not in top_props, f"{model.__name__} 顶层不应有 next_episode_teaser"


class TestRuntimeBackwardCompat:
    """LLM-hidden schema fields still accept legacy Python model_validate input and have default fallbacks."""

    def test_drama_scene_accepts_legacy_scene_type_field(self):
        """Legacy scene_type must not break model_validate."""
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
        """Remaining clues_in_segment, removed by v0→v1 migration, must not break model_validate."""
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
        """Remaining clues_in_scene, removed by v0→v1 migration, must not break model_validate."""
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
        """Without LLM-written content_mode / novel / summary, model_validate still succeeds using defaults."""
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

    def test_segment_rejects_transition(self):
        """Transitions do not belong to script items; reject transition_to_next as an unknown storyboard field."""
        with pytest.raises(ValidationError):
            NarrationSegment.model_validate(
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
                    "transition_to_next": "cut",
                }
            )


class TestGeneratedAssetsTemplateContract:
    """GeneratedAssets and create_generated_assets() dict templates must agree on fields.

    With extra="forbid", runtime writeback of undeclared fields becomes extra_forbidden in _guard_no_worse
    before/after differences, rejecting whole-episode persistence. For example, video completion could fail when
    reference_video_tasks writes "video_thumbnail" into ga. Guard the contract that template-written
    fields are a subset of model declarations.
    """

    def test_template_dict_validates_against_generated_assets_model(self):
        from lib.project.project_manager import ProjectManager
        from lib.script.script_models import GeneratedAssets

        # extra="forbid" raises for any undeclared template key; assert the validated object contains every template key.
        for template in (ProjectManager.create_generated_assets(), ProjectManager.create_generated_assets("drama")):
            validated = GeneratedAssets.model_validate(template)
            assert set(template) <= set(validated.model_dump())

    def test_video_thumbnail_runtime_write_passes_strict_validation(self):
        """reference_video_tasks writes ga['video_thumbnail'] after video generation; the model must accept it."""
        from lib.script.script_models import GeneratedAssets

        validated = GeneratedAssets.model_validate(
            {
                "storyboard_image": "scenes/E1S01.png",
                "video_clip": "videos/E1S01.mp4",
                "video_thumbnail": "thumbnails/E1S01.jpg",
                "video_uri": "https://example/v",
                "status": "completed",
            }
        )

        assert validated.video_thumbnail == "thumbnails/E1S01.jpg"


class TestResolveContentMode:
    """Absent episode/script content_mode falls back to project configuration, matching
    existing semantics already accepted by lib.project.data_validator._validate_episode_payload."""

    def test_episode_level_wins_when_present(self):
        assert resolve_content_mode({"content_mode": "drama"}, {"content_mode": "narration"}) == "drama"

    def test_falls_back_to_project_when_episode_omits_it(self):
        assert resolve_content_mode({}, {"content_mode": "drama"}) == "drama"

    def test_falls_back_to_narration_when_both_omit_it(self):
        assert resolve_content_mode({}, {}) == "narration"
