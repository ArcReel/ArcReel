"""请求期能力校验（gate_video_request）与帧槽位组装（plan_frame_slots）的直接单测。"""

from pathlib import Path

import pytest

from lib.reference_compression import RefRole
from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities, VideoCapabilityError
from lib.video_frame_slots import gate_video_request, plan_frame_slots, resolve_video_capabilities

pytestmark = pytest.mark.unit

CAPS_WITH_LAST_FRAME = VideoCapabilities(first_frame=True, last_frame=True, max_reference_images=4)
CAPS_NO_LAST_FRAME = VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=4)
CAPS_WITH_AUDIO = VideoCapabilities(
    first_frame=True,
    max_reference_images=4,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=3,
)
CAPS_WITH_AUDIO_DURATION_LIMIT = VideoCapabilities(
    first_frame=True,
    max_reference_images=4,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=3,
    max_reference_audio_total_seconds=15.0,
)


def _gate(caps: VideoCapabilities | None, **kwargs):
    gate_video_request(caps=caps, provider="acme", model="acme-v1", **kwargs)


def _plan(caps: VideoCapabilities | None, **kwargs):
    """沿用「先校验再组装」的生产调用序：组装是纯函数，不再自行判定能力。"""
    gate_kwargs = {k: v for k, v in kwargs.items() if k != "start_image"}
    _gate(caps, **gate_kwargs)
    return plan_frame_slots(**kwargs)


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
        """caps=None（调用方未查询能力）× 三条路径都不走：无需能力声明即可放行。"""
        _gate(None)

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


class TestReferenceImageGating:
    def test_reference_images_beyond_limit_raise(self):
        """超出上限硬失败：静默截断会让用户以为所有参考图都生效了。"""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_LAST_FRAME, reference_images=[Path(f"r{i}.png") for i in range(5)])

        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 4, "count": 5}

    def test_reference_images_at_limit_pass(self):
        _gate(CAPS_WITH_LAST_FRAME, reference_images=[Path(f"r{i}.png") for i in range(4)])

    def test_reference_images_on_zero_capacity_model_raise(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(VideoCapabilities(), reference_images=[Path("r.png")])

        assert exc.value.code == "video_reference_images_unsupported"

    def test_uncharted_caps_with_reference_images_raise(self):
        """caps=None × 携带参考图：与尾帧同理，未经能力核实不放行。"""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(None, reference_images=[Path("r.png")])

        assert exc.value.code == "video_reference_images_unsupported"


class TestReferenceAudioGating:
    def test_audio_on_model_without_capability_raises(self):
        """无音色输入能力的模型收到音频：硬失败，不静默丢弃后照常扣费生成随机音色。"""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_LAST_FRAME, reference_audio_files=[Path("a.mp3")])

        assert exc.value.code == "video_reference_audio_unsupported"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_audio_within_limit_passes(self):
        _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path("a.mp3"), Path("b.wav")])

    def test_audio_at_limit_passes(self):
        _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path(f"a{i}.mp3") for i in range(3)])

    def test_audio_beyond_limit_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path(f"a{i}.mp3") for i in range(4)])

        assert exc.value.code == "video_reference_audio_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 3, "count": 4}

    def test_uncharted_caps_with_audio_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(None, reference_audio_files=[Path("a.mp3")])

        assert exc.value.code == "video_reference_audio_unsupported"

    def test_empty_audio_list_passes_on_incapable_model(self):
        """空列表与 None 同义：没有音频诉求就不该被音频能力挡住。"""
        _gate(CAPS_WITH_LAST_FRAME, reference_audio_files=[])


class TestReferenceAudioDurationGating:
    def test_total_within_limit_passes(self):
        _gate(
            CAPS_WITH_AUDIO_DURATION_LIMIT,
            reference_audio_files=[Path("a.mp3"), Path("b.wav")],
            reference_audio_total_seconds=14.9,
        )

    def test_total_at_limit_passes(self):
        _gate(
            CAPS_WITH_AUDIO_DURATION_LIMIT,
            reference_audio_files=[Path("a.mp3"), Path("b.wav")],
            reference_audio_total_seconds=15.0,
        )

    def test_total_beyond_limit_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(
                CAPS_WITH_AUDIO_DURATION_LIMIT,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=15.1,
            )

        assert exc.value.code == "video_reference_audio_duration_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 15.0, "total": 15.1}

    def test_unknown_total_skips_check_even_when_limit_declared(self):
        """探测失败（total=None）按仓库既有降级口径跳过校验，不当作超限拒绝。"""
        _gate(
            CAPS_WITH_AUDIO_DURATION_LIMIT,
            reference_audio_files=[Path("a.mp3"), Path("b.wav")],
            reference_audio_total_seconds=None,
        )

    def test_no_declared_limit_skips_check_regardless_of_total(self):
        """caps 未声明总时长约束（None）：即便传了很大的 total 也不拦——该维度对这个后端不适用。"""
        _gate(
            CAPS_WITH_AUDIO,
            reference_audio_files=[Path("a.mp3"), Path("b.wav")],
            reference_audio_total_seconds=1000.0,
        )


class TestGateAndAssemblySeparation:
    def test_plan_frame_slots_does_not_gate(self):
        """组装是纯函数：即便尾帧不被支持也照常铺槽位，拒绝的责任全在 gate。"""
        plan = plan_frame_slots(start_image=Path("start.png"), end_image=Path("end.png"))

        assert (plan.start_index, plan.end_index) == (0, 1)
