"""从节点绑定推导能力位与参数约束：图绑定四位、音轨三形态、原生时长与固定维度。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.custom_provider.comfyui.capabilities import (
    default_supported_durations,
    derive_video_capabilities,
    duration_is_fixed,
    fps_literals,
    native_duration,
    native_short_edge,
    size_is_fixed,
    takes_reference_images,
)
from tests.factories import comfyui_endpoint_definition


def _image_target(node: str, class_type: str = "LoadImage") -> dict[str, Any]:
    return {"node": node, "input": "image", "class_type": class_type}


def _frames_target(node: str = "5", **extra: Any) -> dict[str, Any]:
    return {"node": node, "input": "length", "class_type": "EmptyLatentImage", **extra}


class TestImageBindingsDecideTheFourVideoBits:
    def test_no_image_binding_at_all_is_the_only_shape_that_is_text_to_video(self):
        caps = derive_video_capabilities(comfyui_endpoint_definition())

        assert caps.text_to_video is True
        assert caps.first_frame is False
        assert caps.last_frame is False
        assert caps.max_reference_images == 0

    @pytest.mark.parametrize("key", ["start_image", "end_image", "reference_images"])
    def test_any_one_image_binding_takes_text_to_video_away(self, key: str):
        definition = comfyui_endpoint_definition()
        definition["bindings"][key] = [_image_target("11")]

        assert derive_video_capabilities(definition).text_to_video is False

    def test_first_and_last_frame_follow_their_own_key(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["start_image"] = [_image_target("11")]
        definition["bindings"]["end_image"] = [_image_target("12")]

        caps = derive_video_capabilities(definition)

        assert (caps.first_frame, caps.last_frame) == (True, True)

    def test_the_reference_image_ceiling_is_the_number_of_slots(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["reference_images"] = [_image_target("11"), _image_target("12"), _image_target("13")]

        assert derive_video_capabilities(definition).max_reference_images == 3

    @pytest.mark.parametrize("value", [[], None], ids=["empty_list", "key_absent"])
    def test_an_empty_list_and_a_missing_key_both_read_as_no_binding(self, value: list[Any] | None):
        definition = comfyui_endpoint_definition()
        if value is None:
            definition["bindings"].pop("start_image", None)
        else:
            definition["bindings"]["start_image"] = value

        assert derive_video_capabilities(definition).first_frame is False


class TestAudioTrackIsReadOffTheOutputChain:
    def test_a_video_combine_node_with_a_fed_audio_input_is_always_on(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {
            "class_type": "VHS_VideoCombine",
            "inputs": {"images": ["8", 0], "audio": ["21", 0]},
        }
        definition["bindings"]["output"] = [{"node": "20", "class_type": "VHS_VideoCombine"}]

        assert derive_video_capabilities(definition).audio_track == "always_on"

    def test_the_same_node_without_that_link_is_always_off(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {"class_type": "VHS_VideoCombine", "inputs": {"images": ["8", 0]}}
        definition["bindings"]["output"] = [{"node": "20", "class_type": "VHS_VideoCombine"}]

        assert derive_video_capabilities(definition).audio_track == "always_off"

    def test_the_audio_input_one_hop_upstream_of_save_video_counts(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["21"] = {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "audio": ["22", 0]}}
        definition["workflow"]["9"] = {"class_type": "SaveVideo", "inputs": {"video": ["21", 0]}}

        assert derive_video_capabilities(definition).audio_track == "always_on"

    def test_an_unrecognised_node_on_that_hop_falls_back_to_always_off(self):
        """``SaveVideo.video`` 接的不是名录里的成片节点时，这份图的音轨判不出来。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["21"] = {"class_type": "MyCustomMuxer", "inputs": {"audio": ["22", 0]}}
        definition["workflow"]["9"] = {"class_type": "SaveVideo", "inputs": {"video": ["21", 0]}}

        assert derive_video_capabilities(definition).audio_track == "always_off"

    def test_an_output_node_outside_the_table_falls_back_to_always_off(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {"class_type": "MySaveNode", "inputs": {"audio": ["21", 0]}}
        definition["bindings"]["output"] = [{"node": "20", "class_type": "MySaveNode"}]

        assert derive_video_capabilities(definition).audio_track == "always_off"

    def test_it_is_never_controllable(self):
        """ComfyUI 侧没有可下发的音轨开关，两种形态都不是 ``controllable``。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {"class_type": "VHS_VideoCombine", "inputs": {"audio": ["21", 0]}}
        definition["bindings"]["output"] = [{"node": "20", "class_type": "VHS_VideoCombine"}]
        fed = derive_video_capabilities(definition).audio_track
        definition["workflow"]["20"]["inputs"] = {}
        unfed = derive_video_capabilities(definition).audio_track

        assert {fed, unfed} == {"always_on", "always_off"}


class TestReferenceImageSlotsDecideTheImageBucket:
    def test_no_reference_image_binding_at_all(self):
        definition = comfyui_endpoint_definition(media_type="image")
        definition["bindings"].pop("fps", None)

        assert takes_reference_images(definition) is False

    def test_a_reference_image_binding_is_reported(self):
        definition = comfyui_endpoint_definition(media_type="image")
        definition["bindings"]["reference_images"] = [_image_target("11")]

        assert takes_reference_images(definition) is True


class TestSizeIsFixedUnlessBothSidesAreBound:
    def test_both_sides_bound_leaves_the_dimension_drivable(self):
        assert size_is_fixed(comfyui_endpoint_definition()["bindings"]) is False

    @pytest.mark.parametrize("dropped", ["width", "height"])
    def test_binding_only_one_side_counts_as_fixed(self, dropped: str):
        """只写得动一侧时派生出的比例既不是原生的也不是用户选的，故整维判为固定。"""
        bindings = comfyui_endpoint_definition()["bindings"]
        bindings.pop(dropped)

        assert size_is_fixed(bindings) is True

    def test_neither_side_bound_is_fixed(self):
        bindings = comfyui_endpoint_definition()["bindings"]
        bindings.pop("width")
        bindings.pop("height")

        assert size_is_fixed(bindings) is True

    def test_two_width_inputs_that_disagree_leave_the_native_short_edge_unknown(self):
        """两路消费者各写各的尺寸：这份图没有「原生」那一档，报较小的那个等于替用户选了一路。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"].update({"width": 1024, "height": 576})
        definition["workflow"]["12"] = {"class_type": "WanVideoSize", "inputs": {"width": 512, "height": 512}}
        definition["bindings"]["width"].append({"node": "12", "input": "width", "class_type": "WanVideoSize"})
        definition["bindings"]["height"].append({"node": "12", "input": "height", "class_type": "WanVideoSize"})

        assert native_short_edge(definition) is None

    def test_a_dimension_input_that_reads_no_literal_leaves_the_short_edge_unknown(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"].update({"width": 832, "height": 480})
        definition["workflow"]["12"] = {"class_type": "WanVideoSize", "inputs": {"width": ["5", 0]}}
        definition["bindings"]["width"].append({"node": "12", "input": "width", "class_type": "WanVideoSize"})

        assert native_short_edge(definition) is None

    def test_agreeing_inputs_still_give_the_shorter_of_the_two_sides(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"].update({"width": 832, "height": 480})
        definition["workflow"]["12"] = {"class_type": "WanVideoSize", "inputs": {"width": 832, "height": 480}}
        definition["bindings"]["width"].append({"node": "12", "input": "width", "class_type": "WanVideoSize"})
        definition["bindings"]["height"].append({"node": "12", "input": "height", "class_type": "WanVideoSize"})

        assert native_short_edge(definition) == 480


class TestNativeDurationAndTheDefaultTier:
    def test_frames_and_a_read_only_fps_give_the_workflow_its_own_tier(self):
        """``SaveVideo.fps`` 字面 16、``length`` 字面 81 → ``round((81 − 1) / 16)`` = 5 秒。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["bindings"]["frames"] = [_frames_target(step=4)]

        assert native_duration(definition) == 5
        assert default_supported_durations(definition) == [5]

    def test_a_manually_typed_fps_on_the_frames_entry_stands_in_for_a_missing_binding(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 49
        definition["bindings"].pop("fps")
        definition["bindings"]["frames"] = [_frames_target(fps=24)]

        assert native_duration(definition) == 2

    def test_frames_unbound_means_the_duration_is_fixed_and_the_tier_is_empty(self):
        definition = comfyui_endpoint_definition()

        assert duration_is_fixed(definition["bindings"]) is True
        assert default_supported_durations(definition) == []

    def test_frames_bound_without_any_frame_rate_source_also_yields_an_empty_tier(self):
        """时长换算不出来时不猜一个帧率：档位空集，控件禁用。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["bindings"].pop("fps")
        definition["bindings"]["frames"] = [_frames_target()]

        assert duration_is_fixed(definition["bindings"]) is False
        assert native_duration(definition) is None
        assert default_supported_durations(definition) == []

    def test_a_frames_target_whose_literal_is_missing_yields_no_native_duration(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["frames"] = [_frames_target()]

        assert native_duration(definition) is None

    def test_one_unreadable_target_among_several_voids_the_tier_too(self):
        """接了链接的那一格照样会被填值层写，它回写成什么无从判断，这一档因此不成立。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["workflow"]["12"] = {"class_type": "WanVideoLength", "inputs": {"num_frames": ["5", 0]}}
        definition["bindings"]["frames"] = [
            _frames_target(step=4),
            {"node": "12", "input": "num_frames", "class_type": "WanVideoLength"},
        ]

        assert native_duration(definition) is None

    def test_a_second_frames_input_that_the_tier_would_rewrite_leaves_no_native_tier(self):
        """两个帧数入口字面值不一致：选中 5 秒会把 65 那个也写成 81，这一档不算原生。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["workflow"]["12"] = {"class_type": "WanVideoLength", "inputs": {"num_frames": 65}}
        definition["bindings"]["frames"] = [
            _frames_target(step=4),
            {"node": "12", "input": "num_frames", "class_type": "WanVideoLength"},
        ]

        assert native_duration(definition) is None
        assert default_supported_durations(definition) == []

    def test_every_frames_input_writing_back_its_own_literal_keeps_the_tier(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["workflow"]["12"] = {"class_type": "WanVideoLength", "inputs": {"num_frames": 81}}
        definition["bindings"]["frames"] = [
            _frames_target(step=4),
            {"node": "12", "input": "num_frames", "class_type": "WanVideoLength", "step": 4},
        ]

        assert native_duration(definition) == 5

    def test_a_literal_the_step_itself_would_realign_is_not_a_native_tier_either(self):
        """76 帧 @ 15fps 正好折回 5 秒，但步长 4 会把它对齐到 73：选中即改图，故不是原生档。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["9"]["inputs"]["fps"] = 15
        definition["workflow"]["5"]["inputs"]["length"] = 76
        definition["bindings"]["frames"] = [_frames_target(step=4)]

        assert native_duration(definition) is None

    def test_the_conversion_is_the_inverse_of_the_one_the_request_builder_uses(self):
        """``frames = round(时长 × 帧率) + 1`` 与本处的反算必须同源。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["9"]["inputs"]["fps"] = 24
        definition["workflow"]["5"]["inputs"]["length"] = (7 * 24) + 1
        definition["bindings"]["frames"] = [_frames_target()]

        assert native_duration(definition) == 7


class TestFpsLiterals:
    def test_every_read_only_binding_contributes_its_literal(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["21"] = {"class_type": "CreateVideo", "inputs": {"fps": 24}}
        definition["bindings"]["fps"].append(
            {"node": "21", "input": "fps", "class_type": "CreateVideo", "direction": "read"}
        )

        assert fps_literals(definition["workflow"], definition["bindings"]) == [16.0, 24.0]

    def test_a_target_whose_node_is_gone_contributes_nothing(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["fps"].append(
            {"node": "404", "input": "fps", "class_type": "CreateVideo", "direction": "read"}
        )

        assert fps_literals(definition["workflow"], definition["bindings"]) == [16.0]
