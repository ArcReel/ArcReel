"""供应商模型的生效模型事实：能力覆盖 > 对应条目 > 运行时回落。"""

from __future__ import annotations

import pytest

from arcreel_catalog_core import DurationConstraints
from lib.custom_provider.model_facts import (
    ImageModelFacts,
    TextModelFacts,
    image_model_facts,
    model_fact_overrides,
    model_fact_value_matches,
    text_model_facts,
    video_model_facts,
)

VEO = "vendor/Veo-3.1-Generate-001"


class TestVideoDurations:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [("relay-video-x1", (4, 8)), ("relay-kling-v2-turbo", (5, 10)), (VEO, (4, 6, 8))],
        ids=["no-family", "name-preset", "entry"],
    )
    def test_without_override_takes_entry_or_name_preset(self, model_id, expected):
        assert video_model_facts(model_id, None).durations == expected

    def test_override_replaces_entry(self):
        assert video_model_facts(VEO, {"durations": [10, 5, 5]}).durations == (5, 10)

    def test_legacy_column_sits_between_override_and_entry(self):
        assert video_model_facts(VEO, None, legacy_durations=[6]).durations == (6,)
        assert video_model_facts(VEO, {"durations": [8]}, legacy_durations=[6]).durations == (8,)

    def test_invalid_override_falls_back(self):
        assert video_model_facts("relay-video-x1", {"durations": []}).durations == (4, 8)


class TestVideoOtherFacts:
    def test_unmatched_model_puts_no_constraint_on_resolutions_and_constraints(self):
        facts = video_model_facts("relay-video-x1", None)
        assert facts.resolutions is None
        assert facts.duration_constraints == DurationConstraints()

    def test_entry_facts_used_when_not_overridden(self):
        facts = video_model_facts(VEO, None)
        assert facts.resolutions == ("720p", "1080p", "4k")
        assert facts.duration_constraints.with_reference_images == (8,)

    def test_overrides_replace_each_dimension_whole(self):
        facts = video_model_facts(
            VEO,
            {"resolutions": ["720p"], "duration_constraints": {"by_resolution": {"720P": [4]}}},
        )
        assert facts.resolutions == ("720p",)
        # 整维替换：没写的参考图约束随之消失，不与条目合并
        assert facts.duration_constraints == DurationConstraints(by_resolution={"720p": (4,)})


class TestTextAndImageFacts:
    def test_text_unmatched_without_override_is_unconstrained(self):
        assert text_model_facts("relay-llm-x", None) == TextModelFacts()

    def test_text_entry_and_override(self):
        facts = text_model_facts("gemini-3-flash-preview", {"image_input": False})
        assert facts == TextModelFacts(image_input=False, structured_output=True, max_output_tokens=65536)

    def test_text_max_output_tokens_precedence(self):
        model = "gemini-3-flash-preview"
        assert text_model_facts(model, None, legacy_max_output_tokens=8192).max_output_tokens == 8192
        assert (
            text_model_facts(model, {"max_output_tokens": 4096}, legacy_max_output_tokens=8192).max_output_tokens
            == 4096
        )

    def test_image_override_on_unmatched_model(self):
        assert image_model_facts("relay-image-x", {"text_to_image": False}) == ImageModelFacts(text_to_image=False)


class TestOverrideValues:
    @pytest.mark.parametrize(
        ("key", "value", "ok"),
        [
            ("image_input", True, True),
            ("image_input", 1, False),
            ("max_output_tokens", 1024, True),
            ("max_output_tokens", 0, False),
            ("max_output_tokens", True, False),
            ("durations", [4, 8], True),
            ("durations", [], False),
            ("durations", [4, True], False),
            ("resolutions", ["720p"], True),
            ("resolutions", [""], False),
            ("duration_constraints", {"by_resolution": {"1080p": [8]}, "with_reference_images": [8]}, True),
            ("duration_constraints", {"by_resolution": {"1080p": []}}, False),
            ("duration_constraints", {"other": [8]}, False),
            ("no_such_key", True, False),
        ],
    )
    def test_value_shapes(self, key, value, ok):
        assert model_fact_value_matches(key, value) is ok

    def test_only_dimensions_of_the_media_type_are_taken(self):
        overrides = {"durations": [5], "text_to_image": True, "last_frame": True, "max_output_tokens": -1}
        assert model_fact_overrides(overrides, "video") == {"durations": [5]}
        assert model_fact_overrides(overrides, "image") == {"text_to_image": True}
        assert model_fact_overrides(overrides, "text") == {}
