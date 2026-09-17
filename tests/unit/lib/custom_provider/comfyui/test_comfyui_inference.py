"""节点绑定推断：候选、分数、信号与提示。

样本 workflow 取自 ComfyUI 官方模板库与 Kijai 的社区示例所抽样出的节点形态，按「Export (API)」
格式写成——官方模板库存的是画布存档，不能直接当 API 格式用。
"""

import json
from pathlib import Path

import pytest

from lib.custom_provider.comfyui.bindings import BINDING_KEYS_BY_MEDIA_TYPE
from lib.custom_provider.comfyui.inference import (
    BindingSignal,
    BindingState,
    InferenceNote,
    infer_bindings,
)

DATA = Path(__file__).parent / "data"

#: 样本 workflow → 它产出的媒体类型。
SAMPLES = {
    "wan21_t2v": "video",
    "wan22_moe_t2v": "video",
    "ltxv_i2v": "video",
    "two_stage_sdxl_svd": "video",
    "kijai_wanvideo_flf2v": "video",
    "wan_vace_flf2v": "video",
    "flux_kontext_edit": "image",
    "qwen_image_edit_2509": "image",
    "sdxl_batch_t2i": "image",
}

#: 分级信号自高到低，每一级都要有夹具命中。
GRADED_SIGNALS = (
    BindingSignal.MANUAL_BINDING,
    BindingSignal.TITLE_MARKER,
    BindingSignal.EXTERNAL_NODE_FAMILY,
    BindingSignal.SAMPLER_PORT_TRACE,
    BindingSignal.ALIAS_WITH_CLASS_TYPE,
    BindingSignal.ALIAS_ONLY,
    BindingSignal.LINK_TRACE,
)


def sample_workflow(name: str) -> dict:
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def infer_sample(name: str, *, bindings: dict | None = None, workflow: dict | None = None):
    return infer_bindings(
        {
            "media_type": SAMPLES[name],
            "workflow": workflow if workflow is not None else sample_workflow(name),
            "bindings": bindings if bindings is not None else {},
        }
    )


def landings(result, key: str) -> list[tuple[str, str | None]]:
    return [(c.node, c.input) for c in result.keys[key].candidates if c.selected]


def offered(result, key: str) -> list[tuple[str, str | None]]:
    return [(c.node, c.input) for c in result.keys[key].candidates]


def signals_on(result, key: str) -> set[str]:
    return {hit.signal.value for c in result.keys[key].candidates for hit in c.signals}


def graded_on(result, key: str) -> set[str]:
    return signals_on(result, key) & {signal.value for signal in GRADED_SIGNALS}


def note_codes(result, key: str) -> set[str]:
    return {note.code.value for note in result.keys[key].notes}


def marked(workflow: dict, node: str, title: str) -> dict:
    workflow[node]["_meta"] = {"title": title}
    return workflow


def external_workflow() -> dict:
    """装了 ComfyUI-Deploy 参数化节点的图：正向文本由外部节点驱动。"""
    return {
        "1": {"class_type": "ComfyUIDeployExternalText", "inputs": {"input_id": "prompt", "default_value": "一只猫"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 0], "clip": ["3", 1]}},
        "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan.safetensors"}},
        "4": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["3", 0], "positive": ["2", 0]}},
        "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["3", 2]}},
        "6": {"class_type": "CreateVideo", "inputs": {"fps": 16, "images": ["5", 0]}},
        "7": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/x", "video": ["6", 0]}},
    }


# ---------------------------------------------------------------------------
# 覆盖面
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_sample_gets_a_verdict_on_every_semantic_key(name):
    result = infer_sample(name)
    assert set(result.keys) == BINDING_KEYS_BY_MEDIA_TYPE[SAMPLES[name]]
    for key, verdict in result.keys.items():
        assert isinstance(verdict.state, BindingState), key


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_candidate_carries_a_score_and_at_least_one_signal(name):
    for verdict in infer_sample(name).keys.values():
        for candidate in verdict.candidates:
            assert candidate.signals
            assert candidate.score == sum(hit.weight for hit in candidate.signals)


def test_each_graded_signal_is_hit_by_at_least_one_fixture():
    seen: set[str] = set()
    for name in SAMPLES:
        for verdict in infer_sample(name).keys.values():
            seen |= {hit.signal.value for c in verdict.candidates for hit in c.signals}
    seen |= signals_on(
        infer_sample("wan21_t2v", workflow=marked(sample_workflow("wan21_t2v"), "7", "ARCREEL:prompt")), "prompt"
    )
    seen |= signals_on(
        infer_bindings({"media_type": "video", "workflow": external_workflow(), "bindings": {}}), "prompt"
    )
    seen |= signals_on(
        infer_sample(
            "wan21_t2v",
            bindings={"seed": [{"node": "3", "input": "seed", "class_type": "KSampler", "title": "KSampler"}]},
        ),
        "seed",
    )
    assert {signal.value for signal in GRADED_SIGNALS} <= seen


