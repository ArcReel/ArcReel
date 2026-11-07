"""Build submitted workflows: filling values, dimensions/frame-count conversion, seeds, `Avoid` splitting, and image-count-driven graph changes."""

from __future__ import annotations

from random import Random
from typing import Any

import pytest

from lib.custom_provider.comfyui.failures import IMAGE_DROP_UNSUPPORTED, ComfyuiError
from lib.custom_provider.comfyui.request_builder import (
    SEED_UPPER_BOUND,
    MediaInputs,
    build_workflow,
    workflow_sha256,
)
from lib.generation.task_failure import FAILURE_CODE_KEYS, encode_failure, render_failure
from tests.factories import comfyui_endpoint_definition, make_display_names, make_translator


def _build(definition: dict[str, Any], **kwargs: Any):
    """Build one submitted workflow with useful defaults for every argument dimension."""
    kwargs.setdefault("prompt", "一只猫走过屋顶")
    kwargs.setdefault("aspect_ratio", "9:16")
    kwargs.setdefault("rng", Random(0))
    return build_workflow(definition, **kwargs)


def _inputs(built: Any, node_id: str) -> dict[str, Any]:
    return built.workflow[node_id]["inputs"]


class TestDraftIsNeverTouched:
    def test_the_definition_workflow_stays_untouched(self):
        definition = comfyui_endpoint_definition()

        built = _build(definition, resolution="720p")

        assert definition["workflow"]["5"]["inputs"]["width"] == 832
        assert definition["workflow"]["6"]["inputs"]["text"] == "一只猫"
        assert _inputs(built, "6")["text"] == "一只猫走过屋顶"

    def test_the_prompt_body_is_written_verbatim_to_every_prompt_target(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"].append(
            {"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}
        )

        built = _build(definition, prompt="镜头缓缓推近\n第二行")

        assert _inputs(built, "6")["text"] == "镜头缓缓推近\n第二行"
        assert _inputs(built, "7")["text"] == "镜头缓缓推近\n第二行"


class TestWorkflowDigest:
    def test_the_same_inputs_produce_the_same_digest(self):
        first = _build(comfyui_endpoint_definition(), resolution="720p", seed=7)
        second = _build(comfyui_endpoint_definition(), resolution="720p", seed=7)

        assert first.workflow_sha256 == second.workflow_sha256
        assert len(first.workflow_sha256) == 64

    def test_the_digest_ignores_key_insertion_order(self):
        assert workflow_sha256({"a": {"class_type": "X"}, "b": {"class_type": "Y"}}) == workflow_sha256(
            {"b": {"class_type": "Y"}, "a": {"class_type": "X"}}
        )

    def test_a_changed_value_changes_the_digest(self):
        assert (
            _build(comfyui_endpoint_definition(), seed=1).workflow_sha256
            != _build(comfyui_endpoint_definition(), seed=2).workflow_sha256
        )


class TestSize:
    def test_the_tier_short_edge_drives_the_derived_size(self):
        built = _build(comfyui_endpoint_definition(), aspect_ratio="9:16", resolution="720p")

        assert (built.width, built.height) == (720, 1280)
        assert _inputs(built, "5")["width"] == 720
        assert _inputs(built, "5")["height"] == 1280

    def test_the_project_ratio_wins_over_the_ratio_implied_by_a_custom_resolution(self):
        built = _build(comfyui_endpoint_definition(), aspect_ratio="16:9", resolution="1280*720")

        assert built.width > built.height

    def test_an_unselected_resolution_takes_the_shorter_literal_edge(self):
        """The template's 832×480 is the author's tuned tier: use short side 480 while keeping the project ratio (720p instead yields 720)."""
        literal = _build(comfyui_endpoint_definition(), aspect_ratio="9:16", resolution=None)
        tiered = _build(comfyui_endpoint_definition(), aspect_ratio="9:16", resolution="720p")

        assert (literal.width, literal.height) == (432, 768)
        assert (tiered.width, tiered.height) == (720, 1280)

    def test_the_round_to_is_the_least_common_multiple_of_both_steps(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["width"][0]["step"] = 8
        definition["bindings"]["height"][0]["step"] = 12

        built = _build(definition, aspect_ratio="1:1", resolution="720p")

        assert (built.width, built.height) == (720, 720)

    def test_each_target_is_aligned_down_to_its_own_step(self):
        """Both sides step by 64: short side 1080 aligns to integer multiples of 9·64·t; t = 2 gives 1152×2048."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["width"][0]["step"] = 64
        definition["bindings"]["height"][0]["step"] = 64

        built = _build(definition, aspect_ratio="9:16", resolution="1080p")

        assert (built.width, built.height) == (1152, 2048)

    def test_a_tiny_short_edge_still_yields_at_least_one_step(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["width"][0]["step"] = 64
        definition["bindings"]["height"][0]["step"] = 64

        built = _build(definition, aspect_ratio="9:16", resolution="8")

        assert (built.width, built.height) == (576, 1024)

    def test_an_unbound_size_keeps_the_literal_value(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["width"] = []
        del definition["bindings"]["height"]

        built = _build(definition, resolution="1080p")

        assert (built.width, built.height) == (None, None)
        assert _inputs(built, "5")["width"] == 832
        assert _inputs(built, "5")["height"] == 480

    @pytest.mark.parametrize("dropped", ["width", "height"])
    def test_binding_only_one_side_leaves_both_literals_alone(self, dropped: str):
        """With one writable dimension and the other fixed, the derived ratio matches neither intent; treat the entire dimension as fixed and change no bytes."""
        definition = comfyui_endpoint_definition()
        del definition["bindings"][dropped]

        built = _build(definition, aspect_ratio="9:16", resolution="1080p")

        assert (built.width, built.height) == (None, None)
        assert _inputs(built, "5")["width"] == 832
        assert _inputs(built, "5")["height"] == 480

    def test_a_non_integer_literal_falls_back_to_the_shared_default_short_edge(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["width"] = "832"
        definition["workflow"]["5"]["inputs"]["height"] = "480"

        built = _build(definition, aspect_ratio="9:16", resolution=None)

        assert (built.width, built.height) == (720, 1280)

    def test_an_image_endpoint_reads_the_image_tier_table(self):
        definition = comfyui_endpoint_definition(media_type="image")

        built = _build(definition, aspect_ratio="1:1", resolution="1K")

        assert (built.width, built.height) == (1024, 1024)


class TestFrames:
    @staticmethod
    def _with_frames(step: int, *, fps: int | None = None, entry_fps: float | None = None) -> dict[str, Any]:
        definition = comfyui_endpoint_definition()
        entry: dict[str, Any] = {"node": "9", "input": "length", "class_type": "SaveVideo", "step": step}
        if entry_fps is not None:
            entry["fps"] = entry_fps
        definition["bindings"]["frames"] = [entry]
        definition["workflow"]["9"]["inputs"]["length"] = 1
        if fps is None:
            del definition["bindings"]["fps"]
        else:
            definition["workflow"]["9"]["inputs"]["fps"] = fps
        return definition

    def test_four_n_plus_one_alignment(self):
        """16 fps × 5 seconds = 81 frames, already 4n+1."""
        built = _build(self._with_frames(4, fps=16), duration_seconds=5)

        assert built.frames == 81
        assert _inputs(built, "9")["length"] == 81

    def test_eight_n_plus_one_alignment(self):
        """24 fps × 4 seconds + 1 = 97, already 8n+1."""
        built = _build(self._with_frames(8, fps=24), duration_seconds=4)

        assert built.frames == 97
        assert (built.frames - 1) % 8 == 0

    def test_a_non_conforming_count_is_aligned_down(self):
        built = _build(self._with_frames(4, fps=25), duration_seconds=5)

        assert built.frames == 125
        assert (built.frames - 1) % 4 == 0

    def test_the_read_only_fps_binding_wins_over_the_hand_written_constant(self):
        built = _build(self._with_frames(4, fps=16, entry_fps=8), duration_seconds=5)

        assert built.frames == 81

    def test_the_hand_written_constant_is_used_when_no_fps_is_bound(self):
        built = _build(self._with_frames(4, entry_fps=8), duration_seconds=5)

        assert built.frames == 41

    def test_frames_stay_literal_when_no_frame_rate_is_known_at_all(self):
        built = _build(self._with_frames(4), duration_seconds=5)

        assert built.frames is None
        assert _inputs(built, "9")["length"] == 1

    def test_a_duration_below_one_step_is_floored_at_one_plus_step(self):
        built = _build(self._with_frames(8, fps=16), duration_seconds=0.1)

        assert built.frames == 9

    def test_a_workflow_that_keeps_its_own_length_is_not_rewritten(self):
        """81 frames @ 24fps yields no whole-second tier, so the endpoint declares duration not driven by ArcReel; filling must give the same answer."""
        definition = self._with_frames(1, fps=24)
        definition["workflow"]["9"]["inputs"]["length"] = 81

        built = _build(definition, duration_seconds=4)

        assert built.frames is None
        assert _inputs(built, "9")["length"] == 81

    def test_a_length_that_does_map_to_a_tier_is_still_driven(self):
        definition = self._with_frames(4, fps=16)
        definition["workflow"]["9"]["inputs"]["length"] = 81

        built = _build(definition, duration_seconds=3)

        assert built.frames == 49

    def test_an_unbound_frames_key_writes_nothing(self):
        definition = comfyui_endpoint_definition()

        built = _build(definition, duration_seconds=5)

        assert built.frames is None


class TestSeed:
    def test_the_random_policy_rolls_inside_the_thirty_two_bit_range(self):
        built = _build(comfyui_endpoint_definition(), rng=Random(1234))

        assert built.seed is not None
        assert 0 <= built.seed < SEED_UPPER_BOUND
        assert _inputs(built, "3")["seed"] == built.seed
        assert _inputs(built, "3")["seed"] != 123456

    def test_every_random_target_gets_the_same_seed(self):
        """Two samplers in one workflow share a seed; otherwise one generation cannot be reproduced."""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["10"] = {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["4", 0]}}
        definition["bindings"]["seed"].append({"node": "10", "input": "seed", "class_type": "KSampler"})

        built = _build(definition)

        assert _inputs(built, "3")["seed"] == _inputs(built, "10")["seed"] == built.seed

    def test_the_keep_policy_reports_the_literal_and_writes_nothing(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["seed"][0]["policy"] = "keep"

        built = _build(definition)

        assert built.seed == 123456
        assert _inputs(built, "3")["seed"] == 123456

    def test_a_requested_seed_wins_over_the_roll(self):
        built = _build(comfyui_endpoint_definition(), seed=42)

        assert built.seed == 42
        assert _inputs(built, "3")["seed"] == 42

    def test_a_requested_seed_does_not_override_an_explicit_keep(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["seed"][0]["policy"] = "keep"

        built = _build(definition, seed=42)

        assert built.seed == 123456
        assert _inputs(built, "3")["seed"] == 123456

    def test_an_unbound_seed_reports_none(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["seed"] = []

        built = _build(definition)

        assert built.seed is None
        assert _inputs(built, "3")["seed"] == 123456


class TestAvoidSplit:
    def test_every_avoid_line_is_lifted_out_of_the_body(self):
        built = _build(
            comfyui_endpoint_definition(),
            prompt="一只猫\n\nAvoid: BGM、文字字幕、水印、Logo\n\n镜头推近\n\nAvoid: 模糊、畸变",
        )

        assert _inputs(built, "6")["text"] == "一只猫\n\n镜头推近"
        assert built.negative_prompt == "BGM、文字字幕、水印、Logo，模糊、畸变"

    def test_the_avoid_text_is_appended_after_a_non_empty_literal(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["7"]["inputs"]["text"] = "low quality"

        built = _build(definition, prompt="一只猫\nAvoid: 水印")

        assert _inputs(built, "7")["text"] == "low quality，水印"

    def test_an_empty_literal_takes_the_avoid_text_as_is(self):
        built = _build(comfyui_endpoint_definition(), prompt="一只猫\nAvoid: 水印")

        assert _inputs(built, "7")["text"] == "水印"

    def test_an_unbound_negative_prompt_only_strips_the_lines(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["negative_prompt"] = []

        built = _build(definition, prompt="一只猫\n\nAvoid: 水印")

        assert _inputs(built, "6")["text"] == "一只猫"
        assert _inputs(built, "7")["text"] == ""
        assert built.negative_prompt == "水印"

    def test_the_legacy_avoid_line_without_a_logo_is_still_lifted(self):
        built = _build(comfyui_endpoint_definition(), prompt="一只猫\n\nAvoid: BGM、文字字幕、水印")

        assert _inputs(built, "6")["text"] == "一只猫"
        assert _inputs(built, "7")["text"] == "BGM、文字字幕、水印"


class TestMediaValues:
    def test_a_start_frame_is_written_to_its_read_node(self):
        definition = _i2v_definition()

        built = _build(definition, media=MediaInputs(start_image="sub/first.png"))

        assert _inputs(built, "20")["image"] == "sub/first.png"

    def test_reference_images_fill_their_slots_in_order(self):
        definition = _reference_definition()

        built = _build(definition, media=MediaInputs(reference_images=("a.png", "b.png")))

        assert _inputs(built, "20")["image"] == "a.png"
        assert _inputs(built, "21")["image"] == "b.png"
        assert built.dropped_nodes == ()


class TestImageDrop:
    def test_an_optional_entry_loses_its_key(self):
        """No last-frame value: delete its loader; video-node ``end_image`` is optional, so remove that key."""
        definition = _i2v_definition()

        built = _build(definition, media=MediaInputs(start_image="first.png"))

        assert "21" not in built.workflow
        assert "end_image" not in _inputs(built, "30")
        assert _inputs(built, "30")["start_image"] == ["20", 0]
        assert "21" in built.dropped_nodes

    def test_surplus_autogrow_reference_slots_lose_their_keys(self):
        """Hailuo H3 references fewer than slots: remove surplus ``ref_images.ref_image_N`` keys while retaining the node."""
        definition = _autogrow_reference_definition(slots=3)

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert _inputs(built, "50")["ref_images.ref_image_0"] == ["40", 0]
        assert "ref_images.ref_image_1" not in _inputs(built, "50")
        assert "ref_images.ref_image_2" not in _inputs(built, "50")
        assert "41" not in built.workflow
        assert "42" not in built.workflow
        assert built.workflow["50"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert _dangling_links(built.workflow) == []

    def test_a_pairwise_merge_node_is_bypassed_and_its_consumer_rewired(self):
        definition = _reference_definition()

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert "21" not in built.workflow
        assert "22" not in built.workflow
        assert _inputs(built, "30")["reference_image"] == ["20", 0]

    def test_a_merge_whose_two_inputs_share_one_loader_is_not_bypassed(self):
        """Both merge inputs connect to the same image loader; the remaining branch points to the node just deleted.

        Redirecting to it leaves a dangling downstream link, yet submits the workflow normally, delaying failure until remote execution. If removal is impossible,
        delete the merge node too and continue cascading; here that reaches the output node, meaning the graph cannot be modified.
        """
        definition = _reference_definition()
        definition["workflow"]["22"]["inputs"]["image2"] = ["20", 0]
        definition["bindings"]["reference_images"] = definition["bindings"]["reference_images"][:1]
        # The merge consumer precedes it: when deletion scans downstream, the merge remains; the redirected dead link gets no second
        # chance for incidental cleanup. Reverse ordering hides the same bug in a later hop, so pin this order.
        nodes = definition["workflow"]
        definition["workflow"] = {"30": nodes.pop("30"), **nodes}

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert _dangling_links(built.workflow) == []
        assert "22" not in built.workflow, "摘不掉就该连它一起删，而不是照一条死链改接下游"
        assert "reference_image" not in _inputs(built, "30")

    def test_a_consumer_outside_the_two_tables_repeats_the_last_image_instead(self):
        """A slot connects to neither an optional input nor a registered merge node: graph changes are impossible, so repeat the last image.

        Inference used this same criterion when saving bindings to warn that reducing image count repeats the last image; both sides agree.
        """
        definition = _masher_definition()

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert _inputs(built, "20")["image"] == "a.png"
        assert _inputs(built, "21")["image"] == "a.png"
        assert "22" in built.workflow
        assert built.dropped_nodes == ()

    def test_the_cascade_continues_past_a_node_type_outside_the_two_tables(self):
        """An unknown type after the second cascade hop counts as another node: delete and continue until the next optional input."""
        definition = _reference_definition()
        definition["workflow"]["23"] = {"class_type": "SomeCustomImageMasher", "inputs": {"images": ["22", 0]}}
        definition["workflow"]["30"]["inputs"]["reference_image"] = ["23", 0]

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert "22" not in built.workflow
        assert "23" not in built.workflow
        assert "30" in built.workflow
        assert "reference_image" not in _inputs(built, "30")

    def test_reaching_the_output_node_is_refused(self):
        """The whole final-video chain cannot tolerate the missing image; deletion cascades to the output node and this generation must fail."""
        definition = _reference_definition()
        definition["workflow"]["30"]["class_type"] = "SomeCustomVideoNode"

        with pytest.raises(ComfyuiError) as caught:
            _build(definition, media=MediaInputs(reference_images=()))

        assert caught.value.code == IMAGE_DROP_UNSUPPORTED
        assert caught.value.params == {"node": "9"}

    def test_upstream_that_only_fed_a_dropped_node_is_removed_too(self):
        definition = _i2v_definition()
        definition["workflow"]["19"] = {"class_type": "ImageScale", "inputs": {"image": ["18", 0]}}
        definition["workflow"]["18"] = {"class_type": "LoadImageOutput", "inputs": {"image": "raw.png"}}
        definition["workflow"]["21"]["inputs"]["image"] = ["19", 0]
        definition["bindings"]["end_image"] = [
            {"node": "21", "input": "image", "class_type": "ImageOnlyCheckpointLoader"}
        ]

        built = _build(definition, media=MediaInputs(start_image="first.png"))

        assert "21" not in built.workflow
        assert "19" not in built.workflow
        assert "18" not in built.workflow

    def test_a_terminal_node_outside_the_dropped_chain_survives(self):
        """Backward cleanup visits only ancestors of deleted nodes; do not remove preview nodes that already had no consumers."""
        definition = _i2v_definition()
        definition["workflow"]["40"] = {"class_type": "PreviewImage", "inputs": {"images": ["20", 0]}}

        built = _build(definition, media=MediaInputs(start_image="first.png"))

        assert "40" in built.workflow

    def test_no_reference_image_at_all_drops_every_slot(self):
        definition = _reference_definition()

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert "20" not in built.workflow
        assert "21" not in built.workflow
        assert "22" not in built.workflow
        assert "reference_image" not in _inputs(built, "30")

    def test_a_slot_with_no_consumer_recorded_repeats_the_last_image_instead(self):
        definition = _reference_definition()
        del definition["bindings"]["reference_images"][1]["consumer"]

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert _inputs(built, "20")["image"] == "a.png"
        assert _inputs(built, "21")["image"] == "a.png"
        assert "22" in built.workflow
        assert built.dropped_nodes == ()

    def test_a_slot_with_no_consumer_recorded_and_no_image_at_all_keeps_the_literals(self):
        definition = _reference_definition()
        del definition["bindings"]["reference_images"][0]["consumer"]
        del definition["bindings"]["reference_images"][1]["consumer"]

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert _inputs(built, "20")["image"] == "ref_a.png"
        assert _inputs(built, "21")["image"] == "ref_b.png"

    def test_a_merge_node_whose_survivor_is_a_literal_cascades_instead(self):
        definition = _reference_definition()
        definition["workflow"]["22"]["inputs"]["image1"] = "baked.png"

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert "22" not in built.workflow
        assert "reference_image" not in _inputs(built, "30")

    def test_a_definition_without_any_image_binding_leaves_the_graph_alone(self):
        """A text-to-video endpoint has no image loaders to delete; no graph modification should occur."""
        definition = comfyui_endpoint_definition()

        built = _build(definition, media=MediaInputs())

        assert built.dropped_nodes == ()
        assert set(built.workflow) == set(definition["workflow"])


class TestFailureCodeRegistration:
    def test_the_drop_failure_renders_in_every_locale(self):
        assert IMAGE_DROP_UNSUPPORTED in FAILURE_CODE_KEYS
        reason = encode_failure(IMAGE_DROP_UNSUPPORTED, node="30")

        for locale in ("zh", "en", "vi"):
            rendered = render_failure(reason, make_translator(locale), make_display_names(locale))
            assert "30" in rendered
            assert IMAGE_DROP_UNSUPPORTED not in rendered


def _i2v_definition() -> dict[str, Any]:
    """First/last frames each have one image loader, both connected to optional video-node inputs."""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "first.png"}}
    definition["workflow"]["21"] = {"class_type": "LoadImage", "inputs": {"image": "last.png"}}
    definition["workflow"]["30"] = {
        "class_type": "WanFirstLastFrameToVideo",
        "inputs": {"positive": ["6", 0], "start_image": ["20", 0], "end_image": ["21", 0]},
    }
    definition["workflow"]["3"]["inputs"]["latent_image"] = ["30", 0]
    definition["bindings"]["start_image"] = [{"node": "20", "input": "image", "class_type": "LoadImage"}]
    definition["bindings"]["end_image"] = [{"node": "21", "input": "image", "class_type": "LoadImage"}]
    return definition


def _dangling_links(workflow: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Links referencing nodes no longer in the graph: ``(消费方, 入口名, 上游)``."""
    return [
        (node_id, name, raw[0])
        for node_id, node in workflow.items()
        for name, raw in (node.get("inputs") or {}).items()
        if isinstance(raw, list) and len(raw) == 2 and isinstance(raw[0], str) and raw[0] not in workflow
    ]


def _reference_definition() -> dict[str, Any]:
    """Two reference-image slots merge through ``ImageBatch`` before entering an optional video-node input."""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "ref_a.png"}}
    definition["workflow"]["21"] = {"class_type": "LoadImage", "inputs": {"image": "ref_b.png"}}
    definition["workflow"]["22"] = {"class_type": "ImageBatch", "inputs": {"image1": ["20", 0], "image2": ["21", 0]}}
    definition["workflow"]["30"] = {
        "class_type": "WanVaceToVideo",
        "inputs": {"positive": ["6", 0], "reference_image": ["22", 0]},
    }
    definition["workflow"]["3"]["inputs"]["latent_image"] = ["30", 0]
    definition["bindings"]["reference_images"] = [
        {
            "node": "20",
            "input": "image",
            "class_type": "LoadImage",
            "consumer": {"node": "22", "input": "image1", "class_type": "ImageBatch"},
        },
        {
            "node": "21",
            "input": "image",
            "class_type": "LoadImage",
            "consumer": {"node": "22", "input": "image2", "class_type": "ImageBatch"},
        },
    ]
    return definition


