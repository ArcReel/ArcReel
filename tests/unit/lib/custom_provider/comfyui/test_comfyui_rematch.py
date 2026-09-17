"""重导入重匹配：已保存的节点绑定对上一份改过的 workflow。"""

from lib.custom_provider.comfyui.inference import BindingSignal, BindingState, InferenceNote, infer_bindings

POSITIVE = {"node": "6", "input": "text", "class_type": "CLIPTextEncode", "title": "正向"}
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
    for node in graph.values():
        for name, raw in node["inputs"].items():
            if isinstance(raw, list) and raw[0] == old:
                node["inputs"][name] = [new, raw[1]]
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
