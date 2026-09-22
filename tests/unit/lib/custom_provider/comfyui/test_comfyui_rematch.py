"""重导入重匹配：已保存的节点绑定对上一份改过的 workflow。"""

from lib.custom_provider.comfyui.inference import BindingSignal, BindingState, InferenceNote, infer_bindings

POSITIVE = {"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}
NEGATIVE = {"node": "7", "input": "text", "class_type": "CLIPTextEncode", "title": "负向"}
OUTPUT = {"node": "58", "class_type": "SaveVideo", "title": "Save Video"}


def workflow() -> dict:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": 1, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0]},
            "_meta": {"title": "KSampler"},
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan.safetensors"}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "一只猫", "clip": ["4", 1]},
            "_meta": {"title": "正向"},
        },
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}, "_meta": {"title": "负向"}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "57": {"class_type": "CreateVideo", "inputs": {"fps": 16, "images": ["8", 0]}},
        "58": {
            "class_type": "SaveVideo",
            "inputs": {"filename_prefix": "video/x", "video": ["57", 0]},
            "_meta": {"title": "Save Video"},
        },
    }


def renumbered(old: str, new: str) -> dict:
    """把一个节点换个 id，并把指向它的连线一并改过去——重新导出 workflow 就是这种变化。"""
    graph = workflow()
    graph[new] = graph.pop(old)
    return rewired(graph, {old: new})


def swapped(first: str, second: str) -> dict:
    """两个节点互换编号，指向它们的连线一并改过去。"""
    graph = workflow()
    graph[first], graph[second] = graph[second], graph[first]
    return rewired(graph, {first: second, second: first})


def rewired(graph: dict, moves: dict[str, str]) -> dict:
    """把图里指向旧 id 的连线改到新 id。"""
    for node in graph.values():
        for name, raw in node["inputs"].items():
            if isinstance(raw, list) and raw[0] in moves:
                node["inputs"][name] = [moves[raw[0]], raw[1]]
    return graph


def rematch(graph: dict, bindings: dict):
    return infer_bindings({"media_type": "video", "workflow": graph, "bindings": bindings})


def origins(result, key: str) -> list[str]:
    return [candidate.origin.value for candidate in result.keys[key].candidates]


