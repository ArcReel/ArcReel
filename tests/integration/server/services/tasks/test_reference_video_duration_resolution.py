"""Default new reference-video unit duration comes from its bucket's video-request facts."""

from __future__ import annotations

import pytest

from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import DEFAULT_PLANNED_DURATION_SECONDS, VideoRequestFactsFailure
from lib.script.reference_video.request_projection import configured_reference_request_facts
from server.services.tasks.reference_video_tasks import default_unit_duration
from tests.factories import make_video_request_facts

VEO = "gemini-vertex/veo-3.1-generate-001"


@pytest.mark.parametrize(("bucket", "expected"), [("i2v", 4), ("r2v", 8)])
async def test_new_unit_default_follows_the_bucket_it_lands_in(db_factory, bucket: str, expected: int):
    """Veo 3.1 with unset resolution: imageless i2v uses first [4,6,8] tier; image-backed units narrow to eight seconds only."""
    project = {"generation_mode": "reference_video", "video_provider_r2v": VEO, "video_provider_i2v": VEO}

    facts = await configured_reference_request_facts(project, ConfigResolver(db_factory))(bucket)

    assert default_unit_duration(facts, project) == expected


def test_default_unit_duration_takes_the_project_preference_only_when_it_is_a_narrowed_tier():
    facts = make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,))

    assert default_unit_duration(facts, {"default_duration": 8}) == 8
    # Ignore preferences removed by narrowing even if in the full set; use the narrowed minimum instead.
    assert default_unit_duration(facts, {"default_duration": 4}) == 8


def test_default_unit_duration_takes_min_of_unordered_custom_tiers():
    """Custom-provider tier lists may be unordered: select minimum, not first item."""
    facts = make_video_request_facts(supported_durations=(8, 4), allowed_durations=(8, 4))

    assert default_unit_duration(facts, {}) == 4


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param(VideoRequestFactsFailure("reference_capability_unavailable"), id="unresolvable"),
        pytest.param(
            make_video_request_facts(supported_durations=(), allowed_durations=(), duration_endpoint_fixed=True),
            id="endpoint-fixed",
        ),
    ],
)
def test_default_unit_duration_falls_back_when_no_tier_can_be_taken(facts):
    """If facts cannot resolve or endpoint fixes duration, preference validity is unknowable; use the fallback directly."""
    assert default_unit_duration(facts, {"default_duration": 12}) == DEFAULT_PLANNED_DURATION_SECONDS
