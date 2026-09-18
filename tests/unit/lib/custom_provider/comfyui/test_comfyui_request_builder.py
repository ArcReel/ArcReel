"""实发 workflow 的构造：填值、尺寸与帧数换算、种子、`Avoid` 拆分与按张数改图。"""

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
from lib.task_failure import FAILURE_CODE_KEYS, encode_failure, render_failure
from tests.factories import comfyui_endpoint_definition, make_translator


def _build(definition: dict[str, Any], **kwargs: Any):
    """构造一次实发 workflow，入参给出各维度都用得上的缺省值。"""
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
        """底稿的 832×480 就是作者调好的那一档，短边取 480、比例仍按项目走（720p 则得 720）。"""
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
        """两侧步长 64：短边 1080 落在 9·64·t 的整数倍上，取 t = 2 得 1152×2048。"""
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
        """写得动一侧、另一侧固定时派生出的比例两头不靠，故整维当作固定、一个字节都不改。"""
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
        del definition["bindings"]["fps"]

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
        """16 fps × 5 秒 = 81 帧，已是 4n+1。"""
        built = _build(self._with_frames(4, fps=16), duration_seconds=5)

        assert built.frames == 81
        assert _inputs(built, "9")["length"] == 81

    def test_eight_n_plus_one_alignment(self):
        """24 fps × 4 秒 + 1 = 97，已是 8n+1。"""
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
        """81 帧 @ 24fps 凑不出整档，端点因此对外说时长不由 ArcReel 驱动；填值层要给同一个答案。"""
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
        """一份 workflow 里的两个采样器共用一个种子，否则同一次生成不可复现。"""
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
        """尾帧未给值：读图节点删掉，视频节点的 ``end_image`` 是可选入口，摘键即可。"""
        definition = _i2v_definition()

        built = _build(definition, media=MediaInputs(start_image="first.png"))

        assert "21" not in built.workflow
        assert "end_image" not in _inputs(built, "30")
        assert _inputs(built, "30")["start_image"] == ["20", 0]
        assert "21" in built.dropped_nodes

    def test_a_pairwise_merge_node_is_bypassed_and_its_consumer_rewired(self):
        definition = _reference_definition()

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert "21" not in built.workflow
        assert "22" not in built.workflow
        assert _inputs(built, "30")["reference_image"] == ["20", 0]

    def test_a_merge_whose_two_inputs_share_one_loader_is_not_bypassed(self):
        """合并节点两个入口接的是同一个读图节点：剩下那一路指的正是刚删掉的节点。

        照它改接，下游会拿到一条悬空连线，而 workflow 仍会照常提交出去，远端才报错。摘不掉就
        只能把合并节点也删掉并继续级联——这张图上级联一路走到产物节点，那正是「改不动」。
        """
        definition = _reference_definition()
        definition["workflow"]["22"]["inputs"]["image2"] = ["20", 0]
        definition["bindings"]["reference_images"] = definition["bindings"]["reference_images"][:1]
        # 合并节点的下游排在它前面：删节点那一遍扫到下游时合并还没摘，改接留下的死链没有第二次
        # 机会被顺手清掉。反过来排的图上同一个缺陷会被后面那一跳掩盖，故这里固定这个次序。
        nodes = definition["workflow"]
        definition["workflow"] = {"30": nodes.pop("30"), **nodes}

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert _dangling_links(built.workflow) == []
        assert "22" not in built.workflow, "摘不掉就该连它一起删，而不是照一条死链改接下游"
        assert "reference_image" not in _inputs(built, "30")

    def test_a_consumer_outside_the_two_tables_repeats_the_last_image_instead(self):
        """格子接进的入口不在可选入口表也不在合并节点表：改不动图，退回重复最后一张。

        推断在保存这份绑定时就按同一条判据提示过「减少张数会重复填充最后一张」，两侧同口径。
        """
        definition = _masher_definition()

        built = _build(definition, media=MediaInputs(reference_images=("a.png",)))

        assert _inputs(built, "20")["image"] == "a.png"
        assert _inputs(built, "21")["image"] == "a.png"
        assert "22" in built.workflow
        assert built.dropped_nodes == ()

    def test_the_cascade_continues_past_a_node_type_outside_the_two_tables(self):
        """级联第二跳往后撞上名录外的类型：按「其他节点」删掉并继续，到下一个可选入口才停。"""
        definition = _reference_definition()
        definition["workflow"]["23"] = {"class_type": "SomeCustomImageMasher", "inputs": {"images": ["22", 0]}}
        definition["workflow"]["30"]["inputs"]["reference_image"] = ["23", 0]

        built = _build(definition, media=MediaInputs(reference_images=()))

        assert "22" not in built.workflow
        assert "23" not in built.workflow
        assert "30" in built.workflow
        assert "reference_image" not in _inputs(built, "30")

    def test_reaching_the_output_node_is_refused(self):
        """整条成片链路都不认得这张图缺席，级联一路走到产物节点——这次生成只能失败。"""
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
        """后向清理只走被删节点的上游，图里本就没有下游的预览节点不该被扫掉。"""
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
        """文生视频端点没有读图节点可删，一次改图也不该发生。"""
        definition = comfyui_endpoint_definition()

        built = _build(definition, media=MediaInputs())

        assert built.dropped_nodes == ()
        assert set(built.workflow) == set(definition["workflow"])


class TestFailureCodeRegistration:
    def test_the_drop_failure_renders_in_every_locale(self):
        assert IMAGE_DROP_UNSUPPORTED in FAILURE_CODE_KEYS
        reason = encode_failure(IMAGE_DROP_UNSUPPORTED, node="30")

        for locale in ("zh", "en", "vi"):
            rendered = render_failure(reason, make_translator(locale))
            assert "30" in rendered
            assert IMAGE_DROP_UNSUPPORTED not in rendered


def _i2v_definition() -> dict[str, Any]:
    """首尾帧各一个读图节点，都落在视频节点的可选入口上。"""
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
    """指向图里已经不存在的节点的连线：``(消费方, 入口名, 上游)``。"""
    return [
        (node_id, name, raw[0])
        for node_id, node in workflow.items()
        for name, raw in (node.get("inputs") or {}).items()
        if isinstance(raw, list) and len(raw) == 2 and isinstance(raw[0], str) and raw[0] not in workflow
    ]


def _reference_definition() -> dict[str, Any]:
    """两个参考图格子经 ``ImageBatch`` 汇成一路，再进视频节点的可选入口。"""
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


def _masher_definition() -> dict[str, Any]:
    """两个参考图格子接进一个名录外的自定义节点——推断认得出是参考图，却改不动图。"""
    definition = _reference_definition()
    definition["workflow"]["22"]["class_type"] = "SomeCustomImageMasher"
    for target in definition["bindings"]["reference_images"]:
        target["consumer"]["class_type"] = "SomeCustomImageMasher"
    return definition
