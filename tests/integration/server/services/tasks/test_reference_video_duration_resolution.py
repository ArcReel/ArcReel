"""新建参考生视频 unit 的默认时长：按所落桶的视频请求事实取档。"""

from __future__ import annotations

import pytest

from lib.config.resolver import ConfigResolver
from lib.generation.video_request_facts import DEFAULT_PLANNED_DURATION_SECONDS, VideoRequestFactsFailure
from lib.script.reference_video.request_projection import configured_reference_request_facts
from server.services.tasks.reference_video_tasks import default_unit_duration
from tests.factories import make_video_request_facts

VEO = "gemini-aistudio/veo-3.1-generate-preview"


@pytest.mark.parametrize(("bucket", "expected"), [("i2v", 4), ("r2v", 8)])
async def test_new_unit_default_follows_the_bucket_it_lands_in(db_factory, bucket: str, expected: int):
    """Veo 3.1 未设分辨率：无参考图单元落 i2v 取 [4,6,8] 的首档，带参考图单元按参考图约束只剩 8 秒。"""
    project = {"generation_mode": "reference_video", "video_provider_r2v": VEO, "video_provider_i2v": VEO}

    facts = await configured_reference_request_facts(project, ConfigResolver(db_factory))(bucket)

    assert default_unit_duration(facts, project) == expected


def test_default_unit_duration_takes_the_project_preference_only_when_it_is_a_narrowed_tier():
    facts = make_video_request_facts(supported_durations=(4, 6, 8), allowed_durations=(8,))

    assert default_unit_duration(facts, {"default_duration": 8}) == 8
    # 偏好只在全集里、被收窄掉时不采信：退到收窄后的最短档。
    assert default_unit_duration(facts, {"default_duration": 4}) == 8


def test_default_unit_duration_takes_min_of_unordered_custom_tiers():
    """自定义供应商声明的档位可能不按升序排列：取最小值而非第一项。"""
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
    """事实解析不出或时长由端点固定时无从校验偏好是否可申请，直接退到兜底值。"""
    assert default_unit_duration(facts, {"default_duration": 12}) == DEFAULT_PLANNED_DURATION_SECONDS