# ---------------------------------------------------------------------------
# 三种状态
# ---------------------------------------------------------------------------


def test_a_single_highest_score_is_selected_automatically():
    result = infer_sample("wan21_t2v")
    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert landings(result, "prompt") == [("6", "text")]


def test_a_tie_at_the_top_is_ambiguous_and_selects_nothing():
    """两段式 workflow 的两个采样器各有一个种子，谁也不比谁更像答案。"""
    result = infer_sample("two_stage_sdxl_svd")
    assert result.keys["seed"].state is BindingState.AMBIGUOUS
    assert landings(result, "seed") == []
    assert offered(result, "seed") == [("3", "seed"), ("14", "seed")]
    assert result.savable is False


def test_zero_candidates_reads_as_not_found():
    """产物节点只预览不落盘时一个候选也没有，而不是把预览节点当成成片。"""
    result = infer_sample("kijai_wanvideo_flf2v")
    assert result.keys["output"].state is BindingState.NOT_FOUND
    assert result.keys["output"].candidates == ()


def test_an_empty_list_means_the_workflow_does_not_support_that_semantic():
    result = infer_sample("wan21_t2v", bindings={"end_image": []})
    assert result.keys["end_image"].state is BindingState.UNSUPPORTED
    assert result.keys["end_image"].candidates == ()


def test_a_missing_key_is_inferred_from_scratch():
    result = infer_sample(
        "wan21_t2v",
        bindings={
            "prompt": [
                {
                    "node": "6",
                    "input": "text",
                    "class_type": "CLIPTextEncode",
                    "title": "CLIP Text Encode (Positive Prompt)",
                }
            ]
        },
    )
    assert result.keys["prompt"].candidates[0].origin.value == "kept"
    assert result.keys["width"].state is BindingState.AUTO_SELECTED


# ---------------------------------------------------------------------------
# 标题约定
# ---------------------------------------------------------------------------


def test_a_title_marker_suppresses_the_other_signals_on_that_key_only():
    """标记按语义键抑制推断：正向文本改判给被标记的节点，负向仍按端口反溯。"""
    workflow = marked(sample_workflow("wan21_t2v"), "7", "ARCREEL:prompt 我说了算")
    result = infer_sample("wan21_t2v", workflow=workflow)
    assert landings(result, "prompt") == [("7", "text")]
    assert graded_on(result, "prompt") == {BindingSignal.TITLE_MARKER.value}
    assert landings(result, "negative_prompt") == [("7", "text")]


def test_a_title_marker_is_case_insensitive_and_stops_at_the_first_space():
    workflow = marked(sample_workflow("wan21_t2v"), "40", "arcreel:width 宽")
    assert landings(infer_sample("wan21_t2v", workflow=workflow), "width") == [("40", "width")]


def test_a_marked_node_with_several_fields_is_narrowed_by_the_input_name():
    """``ARCREEL:width`` 打在同时有 width 与 height 的节点上只绑 width。"""
    workflow = marked(sample_workflow("wan21_t2v"), "40", "ARCREEL:width")
    result = infer_sample("wan21_t2v", workflow=workflow)
    assert landings(result, "width") == [("40", "width")]
    assert ("40", "length") not in offered(result, "width")


def test_reference_image_markers_are_numbered_by_ascending_node_id():
    workflow = sample_workflow("flux_kontext_edit")
    marked(workflow, "147", "ARCREEL:reference_images 后一张")
    marked(workflow, "142", "ARCREEL:reference_images 前一张")
    result = infer_sample("flux_kontext_edit", workflow=workflow)
    assert landings(result, "reference_images") == [("142", "image"), ("147", "image")]


# ---------------------------------------------------------------------------
# 外部约定节点族
# ---------------------------------------------------------------------------


def test_an_external_parameter_node_outranks_the_field_it_drives():
    result = infer_bindings({"media_type": "video", "workflow": external_workflow(), "bindings": {}})
    assert landings(result, "prompt") == [("1", "default_value")]
    assert BindingSignal.EXTERNAL_NODE_FAMILY.value in signals_on(result, "prompt")


