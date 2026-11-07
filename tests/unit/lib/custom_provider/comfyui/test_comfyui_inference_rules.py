"""Data contracts for node-binding inference rule tables."""

import json

import pytest
from jsonschema import Draft202012Validator

from arcreel_market_core.comfyui.bindings import BINDING_KEYS_BY_MEDIA_TYPE
from lib.custom_provider.comfyui import inference_rules as rules_module
from lib.custom_provider.comfyui.inference import SIGNAL_WEIGHTS, WEAKEST_GRADED_WEIGHT, BindingSignal
from lib.custom_provider.comfyui.inference_rules import (
    RULES_PATHS,
    ConsumerPort,
    InputName,
    load_inference_rules,
    load_rules_schema,
    semantic_key_names,
)

#: Sections identical in both rule tables: these are node-type facts, independent of image or video output.
SHARED_SECTIONS = (
    "constant_nodes",
    "optional_inputs",
    "merge_nodes",
    "external_families",
    "seed_gates",
    "sampler_ports",
)


def read(media_type: str) -> dict:
    return json.loads(RULES_PATHS[media_type].read_text(encoding="utf-8"))


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_shipped_rules_pass_their_schema(media_type):
    validator = Draft202012Validator(load_rules_schema())
    Draft202012Validator.check_schema(load_rules_schema())
    assert list(validator.iter_errors(read(media_type))) == []


@pytest.mark.parametrize("section", SHARED_SECTIONS)
def test_node_type_facts_are_identical_across_both_rule_files(section):
    """Media-independent sections must not drift: the same node does not change shape based on endpoint image/video output."""
    assert read("image")[section] == read("video")[section]


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_every_adjustable_input_comes_from_one_of_the_two_tables(media_type):
    """Only two input shapes can be modified for fewer images: remove optional input keys or bypass pairwise merge nodes.

    The inference notice that a slot cannot modify the graph and request construction's choice of graph modification versus repeated filling
    read this same rule-table criterion, rather than maintaining separate tables.
    """
    rules = load_inference_rules(media_type)

    assert rules.adjustable_input("WanVaceToVideo", "reference_image")
    assert rules.adjustable_input("ImageBatch", "image2")
    assert not rules.adjustable_input("ImageStitch", "image1")
    assert not rules.adjustable_input("SomeCustomImageMasher", "image1")


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_minimax_h3_slots_are_adjustable(media_type):
    """Hailuo H3 first/last-frame and reference-image slots are optional node inputs.

    Without registration, construction treats them as required: removing image loaders for absent first/last frames cascades to the output node, while fewer references
    fall back to repeating the last image. Reference images use autogrow inputs; one ``ref_images.ref_image_`` prefix entry registers all slots.
    """
    rules = load_inference_rules(media_type)

    assert rules.adjustable_input("MiniMaxH3ImageToVideo", "first_frame")
    assert rules.adjustable_input("MiniMaxH3ImageToVideo", "last_frame")
    for slot in range(9):
        assert rules.adjustable_input("MiniMaxH3ReferenceToVideo", f"ref_images.ref_image_{slot}")


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_a_prefixed_optional_input_only_matches_its_own_node_and_prefix(media_type):
    rules = load_inference_rules(media_type)

    assert rules.is_optional_input("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_3")
    assert not rules.is_optional_input("MiniMaxH3ImageToVideo", "ref_images.ref_image_3")
    assert not rules.is_optional_input("MiniMaxH3ReferenceToVideo", "ref_images.mask_0")
    assert not rules.is_optional_input("MiniMaxH3ReferenceToVideo", "ref_image_0")


def test_minimax_h3_reference_slots_are_one_prefixed_entry_per_section():
    """Per-slot autogrow registration would drift with node slot limits; consolidate into one prefix entry."""
    video = read("video")

    assert [item for item in video["optional_inputs"] if item["class_type"] == "MiniMaxH3ReferenceToVideo"] == [
        {"class_type": "MiniMaxH3ReferenceToVideo", "input_prefix": "ref_images.ref_image_"}
    ]
    assert [
        port
        for port in video["consumer_ports"]["reference_images"]
        if "MiniMaxH3ReferenceToVideo" in port.get("class_types", [])
    ] == [{"input_prefix": "ref_images.ref_image_", "class_types": ["MiniMaxH3ReferenceToVideo"]}]


def test_a_prefixed_consumer_port_matches_by_prefix_within_its_class_types():
    port = ConsumerPort(InputName("ref_images.ref_image_", prefix=True), frozenset({"MiniMaxH3ReferenceToVideo"}))

    assert port.matches("ref_images.ref_image_0", "MiniMaxH3ReferenceToVideo")
    assert port.matches("ref_images.ref_image_8", "MiniMaxH3ReferenceToVideo")
    assert not port.matches("ref_images.ref_image_0", "MiniMaxH3ImageToVideo")
    assert not port.matches("ref_images.mask_0", "MiniMaxH3ReferenceToVideo")


def test_an_unrestricted_prefixed_consumer_port_matches_on_any_node():
    port = ConsumerPort(InputName("images.image_", prefix=True), frozenset())

    assert port.matches("images.image_2", "AnyNode")
    assert not port.matches("image", "AnyNode")


