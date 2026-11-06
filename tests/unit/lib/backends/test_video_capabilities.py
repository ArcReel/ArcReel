"""视频的生效输入能力：调用通道与模型条目两侧求值，候选与执行时的校验读同一个结果（docs/adr/0104）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcreel_catalog_core import VideoFacts
from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities, VideoCapabilityError
from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.generation_type_buckets import builtin_model_buckets, custom_model_buckets
from lib.backends.input_limits import InputLimit
from lib.backends.video_capabilities import (
    UNRECORDED_VIDEO_FACTS,
    effective_video_capabilities,
    reference_audio_count_limit,
    reference_image_limit,
)
from lib.backends.video_frame_slots import gate_video_request
from lib.catalog import query as catalog
from lib.custom_provider.backends import CustomVideoBackend
from lib.custom_provider.capabilities import (
    synthesize_video_capabilities,
    system_video_capabilities,
    two_sided_video_capabilities,
)
from lib.custom_provider.endpoints import comfyui_endpoint_spec
from tests.factories import comfyui_endpoint_definition

#: 实现了全部输入路径、参考图与参考音频段数都不设上限的调用通道。
_OPEN_CHANNEL = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=True,
    max_reference_images=None,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=None,
)


def _images(tmp_path: Path, count: int, prefix: str = "r") -> list[Path]:
    paths = []
    for i in range(count):
        path = tmp_path / f"{prefix}{i}.png"
        path.write_bytes(b"\x89PNG")
        paths.append(path)
    return paths


def _with_reference_limit(caps: VideoCapabilities, max_reference_images: int | None) -> VideoCapabilities:
    return replace(caps, max_reference_images=max_reference_images)


def _gate(caps: VideoCapabilities, tmp_path: Path, **inputs) -> None:
    has_image = any(inputs.get(key) for key in ("has_first_frame", "end_image", "reference_images"))
    gate_video_request(caps=caps, provider="p", model="m", prompt="p", has_image=has_image, **inputs)


class TestTwoSidedEvaluation:
    def test_entry_rejecting_last_frame_closes_it_although_the_channel_supports_it(self):
        caps = effective_video_capabilities(_OPEN_CHANNEL, VideoFacts(first_frame=True, last_frame=False))
        assert caps.first_frame is True
        assert caps.last_frame is False

    def test_channel_without_a_path_closes_it_although_the_entry_accepts_it(self):
        channel = VideoCapabilities(last_frame=False, max_reference_images=0)
        caps = effective_video_capabilities(channel, VideoFacts(last_frame=True, max_reference_images=9))
        assert caps.last_frame is False
        assert reference_image_limit(caps) == InputLimit.at_most(0)

    def test_unrecorded_entry_keeps_the_conservative_fallback(self):
        """条目未收录：文生与首帧跟随通道，尾帧与参考音频不开，数值只受通道约束。"""
        caps = effective_video_capabilities(_OPEN_CHANNEL, UNRECORDED_VIDEO_FACTS)
        assert (caps.text_to_video, caps.first_frame, caps.last_frame) == (True, True, False)
        assert reference_image_limit(caps) == InputLimit.unrecorded()
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert reference_audio_count_limit(caps) == InputLimit.unsupported()

        bounded = effective_video_capabilities(_with_reference_limit(_OPEN_CHANNEL, 4), UNRECORDED_VIDEO_FACTS)
        assert reference_image_limit(bounded) == InputLimit.at_most(4)

    def test_explicit_zero_is_not_accepted(self):
        caps = effective_video_capabilities(_OPEN_CHANNEL, VideoFacts(max_reference_images=0))
        assert caps.max_reference_images == 0
        assert reference_image_limit(caps).accepts_input is False

    @pytest.mark.parametrize(("entry", "channel", "expected"), [(7, 4, 4), (3, 9, 3), (9, None, 9), (None, 4, 4)])
    def test_reference_image_limit_takes_the_tighter_side(self, entry, channel, expected):
        caps = effective_video_capabilities(
            _with_reference_limit(_OPEN_CHANNEL, channel), VideoFacts(max_reference_images=entry)
        )
        assert caps.max_reference_images == expected

    def test_reference_audio_needs_both_sides_and_takes_the_tighter_limits(self):
        channel = VideoCapabilities(
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=5,
            max_reference_audio_total_seconds=20.0,
        )
        facts = VideoFacts(reference_audio=True, max_reference_audio_count=3, max_reference_audio_total_seconds=30.0)
        caps = effective_video_capabilities(channel, facts)
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3
        assert caps.max_reference_audio_total_seconds == 20.0

        closed = effective_video_capabilities(channel, VideoFacts(reference_audio=False, max_reference_audio_count=3))
        assert closed.reference_audio_mode is ReferenceAudioMode.NONE
        assert closed.max_reference_audio_count == 0
        assert closed.max_reference_audio_total_seconds is None

    def test_text_to_video_rejected_by_the_entry(self):
        caps = effective_video_capabilities(_OPEN_CHANNEL, VideoFacts(text_to_video=False))
        assert caps.text_to_video is False


class TestCandidatesAndExecutionReadTheSameResult:
    """候选（任务类型桶、能力字典）与执行时的校验对同一个模型给出一致的结论。"""

    def test_entry_rejects_last_frame_while_the_builtin_channel_supports_it(self, tmp_path: Path):
        """百炼通道实现了尾帧；HappyHorse 1.0 i2v 的条目记录不接受。"""
        caps = builtin_video_capabilities_for_model("dashscope", "happyhorse-1.0-i2v")
        assert caps.last_frame is False
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, has_first_frame=True, end_image=_images(tmp_path, 1, "e")[0])
        assert exc.value.code == "video_last_frame_unsupported"

    def test_entry_rejects_last_frame_on_a_custom_endpoint(self, tmp_path: Path):
        caps = two_sided_video_capabilities(endpoint="dashscope-always-on-video", model_id="happyhorse-1.0-i2v")
        assert system_video_capabilities(endpoint="dashscope-always-on-video", model_id="happyhorse-1.0-i2v").last_frame
        assert caps.last_frame is False
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, has_first_frame=True, end_image=_images(tmp_path, 1, "e")[0])
        assert exc.value.code == "video_last_frame_unsupported"

    def test_unrecorded_model_follows_the_channel(self, tmp_path: Path):
        """对应不上条目：参考生视频是候选，上限未收录，执行时不裁剪；尾帧与参考音频不开。"""
        caps = two_sided_video_capabilities(endpoint="ark-seedance-2", model_id="relay-video-x1")
        assert custom_model_buckets(endpoint="ark-seedance-2", model_id="relay-video-x1") == frozenset({"i2v", "r2v"})
        _gate(caps, tmp_path, reference_images=_images(tmp_path, 12))
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, has_first_frame=True, end_image=_images(tmp_path, 1, "e")[0])
        assert exc.value.code == "video_last_frame_unsupported"
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, reference_audio_files=[tmp_path / "a.mp3"])
        assert exc.value.code == "video_reference_audio_unsupported"

    def test_explicit_zero_drops_the_candidate_and_rejects_at_execution(self, tmp_path: Path):
        """Veo 3.1 Lite 的条目记录参考图上限 0：通道实现了参考图，Lite 仍不进参考生视频候选，带参考图被拒。"""
        lite = catalog.get_model("gemini-aistudio", "veo-3.1-lite-generate-preview")
        regular = catalog.get_model("gemini-aistudio", "veo-3.1-generate-preview")
        assert lite is not None
        assert regular is not None
        assert builtin_model_buckets("gemini-aistudio", "veo-3.1-lite-generate-preview", lite) == frozenset({"i2v"})
        assert "r2v" in builtin_model_buckets("gemini-aistudio", "veo-3.1-generate-preview", regular)

        caps = builtin_video_capabilities_for_model("gemini-aistudio", "veo-3.1-lite-generate-preview")
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, reference_images=_images(tmp_path, 1))
        assert exc.value.code == "video_reference_images_unsupported"

    def test_custom_override_replaces_the_whole_dimension(self, tmp_path: Path):
        """Vidu Q3 Pro 的条目不接受参考图；用户覆盖上限 3 后候选列出参考生视频，执行时按 3 校验。"""
        assert "r2v" not in custom_model_buckets(endpoint="vidu-video-q3-turbo", model_id="viduq3-pro")
        overrides = {"max_reference_images": 3}
        assert "r2v" in custom_model_buckets(
            endpoint="vidu-video-q3-turbo", model_id="viduq3-pro", capability_overrides=overrides
        )

        caps = synthesize_video_capabilities(endpoint="vidu-video-q3-turbo", model_id="viduq3-pro", overrides=overrides)
        _gate(caps, tmp_path, reference_images=_images(tmp_path, 3))
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(caps, tmp_path, reference_images=_images(tmp_path, 4))
        assert exc.value.code == "video_reference_images_exceeded"

    def test_custom_override_can_open_a_dimension_the_entry_rejects(self, tmp_path: Path):
        """Vidu Q3 的条目不接受首尾帧（只做参考生视频）；覆盖整维替换后执行时放行。"""
        assert two_sided_video_capabilities(endpoint="vidu-video-q3-turbo", model_id="viduq3").last_frame is False
        caps = synthesize_video_capabilities(
            endpoint="vidu-video-q3-turbo", model_id="viduq3", overrides={"first_frame": True, "last_frame": True}
        )
        assert caps.last_frame is True
        _gate(caps, tmp_path, has_first_frame=True, end_image=_images(tmp_path, 1, "e")[0])

    def test_builtin_user_added_override_applies_after_the_two_sided_evaluation(self):
        def install(overrides: dict[str, object]) -> VideoCapabilities:
            catalog.install_user_added_models(
                [
                    catalog.UserAddedModel(
                        "dashscope",
                        "relay-happyhorse-i2v",
                        "video",
                        entry_binding="alibaba/happyhorse-1.0-i2v",
                        capability_overrides=overrides,
                    )
                ]
            )
            return builtin_video_capabilities_for_model("dashscope", "relay-happyhorse-i2v")

        try:
            assert install({}).last_frame is False
            assert install({"last_frame": True}).last_frame is True
        finally:
            catalog.install_user_added_models(())


class TestComfyuiDoesNotReadEntries:
    def test_comfyui_capabilities_come_only_from_node_bindings(self):
        """模型行的名字恰好对应一个条目（Hailuo 2.3 Fast 不接受文生），ComfyUI 端点的能力也不受它约束。"""
        spec = comfyui_endpoint_spec("ce-7", comfyui_endpoint_definition())
        model_id = "MiniMax-Hailuo-2.3-Fast"
        assert catalog.find_entry(model_id, media_type="video") is not None
        channel = system_video_capabilities(endpoint="ce-7", model_id=model_id, endpoint_spec=spec)
        assert channel.text_to_video is True
        assert two_sided_video_capabilities(endpoint="ce-7", model_id=model_id, endpoint_spec=spec) == channel


class TestCustomBackendTierPath:
    """自定义供应商的执行层能力：按档位解析的通道声明同样与条目两侧求值，再叠加覆盖。"""

    @staticmethod
    def _backend(*, overrides=None) -> CustomVideoBackend:
        delegate = AsyncMock()
        delegate.video_capabilities_for_tier = MagicMock(
            return_value=VideoCapabilities(first_frame=True, last_frame=True, max_reference_images=4)
        )
        return CustomVideoBackend(provider_id="p", delegate=delegate, model="m").with_video_capabilities(
            VideoCapabilities(),
            overrides=overrides,
            entry_facts=VideoFacts(last_frame=False, max_reference_images=2),
        )

    def test_tier_aware_base_is_intersected_with_the_entry(self):
        caps = self._backend().video_capabilities_for_tier("pro")
        assert caps.last_frame is False
        assert caps.max_reference_images == 2

    def test_override_still_wins_on_the_tier_path(self):
        caps = self._backend(overrides={"last_frame": True}).video_capabilities_for_tier("pro")
        assert caps.last_frame is True
        assert caps.max_reference_images == 2
