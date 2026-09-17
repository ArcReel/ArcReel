"""节点绑定推断规则表的数据契约。"""

import json

import pytest
from jsonschema import Draft202012Validator

from lib.custom_provider.comfyui.inference import SIGNAL_WEIGHTS, WEAKEST_GRADED_WEIGHT, BindingSignal
from lib.custom_provider.comfyui.inference_rules import RULES_PATHS, load_inference_rules, load_rules_schema

#: 两份规则表里逐字相同的几节：它们是节点类型的事实，与产图还是产视频无关。
SHARED_SECTIONS = ("constant_nodes", "merge_nodes", "external_families", "seed_gates", "sampler_ports")


def read(media_type: str) -> dict:
    return json.loads(RULES_PATHS[media_type].read_text(encoding="utf-8"))


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_shipped_rules_pass_their_schema(media_type):
    validator = Draft202012Validator(load_rules_schema())
    Draft202012Validator.check_schema(load_rules_schema())
    assert list(validator.iter_errors(read(media_type))) == []


@pytest.mark.parametrize("section", SHARED_SECTIONS)
def test_node_type_facts_are_identical_across_both_rule_files(section):
    """与媒体类型无关的几节不得漂移：同一个节点不会因为端点产图还是产视频而改变形状。"""
    assert read("image")[section] == read("video")[section]


def sub_signal_ceiling() -> int:
    """一条候选能拿到的次级信号加总上界，按实发数据文件算。

    承载节点档位与产物优先级同一个信号位，一条候选只可能命中其中一个，故取两者的较大值；
    加表决权重不随数据变化的另外两个次级信号。
    """
    rules = [load_inference_rules(media_type) for media_type in RULES_PATHS]
    tiers = max(len(tier) for rule in rules for tier in rule.class_type_tiers.values())
    ranks = max(len(rule.output_candidates) for rule in rules)
    return max(tiers, ranks) + SIGNAL_WEIGHTS[BindingSignal.OUTPUT_CHAIN] + SIGNAL_WEIGHTS[BindingSignal.TITLE_POLARITY]


def test_sub_signals_cannot_outrank_the_weakest_graded_signal():
    """次级信号只决同分先后：它们加总也压不过任何一级分级信号。"""
    assert sub_signal_ceiling() < WEAKEST_GRADED_WEIGHT


def test_each_graded_signal_outranks_everything_below_it():
    graded = [
        BindingSignal.MANUAL_BINDING,
        BindingSignal.TITLE_MARKER,
        BindingSignal.EXTERNAL_NODE_FAMILY,
        BindingSignal.SAMPLER_PORT_TRACE,
        BindingSignal.ALIAS_WITH_CLASS_TYPE,
        BindingSignal.ALIAS_ONLY,
        BindingSignal.LINK_TRACE,
    ]
    ceiling = sub_signal_ceiling()
    for index, signal in enumerate(graded):
        below = sum(SIGNAL_WEIGHTS[lower] for lower in graded[index + 1 :])
        assert SIGNAL_WEIGHTS[signal] > below + ceiling


def test_size_tiers_put_model_conditioning_nodes_above_empty_latent_nodes():
    rules = load_inference_rules("video")
    assert rules.tier_of("width", "WanImageToVideo") > rules.tier_of("width", "EmptyHunyuanLatentVideo")
    assert rules.tier_of("width", "EmptyHunyuanLatentVideo") > rules.tier_of("width", "ImageScale")
    assert rules.tier_of("width", "CLIPTextEncode") == 0


def test_output_candidate_ranking_follows_the_sampled_frequency():
    rules = load_inference_rules("video")
    assert rules.output_rank("SaveVideo") > rules.output_rank("VHS_VideoCombine")
    assert rules.output_rank("VHS_VideoCombine") > rules.output_rank("SaveImage")
    assert rules.output_rank("PreviewImage") == 0


def test_frame_step_follows_the_carrier_model_family():
    rules = load_inference_rules("video")
    assert rules.step_of("frames", "WanImageToVideo") == 4
    assert rules.step_of("frames", "EmptyLTXVLatentVideo") == 8
    assert rules.step_of("width", "EmptyLatentImage") == 8
    assert rules.step_of("width", "EmptyHunyuanLatentVideo") == 16


def test_image_rules_drop_the_time_axis_and_the_first_last_frames():
    image = load_inference_rules("image")
    assert "frames" not in image.semantic_keys
    assert "fps" not in image.semantic_keys
    assert "start_image" not in image.consumer_ports
    assert "end_image" not in image.consumer_ports


def test_external_families_are_recognised_by_class_type_prefix():
    rules = load_inference_rules("video")
    deploy = rules.family_of("ComfyUIDeployExternalTextAny")
    assert deploy is not None
    assert deploy.parameter_of({"input_id": " prompt "}, "") == "prompt"
    pack = rules.family_of("CPackInputImage")
    assert pack is not None
    assert pack.parameter_of({}, "start_image") == "start_image"
    assert rules.family_of("CLIPTextEncode") is None


def test_the_audio_track_rule_points_at_the_output_side_node():
    """音轨看的是产物节点（或它上游那个成片节点）的音频入口有没有连线。"""
    sources = {source.class_type: source for source in load_inference_rules("video").audio_track_sources}
    assert sources["VHS_VideoCombine"].audio_input == "audio"
    assert sources["SaveVideo"].through_input == "video"
    assert "CreateVideo" in sources["SaveVideo"].through_class_types


def test_unknown_media_type_is_a_caller_error():
    with pytest.raises(KeyError):
        load_inference_rules("audio")
