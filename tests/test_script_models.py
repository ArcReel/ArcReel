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
            episode=1,
            duration_seconds=4,
            novel_text="原文",
            characters_in_segment=["姜月茴"],
            clues_in_segment=["玉佩"],
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

    def test_invalid_duration_raises_validation_error(self):
        with pytest.raises(ValidationError):
            NarrationSegment(
                segment_id="E1S01",
                episode=1,
                duration_seconds=5,
                novel_text="原文",
                characters_in_segment=["姜月茴"],
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
                ),
            )

    def test_episode_models_build_successfully(self):
        narration = NarrationEpisodeScript(
            episode=1,
            title="第一集",
            summary="摘要",
            novel={"title": "小说", "chapter": "1"},
            segments=[],
        )
        drama = DramaEpisodeScript(
            episode=1,
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
