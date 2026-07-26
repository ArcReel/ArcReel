"""帧槽位规划纯函数的直接单测：能力 gating × 槽位组装各组合分支。"""

from pathlib import Path

import pytest

from lib.reference_compression import RefRole
from lib.video_backends.base import VideoCapabilities, VideoCapabilityError
from lib.video_frame_slots import plan_frame_slots, resolve_video_capabilities

pytestmark = pytest.mark.unit

CAPS_WITH_LAST_FRAME = VideoCapabilities(
    first_frame=True, last_frame=True, reference_images=True, max_reference_images=4
)
CAPS_NO_LAST_FRAME = VideoCapabilities(
    first_frame=True, last_frame=False, reference_images=True, max_reference_images=4
)


def _plan(caps: VideoCapabilities | None, **kwargs):
    return plan_frame_slots(caps=caps, provider="acme", model="acme-v1", **kwargs)


class TestLastFrameGating:
    def test_unsupported_last_frame_with_end_image_raises(self):
        """不支持尾帧 × 携带尾帧：硬失败，不静默降级。"""
        with pytest.raises(VideoCapabilityError) as exc:
            _plan(CAPS_NO_LAST_FRAME, start_image=Path("start.png"), end_image=Path("end.png"))

        assert exc.value.code == "video_last_frame_unsupported"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_unsupported_last_frame_without_end_image_passes(self):
        """不支持尾帧 × 不携带尾帧：正常放行，无尾帧槽位。"""
        plan = _plan(CAPS_NO_LAST_FRAME, start_image=Path("start.png"))

        assert plan.end_index is None
        assert [s.source for s in plan.specs] == [Path("start.png")]

    def test_supported_last_frame_with_end_image_passes(self):
        """支持尾帧 × 携带尾帧：尾帧进入槽位（first_last 模式）。"""
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"), end_image=Path("end.png"))

        assert plan.start_index == 0
        assert plan.end_index == 1
        assert [s.role for s in plan.specs] == [RefRole.FRAME, RefRole.FRAME]

    def test_supported_last_frame_without_end_image_passes(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"))

        assert plan.end_index is None
        assert plan.start_index == 0

    def test_end_image_without_start_image_still_gated(self):
        """尾帧单独出现同样受 gating——不因缺首帧而绕过。"""
        with pytest.raises(VideoCapabilityError):
            _plan(CAPS_NO_LAST_FRAME, end_image=Path("end.png"))

    def test_end_image_only_takes_first_slot_when_supported(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, end_image=Path("end.png"))

        assert plan.start_index is None
        assert plan.end_index == 0

    def test_uncharted_caps_without_end_image_passes(self):
        """caps=None（调用方未查询能力）× 不携带尾帧：能力不影响任何槽位，正常放行。"""
        plan = _plan(None, start_image=Path("start.png"), reference_images=[Path("r1.png")])

        assert (plan.start_index, plan.end_index, plan.reference_start_index) == (0, None, 1)

    def test_uncharted_caps_with_end_image_raises(self):
        """caps=None × 携带尾帧：未经能力核实的尾帧一律拒绝，不按"支持"放行。"""
        with pytest.raises(VideoCapabilityError) as exc:
            _plan(None, start_image=Path("start.png"), end_image=Path("end.png"))

        assert exc.value.code == "video_last_frame_unsupported"


class TestSlotAssembly:
    def test_no_inputs_yields_empty_plan(self):
        plan = _plan(CAPS_WITH_LAST_FRAME)

        assert plan.specs == []
        assert (plan.start_index, plan.end_index, plan.reference_start_index) == (None, None, None)

    def test_reference_images_follow_frames(self):
        """数组参考图恒排在首/尾帧之后，调用方按起始索引切片还原。"""
        plan = _plan(
            CAPS_WITH_LAST_FRAME,
            start_image=Path("start.png"),
            end_image=Path("end.png"),
            reference_images=[Path("r1.png"), Path("r2.png")],
        )

        assert (plan.start_index, plan.end_index, plan.reference_start_index) == (0, 1, 2)
        assert [s.role for s in plan.specs] == [RefRole.FRAME, RefRole.FRAME, RefRole.ARRAY, RefRole.ARRAY]
        assert [s.source for s in plan.specs[plan.reference_start_index :]] == [Path("r1.png"), Path("r2.png")]

    def test_empty_reference_list_yields_no_reference_index(self):
        """空列表与 None 同义：不设起始索引，调用方回落原字段保留 [] / None 语义。"""
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"), reference_images=[])

        assert plan.reference_start_index is None
        assert len(plan.specs) == 1

    def test_str_start_image_normalized_to_path(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image="start.png")

        assert plan.specs[0].source == Path("start.png")

    def test_pil_start_image_skips_compression(self):
        """PIL.Image 首帧不入压缩器，维持 request.start_image=None 的原行为。"""
        from PIL import Image

        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Image.new("RGB", (2, 2)), end_image=Path("end.png"))

        assert plan.start_index is None
        assert plan.end_index == 0


class TestResolveVideoCapabilities:
    def test_prefers_tier_aware_query(self):
        """后端实现 video_capabilities_for_tier 时按实际档位收窄，而非读保守属性。"""
        seen: dict[str, object] = {}

        class TierAwareBackend:
            video_capabilities = CAPS_NO_LAST_FRAME

            def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None):
                seen["service_tier"] = service_tier
                seen["resolution"] = resolution
                return CAPS_WITH_LAST_FRAME

        caps = resolve_video_capabilities(TierAwareBackend(), service_tier="pro", resolution="1080p")

        assert caps.last_frame is True
        assert seen == {"service_tier": "pro", "resolution": "1080p"}

    def test_falls_back_to_static_property(self):
        class PlainBackend:
            video_capabilities = CAPS_NO_LAST_FRAME

        assert resolve_video_capabilities(PlainBackend()).last_frame is False
