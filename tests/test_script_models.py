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
            novel_text="original text",
            characters_in_segment=["Character A"],
            clues_in_segment=["jade-pendant"],
            image_prompt=ImagePrompt(
                scene="scene description",
                composition=Composition(
                    shot_type="Medium Shot",
                    lighting="warm light",
                    ambiance="thin mist",
                ),
            ),
            video_prompt=VideoPrompt(
                action="turns around",
                camera_motion="Static",
                ambiance_audio="wind sound",
                dialogue=[Dialogue(speaker="Character A", line="Wait")],
            ),
        )

        assert segment.transition_to_next == "cut"
        assert segment.generated_assets.status == "pending"

    def test_duration_accepts_any_positive_int_within_range(self):
        """duration_seconds accepts any integer within the 1-60 range."""
        segment = NarrationSegment(
            segment_id="E1S01",
            episode=1,
            duration_seconds=10,  # previously would be rejected by DurationSeconds
            novel_text="original text",
            characters_in_segment=["Character A"],
            image_prompt=ImagePrompt(
                scene="scene description",
                composition=Composition(shot_type="Medium Shot", lighting="warm light", ambiance="thin mist"),
            ),
            video_prompt=VideoPrompt(action="turns around", camera_motion="Static", ambiance_audio="wind sound"),
        )
        assert segment.duration_seconds == 10

    def test_duration_rejects_out_of_range(self):
        """duration_seconds rejects out-of-range values."""
        with pytest.raises(ValidationError):
            NarrationSegment(
                segment_id="E1S01",
                episode=1,
                duration_seconds=0,
                novel_text="original text",
                characters_in_segment=["Character A"],
                image_prompt=ImagePrompt(
                    scene="scene description",
                    composition=Composition(shot_type="Medium Shot", lighting="warm light", ambiance="thin mist"),
                ),
                video_prompt=VideoPrompt(action="turns around", camera_motion="Static", ambiance_audio="wind sound"),
            )
        with pytest.raises(ValidationError):
            NarrationSegment(
                segment_id="E1S01",
                episode=1,
                duration_seconds=61,
                novel_text="original text",
                characters_in_segment=["Character A"],
                image_prompt=ImagePrompt(
                    scene="scene description",
                    composition=Composition(shot_type="Medium Shot", lighting="warm light", ambiance="thin mist"),
                ),
                video_prompt=VideoPrompt(action="turns around", camera_motion="Static", ambiance_audio="wind sound"),
            )

    def test_drama_scene_default_duration_is_8(self):
        """DramaScene default duration_seconds is still 8."""
        scene = DramaScene(
            scene_id="E1S01",
            characters_in_scene=["Character A"],
            image_prompt=ImagePrompt(
                scene="scene description",
                composition=Composition(shot_type="Medium Shot", lighting="warm light", ambiance="thin mist"),
            ),
            video_prompt=VideoPrompt(action="moves forward", camera_motion="Static", ambiance_audio="rain sound"),
        )
        assert scene.duration_seconds == 8

    def test_episode_models_build_successfully(self):
        narration = NarrationEpisodeScript(
            episode=1,
            title="Episode 1",
            summary="Summary",
            novel={"title": "Novel", "chapter": "1"},
            segments=[],
        )
        drama = DramaEpisodeScript(
            episode=1,
            title="Episode 1",
            summary="Summary",
            novel={"title": "Novel", "chapter": "1"},
            scenes=[
                DramaScene(
                    scene_id="E1S01",
                    characters_in_scene=["Character A"],
                    image_prompt=ImagePrompt(
                        scene="scene description",
                        composition=Composition(
                            shot_type="Medium Shot",
                            lighting="warm light",
                            ambiance="thin mist",
                        ),
                    ),
                    video_prompt=VideoPrompt(
                        action="moves forward",
                        camera_motion="Static",
                        ambiance_audio="rain sound",
                    ),
                )
            ],
        )

        assert narration.content_mode == "narration"
        assert drama.content_mode == "drama"
        assert drama.scenes[0].duration_seconds == 8