def test_an_external_node_whose_parameter_names_another_semantic_is_left_alone():
    workflow = external_workflow()
    workflow["1"]["inputs"]["input_id"] = "my_own_knob"
    result = infer_bindings({"media_type": "video", "workflow": workflow, "bindings": {}})
    assert offered(result, "prompt") == []


# ---------------------------------------------------------------------------
# 正负提示词
# ---------------------------------------------------------------------------


def test_polarity_comes_from_the_sampler_ports_not_from_the_node_order():
    result = infer_sample("wan21_t2v")
    assert landings(result, "prompt") == [("6", "text")]
    assert landings(result, "negative_prompt") == [("7", "text")]


def test_a_workflow_without_a_negative_path_reports_not_found_instead_of_the_positive_node():
    """负向条件由正向清零得来时，正确答案是「这份 workflow 没有负向文本」。"""
    result = infer_sample("flux_kontext_edit")
    assert result.keys["negative_prompt"].state is BindingState.NOT_FOUND
    assert landings(result, "prompt") == [("6", "text")]


def test_one_node_holding_both_polarities_is_split_by_input_name():
    """社区形态的采样器没有正负端口，两段文本挤在同一个节点的两个字段里。"""
    result = infer_sample("kijai_wanvideo_flf2v")
    assert landings(result, "prompt") == [("18", "positive_prompt")]
    assert landings(result, "negative_prompt") == [("18", "negative_prompt")]


def test_the_qwen_editor_carries_prompts_on_a_field_not_called_text():
    result = infer_sample("qwen_image_edit_2509")
    assert landings(result, "prompt") == [("111", "prompt")]
    assert landings(result, "negative_prompt") == [("110", "prompt")]


# ---------------------------------------------------------------------------
# 图像类语义
# ---------------------------------------------------------------------------


def test_the_image_field_means_different_things_on_different_carriers():
    """``image`` 在读图节点上是文件名、在模型条件节点上是首帧、在缩放节点上只是中间连线。"""
    result = infer_sample("ltxv_i2v")
    assert landings(result, "start_image") == [("78", "image")]
    assert ("79", "image") not in offered(result, "start_image")


def test_first_and_last_frames_are_told_apart_by_the_port_they_feed():
    result = infer_sample("kijai_wanvideo_flf2v")
    assert landings(result, "start_image") == [("20", "image")]
    assert landings(result, "end_image") == [("21", "image")]


def test_reference_images_record_the_entry_they_feed_in_list_order():
    result = infer_sample("flux_kontext_edit")
    targets = [c.target for c in result.keys["reference_images"].candidates if c.selected]
    assert [t["node"] for t in targets] == ["142", "147"]
    assert [t["consumer"]["input"] for t in targets] == ["image1", "image2"]


def test_an_entry_that_cannot_be_rewired_warns_about_repeated_filling():
    """名录外的读图入口摘不掉也 bypass 不了，张数少于格子数时只能重复最后一张。"""
    workflow = sample_workflow("wan_vace_flf2v")
    workflow["90"] = {"class_type": "MyStyleReference", "inputs": {"reference_image": ["82", 0], "weight": 0.6}}
    workflow["84"]["inputs"].pop("reference_image")
    result = infer_sample("wan_vace_flf2v", workflow=workflow)
    assert landings(result, "reference_images") == [("82", "image")]
    assert InferenceNote.REFERENCE_CONSUMER_UNKNOWN.value in note_codes(result, "reference_images")


def test_an_optional_entry_is_dropped_instead_of_repeated_and_needs_no_warning():
    result = infer_sample("wan_vace_flf2v")
    assert landings(result, "reference_images") == [("82", "image")]
    assert note_codes(result, "reference_images") == set()


def test_frames_encoded_as_a_control_video_are_not_reported_as_reference_images():
    """VACE 把首尾帧拼成图像批次送进控制视频；那两张图不是参考图，也推断不出首尾帧。"""
    result = infer_sample("wan_vace_flf2v")
    assert [node for node, _ in offered(result, "reference_images")] == ["82"]
    assert result.keys["start_image"].state is BindingState.NOT_FOUND
    assert InferenceNote.MANUAL_ONLY_NODE.value in {note.code.value for note in result.notes}


# ---------------------------------------------------------------------------
# 尺寸、帧数与帧率
# ---------------------------------------------------------------------------


def test_a_two_stage_workflow_takes_the_size_group_closest_to_the_output():
    result = infer_sample("two_stage_sdxl_svd")
    assert landings(result, "width") == [("12", "width")]
    assert ("5", "width") in offered(result, "width")