def _autogrow_reference_definition(*, slots: int) -> dict[str, Any]:
    """Several reference-image slots connect directly to Hailuo H3 reference-to-video autogrow input ``ref_images.ref_image_N``."""
    definition = comfyui_endpoint_definition()
    h3_inputs: dict[str, Any] = {"prompt": "一只猫"}
    targets = []
    for slot in range(slots):
        loader = str(40 + slot)
        definition["workflow"][loader] = {"class_type": "LoadImage", "inputs": {"image": f"ref_{slot}.png"}}
        h3_inputs[f"ref_images.ref_image_{slot}"] = [loader, 0]
        targets.append(
            {
                "node": loader,
                "input": "image",
                "class_type": "LoadImage",
                "consumer": {
                    "node": "50",
                    "input": f"ref_images.ref_image_{slot}",
                    "class_type": "MiniMaxH3ReferenceToVideo",
                },
            }
        )
    definition["workflow"]["50"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": h3_inputs}
    definition["workflow"]["3"]["inputs"]["latent_image"] = ["50", 0]
    definition["bindings"]["reference_images"] = targets
    return definition


def _masher_definition() -> dict[str, Any]:
    """Two reference-image slots feed an unknown custom node; inference recognizes references, but graph modification is impossible."""
    definition = _reference_definition()
    definition["workflow"]["22"]["class_type"] = "SomeCustomImageMasher"
    for target in definition["bindings"]["reference_images"]:
        target["consumer"]["class_type"] = "SomeCustomImageMasher"
    return definition