def test_an_unchanged_node_and_class_type_is_carried_over():
    result = rematch(workflow(), {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]
    assert result.keys["prompt"].selected_targets == ({**POSITIVE},)


def test_a_carried_over_entry_outscores_anything_inference_can_produce():
    """用户确认过的落点压过一切信号：重导入不会把它挤到某条自动识别结果后面。"""
    kept = rematch(workflow(), {"prompt": [POSITIVE]}).keys["prompt"].candidates[0]
    best_inferred = max(candidate.score for candidate in rematch(workflow(), {}).keys["prompt"].candidates)
    assert [hit.signal for hit in kept.signals] == [BindingSignal.MANUAL_BINDING]
    assert kept.score > best_inferred


def test_a_renumbered_node_migrates_when_its_class_type_and_title_are_unique():
    result = rematch(renumbered("6", "106"), {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["rematched"]
    assert result.keys["prompt"].selected_targets[0]["node"] == "106"
    assert InferenceNote.REMATCHED.value in {note.code.value for note in result.keys["prompt"].notes}


def test_a_node_level_output_entry_migrates_the_same_way():
    result = rematch(renumbered("58", "158"), {"output": [OUTPUT]})
    assert origins(result, "output") == ["rematched"]
    assert result.keys["output"].selected_targets[0]["node"] == "158"


def test_an_ambiguous_title_is_not_migrated_and_the_key_goes_back_to_inference():
    graph = renumbered("6", "106")
    graph["7"]["_meta"]["title"] = "正向"
    result = rematch(graph, {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION
    assert InferenceNote.BINDING_LOST.value in {note.code.value for note in result.keys["prompt"].notes}
    assert [candidate.node for candidate in result.keys["prompt"].candidates] == ["106"]


def test_a_vanished_node_drops_the_entry_and_reruns_inference_for_that_key():
    graph = workflow()
    graph["6"] = {"class_type": "T5TextEncode", "inputs": {"text": "一只猫"}, "_meta": {"title": "换了个编码器"}}
    result = rematch(graph, {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION
    assert result.keys["prompt"].selected_targets == ()
    assert ("6", "text") in [(c.node, c.input) for c in result.keys["prompt"].candidates]


def test_a_field_that_became_a_wire_drops_the_entry():
    """节点还在、字段却改接了上游：往那里填值运行时会被覆盖，不能算沿用。"""
    graph = workflow()
    graph["6"]["inputs"]["text"] = ["4", 3]
    result = rematch(graph, {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION


def test_a_surviving_sibling_is_kept_but_still_waits_for_confirmation():
    """同一个语义键上有条目丢了，整键都要在用户眼前过一遍，沿用下来的那条也不自动选。"""
    gone = {"node": "99", "input": "text", "class_type": "CLIPTextEncode", "title": "不存在了"}
    result = rematch(workflow(), {"prompt": [POSITIVE, gone]})
    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION
    survivor = next(c for c in result.keys["prompt"].candidates if c.node == "6")
    assert survivor.origin.value == "kept"
    assert [hit.signal for hit in survivor.signals] == [BindingSignal.MANUAL_BINDING]
    assert survivor.selected is False


def test_a_migrated_entry_refreshes_the_class_type_and_title_it_carries():
    graph = renumbered("6", "106")
    graph["106"]["_meta"]["title"] = "正向"
    target = rematch(graph, {"prompt": [POSITIVE]}).keys["prompt"].selected_targets[0]
    assert (target["class_type"], target["title"]) == ("CLIPTextEncode", "正向")


def test_entry_level_extras_survive_the_migration():
    """``step`` / ``policy`` 一类用户改过的值随条目走，不会被重匹配抹回默认。"""
    seed = {"node": "3", "input": "seed", "class_type": "KSampler", "title": "KSampler", "policy": "keep"}
    result = rematch(renumbered("3", "103"), {"seed": [seed]})
    assert result.keys["seed"].selected_targets[0]["policy"] == "keep"
    assert result.keys["seed"].selected_targets[0]["node"] == "103"


def test_an_entry_bound_to_an_explicit_null_literal_is_not_reported_lost():
    """``null`` 是个存在的字面值字段，填得进去；校验器那侧也按键判在不在，两边同口径。"""
    graph = workflow()
    graph["6"]["inputs"]["text"] = None

    result = rematch(graph, {"prompt": [POSITIVE]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]


def reference_workflow() -> dict:
    """两个参考图格子经 ``ImageStitch`` 汇成一路，再进视频节点的参考图入口。"""
    graph = workflow()
    graph["20"] = {"class_type": "LoadImage", "inputs": {"image": "ref_a.png"}, "_meta": {"title": "参考图 A"}}
    graph["21"] = {"class_type": "LoadImage", "inputs": {"image": "ref_b.png"}, "_meta": {"title": "参考图 B"}}
    graph["22"] = {
        "class_type": "ImageStitch",
        "inputs": {"image1": ["20", 0], "image2": ["21", 0]},
        "_meta": {"title": "Image Stitch"},
    }
    graph["30"] = {
        "class_type": "WanVaceToVideo",
        "inputs": {"positive": ["6", 0], "reference_image": ["22", 0]},
        "_meta": {"title": "VACE"},
    }
    graph["3"]["inputs"]["latent_image"] = ["30", 0]
    return graph


def reference_entry(node: str, title: str, consumer_node: str, consumer_input: str) -> dict:
    return {
        "node": node,
        "input": "image",
        "class_type": "LoadImage",
        "title": title,
        "consumer": {
            "node": consumer_node,
            "input": consumer_input,
            "class_type": "ImageStitch",
            "title": "Image Stitch",
        },
    }


def test_a_migrated_entry_re_derives_its_consumer_from_the_live_graph():
    """读图节点与它的消费者常常一起换号：``consumer`` 只由推断得出，照搬旧值会指向不存在的节点。"""
    graph = reference_workflow()
    graph["122"] = graph.pop("22")
    graph["30"]["inputs"]["reference_image"] = ["122", 0]
    saved = [reference_entry("20", "参考图 A", "22", "image1"), reference_entry("21", "参考图 B", "22", "image2")]

    targets = rematch(graph, {"reference_images": saved}).keys["reference_images"].selected_targets

    assert [target["consumer"]["node"] for target in targets] == ["122", "122"]
    assert [target["consumer"]["input"] for target in targets] == ["image1", "image2"]


def test_a_consumer_that_no_longer_exists_is_dropped_from_the_entry():
    """重推不出消费者即「这张图接到了谁没看懂」，与从未推断出 ``consumer`` 同义——留着旧值会让
    实发构造按一个不存在的落点去改图。"""
    graph = reference_workflow()
    graph["30"]["inputs"]["reference_image"] = ["8", 0]
    del graph["22"]
    saved = [reference_entry("20", "参考图 A", "22", "image1")]

    target = rematch(graph, {"reference_images": saved}).keys["reference_images"].selected_targets[0]

    assert target["node"] == "20"
    assert "consumer" not in target


def test_a_carried_over_entry_picks_up_a_consumer_it_never_had():
    """条目上没记 ``consumer`` 不是用户的选择，是上次没推出来；这次推得出就补上。"""
    entry = {"node": "20", "input": "image", "class_type": "LoadImage", "title": "参考图 A"}

    target = rematch(reference_workflow(), {"reference_images": [entry]}).keys["reference_images"].selected_targets[0]

    assert target["consumer"] == {"node": "22", "input": "image1", "class_type": "ImageStitch", "title": "Image Stitch"}


def test_two_nodes_of_one_class_that_swapped_numbers_are_not_carried_over_by_id():
    """正负两个 ``CLIPTextEncode`` 互换编号：标题对不上就不按 id 沿用，两条提示词各自迁到标题对得上的节点。"""
    result = rematch(swapped("6", "7"), {"prompt": [POSITIVE], "negative_prompt": [NEGATIVE]})

    assert origins(result, "prompt") == ["rematched"]
    assert result.keys["prompt"].selected_targets[0]["node"] == "7"
    assert result.keys["negative_prompt"].selected_targets[0]["node"] == "6"


def test_a_swapped_node_whose_title_is_nowhere_in_the_new_graph_needs_confirmation():
    """标题对不上又按标题定位不到：条目算丢，整键重跑推断并等用户确认，不静默沿用。"""
    graph = swapped("6", "7")
    del graph["6"]["_meta"]
    del graph["7"]["_meta"]

    result = rematch(graph, {"prompt": [POSITIVE]})

    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION
    assert InferenceNote.BINDING_LOST.value in {note.code.value for note in result.keys["prompt"].notes}
    assert result.keys["prompt"].selected_targets == ()


def test_an_entry_and_a_node_that_both_have_no_title_are_still_carried_over():
    """导出物里 ``_meta`` 可能整节缺失：两边都没有标题时核对不出任何东西，照旧沿用。"""
    graph = workflow()
    del graph["6"]["_meta"]
    untitled = {**POSITIVE, "title": ""}

    result = rematch(graph, {"prompt": [untitled]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]


def test_an_entry_that_never_recorded_a_title_is_still_carried_over():
    """``title`` 在定义 schema 上是可选字段：手写的定义没记它，不等于当时那个节点没有标题。"""
    entry = {"node": "6", "input": "text", "class_type": "CLIPTextEncode"}

    result = rematch(workflow(), {"prompt": [entry]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]