def test_an_exact_consumer_port_does_not_match_a_longer_name():
    port = ConsumerPort(InputName("reference_image"), frozenset())

    assert port.matches("reference_image", "WanVaceToVideo")
    assert not port.matches("reference_image_2", "WanVaceToVideo")


@pytest.mark.parametrize(
    ("section", "entry"),
    [
        ("optional_inputs", {"class_type": "X", "input_prefix": "ref_images.ref_image_"}),
        ("consumer_ports", {"input_prefix": "ref_images.ref_image_"}),
        ("consumer_ports", {"input_prefix": "ref_images.ref_image_", "class_types": ["X"]}),
    ],
)
def test_the_schema_accepts_a_prefixed_entry(section, entry):
    assert list(Draft202012Validator(load_rules_schema()).iter_errors(_with_entry(section, entry))) == []


@pytest.mark.parametrize(
    ("section", "entry"),
    [
        ("optional_inputs", {"class_type": "X", "input": "a", "input_prefix": "a_"}),
        ("optional_inputs", {"class_type": "X"}),
        ("consumer_ports", {"input": "a", "input_prefix": "a_"}),
        ("consumer_ports", {"class_types": ["X"]}),
    ],
)
def test_the_schema_wants_exactly_one_of_input_and_input_prefix(section, entry):
    assert list(Draft202012Validator(load_rules_schema()).iter_errors(_with_entry(section, entry)))


def test_prefix_forms_stay_out_of_the_other_node_input_sections():
    """Only optional-input and consumer-input sections recognize prefixes; image loaders and other nodes read exact input names, where prefixes have no meaning."""
    entry = {"class_type": "X", "input_prefix": "a_"}

    for section in ("image_loaders", "batch_size_inputs"):
        document = read("video") | {section: [entry]}
        assert list(Draft202012Validator(load_rules_schema()).iter_errors(document))


def _with_entry(section: str, entry: dict) -> dict:
    document = read("video")
    if section == "consumer_ports":
        return document | {"consumer_ports": {**document["consumer_ports"], "reference_images": [entry]}}
    return document | {section: [entry]}


@pytest.mark.parametrize("media_type", sorted(RULES_PATHS))
def test_every_semantic_key_the_rules_mention_is_on_that_media_types_roster(media_type):
    """Every rule-table key must exist in the catalog: misspellings still pass schema validation but leave that rule section unreadable to all consumers."""
    assert semantic_key_names(read(media_type)) <= set(BINDING_KEYS_BY_MEDIA_TYPE[media_type])


@pytest.mark.parametrize(
    "section",
    [
        {"semantic_keys": {"framez": {}}},
        {"consumer_ports": {"framez": []}},
        {"class_type_tiers": {"framez": []}},
        {"steps": {"framez": {"default": 8}}},
        {"sampler_ports": {"positive": "framez"}},
        {"conditioning_slots": {"0": "framez"}},
        {"manual_only_class_types": [{"class_type": "X", "binding_keys": ["framez"]}]},
    ],
)
def test_a_mistyped_key_name_is_seen_wherever_a_section_names_one(section):
    assert "framez" in semantic_key_names(section)


def test_a_rule_table_naming_a_key_off_the_roster_fails_at_load(tmp_path, monkeypatch):
    """Malformed packaged data must fail on load instead of silently preventing inference for a semantic key."""
    path = tmp_path / "inference_rules.video.json"
    path.write_text(json.dumps(read("video") | {"consumer_ports": {"framez": []}}), encoding="utf-8")
    monkeypatch.setattr(rules_module, "RULES_PATHS", {**RULES_PATHS, "video": path})
    load_inference_rules.cache_clear()

    with pytest.raises(ValueError, match="framez"):
        load_inference_rules("video")

    load_inference_rules.cache_clear()


def sub_signal_ceiling() -> int:
    """Upper bound on a candidate's total secondary signals, calculated from the shipped data file.

    Carrier-node tier and output priority occupy the same signal slot; a candidate can match only one, so take the larger value.
    Add the other two secondary signals whose voting weights do not vary with the data.
    """
    rules = [load_inference_rules(media_type) for media_type in RULES_PATHS]
    tiers = max(len(tier) for rule in rules for tier in rule.class_type_tiers.values())
    ranks = max(len(rule.output_candidates) for rule in rules)
    return max(tiers, ranks) + SIGNAL_WEIGHTS[BindingSignal.OUTPUT_CHAIN] + SIGNAL_WEIGHTS[BindingSignal.TITLE_POLARITY]


def test_sub_signals_cannot_outrank_the_weakest_graded_signal():
    """Secondary signals only break ties: their combined sum must not outweigh any ranked signal level."""
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
    """Audio depends on whether the output node (or its upstream final-video node) has a connected audio input."""
    sources = {source.class_type: source for source in load_inference_rules("video").audio_track_sources}
    assert sources["VHS_VideoCombine"].audio_input == "audio"
    assert sources["SaveVideo"].through_input == "video"
    assert "CreateVideo" in sources["SaveVideo"].through_class_types


def test_unknown_media_type_is_a_caller_error():
    with pytest.raises(KeyError):
        load_inference_rules("audio")