def test_the_bound_size_entry_carries_the_step_of_its_carrier():
    result = infer_sample("wan21_t2v")
    assert result.keys["width"].selected_targets[0]["step"] == 16
    assert infer_sample("two_stage_sdxl_svd").keys["width"].selected_targets[0]["step"] == 8


def test_frames_come_from_the_carrier_field_whatever_it_is_called():
    assert landings(infer_sample("wan21_t2v"), "frames") == [("40", "length")]
    assert landings(infer_sample("kijai_wanvideo_flf2v"), "frames") == [("24", "num_frames")]
    assert landings(infer_sample("two_stage_sdxl_svd"), "frames") == [("12", "video_frames")]


def test_fps_binds_the_output_side_node_not_the_model_side_frame_rate():
    result = infer_sample("ltxv_i2v")
    assert landings(result, "fps") == [("57", "fps")]
    assert ("69", "frame_rate") not in offered(result, "fps")
    assert result.keys["fps"].selected_targets[0]["direction"] == "read"


def test_a_wired_field_binds_the_constant_node_it_comes_from():
    result = infer_sample("wan22_moe_t2v")
    assert landings(result, "fps") == [("161", "value")]


def test_a_field_computed_upstream_is_left_unbound_with_a_hint():
    result = infer_sample("wan22_moe_t2v")
    assert result.keys["frames"].state is BindingState.NOT_FOUND
    assert InferenceNote.COMPUTED_SOURCE.value in note_codes(result, "frames")


# ---------------------------------------------------------------------------
# 种子与产物
# ---------------------------------------------------------------------------


def test_only_the_sampler_that_actually_takes_noise_is_a_seed_candidate():
    """MoE 的低噪那一档 ``add_noise`` 是 disable，种子恒为 0。"""
    result = infer_sample("wan22_moe_t2v")
    assert landings(result, "seed") == [("70", "noise_seed")]
    assert ("71", "noise_seed") not in offered(result, "seed")


def test_the_seed_entry_defaults_to_a_fresh_value_every_submission():
    assert infer_sample("wan21_t2v").keys["seed"].selected_targets[0]["policy"] == "random"


def test_a_video_saver_outranks_an_image_saver_in_the_same_graph():
    workflow = sample_workflow("wan21_t2v")
    workflow["59"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "still", "images": ["8", 0]}}
    result = infer_sample("wan21_t2v", workflow=workflow)
    assert landings(result, "output") == [("58", None)]


def test_a_preview_node_never_becomes_the_output():
    result = infer_sample("two_stage_sdxl_svd")
    assert landings(result, "output") == [("58", None)]
    assert ("20", None) not in offered(result, "output")


def test_the_deeper_of_two_savers_wins_and_equal_depth_goes_to_the_user():
    workflow = sample_workflow("wan21_t2v")
    workflow["59"] = {"class_type": "CreateVideo", "inputs": {"fps": 16, "images": ["8", 0]}}
    workflow["60"] = {"class_type": "SaveVideo", "inputs": {"filename_prefix": "b", "video": ["59", 0]}}
    tied = infer_sample("wan21_t2v", workflow=workflow)
    assert tied.keys["output"].state is BindingState.AMBIGUOUS

    workflow["60"]["inputs"]["video"] = ["61", 0]
    workflow["61"] = {"class_type": "CreateVideo", "inputs": {"fps": 16, "images": ["62", 0]}}
    workflow["62"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"image": ["8", 0]}}
    deeper = infer_sample("wan21_t2v", workflow=workflow)
    assert landings(deeper, "output") == [("60", None)]


def test_a_saver_that_only_previews_is_excluded_but_still_carries_the_frame_rate():
    result = infer_sample("kijai_wanvideo_flf2v")
    assert offered(result, "output") == []
    assert landings(result, "fps") == [("30", "frame_rate")]


# ---------------------------------------------------------------------------
# 整图提示与可保存性
# ---------------------------------------------------------------------------


def test_a_batch_field_above_one_is_reported_without_touching_the_workflow():
    result = infer_sample("sdxl_batch_t2i")
    codes = {note.code.value for note in result.notes}
    assert InferenceNote.BATCH_SIZE_ABOVE_ONE.value in codes


def test_a_result_is_savable_only_when_nothing_is_pending_and_the_two_required_keys_landed():
    assert infer_sample("wan21_t2v").savable is True
    assert infer_sample("kijai_wanvideo_flf2v").savable is False
    assert infer_sample("two_stage_sdxl_svd").savable is False
