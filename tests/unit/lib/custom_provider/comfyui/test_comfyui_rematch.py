"""Reimport rematching: match saved node bindings against a modified workflow."""

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
    """Change one node ID and all links pointing to it, as happens when a workflow is exported again."""
    graph = workflow()
    graph[new] = graph.pop(old)
    return rewired(graph, {old: new})


def swapped(first: str, second: str) -> dict:
    """Swap two node IDs and update their incoming links accordingly."""
    graph = workflow()
    graph[first], graph[second] = graph[second], graph[first]
    return rewired(graph, {first: second, second: first})


def rewired(graph: dict, moves: dict[str, str]) -> dict:
    """Redirect graph links from the old ID to the new ID."""
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
    """User-confirmed targets outweigh every signal; reimport must not rank them behind automatically recognized results."""
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
    """The node remains but its field now links upstream: filled values would be overwritten at runtime, so do not retain the binding."""
    graph = workflow()
    graph["6"]["inputs"]["text"] = ["4", 3]
    result = rematch(graph, {"prompt": [POSITIVE]})
    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION


def test_a_surviving_sibling_is_kept_but_still_waits_for_confirmation():
    """Losing one entry requires user review of the whole semantic key; do not automatically select even the retained entry."""
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
    """User-edited ``step`` / ``policy`` values travel with entries; rematching must not reset them to defaults."""
    seed = {"node": "3", "input": "seed", "class_type": "KSampler", "title": "KSampler", "policy": "keep"}
    result = rematch(renumbered("3", "103"), {"seed": [seed]})
    assert result.keys["seed"].selected_targets[0]["policy"] == "keep"
    assert result.keys["seed"].selected_targets[0]["node"] == "103"


def test_an_entry_bound_to_an_explicit_null_literal_is_not_reported_lost():
    """``null`` is an existing writable literal field; the validator also checks key presence, keeping both sides aligned."""
    graph = workflow()
    graph["6"]["inputs"]["text"] = None

    result = rematch(graph, {"prompt": [POSITIVE]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]


def reference_workflow() -> dict:
    """Two reference-image slots merge through ``ImageStitch`` before entering the video node's reference-image input."""
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
    """Image loaders and consumers often change IDs together; ``consumer`` is inferred only, so copying old values can reference nonexistent nodes."""
    graph = reference_workflow()
    graph["122"] = graph.pop("22")
    graph["30"]["inputs"]["reference_image"] = ["122", 0]
    saved = [reference_entry("20", "参考图 A", "22", "image1"), reference_entry("21", "参考图 B", "22", "image2")]

    targets = rematch(graph, {"reference_images": saved}).keys["reference_images"].selected_targets

    assert [target["consumer"]["node"] for target in targets] == ["122", "122"]
    assert [target["consumer"]["input"] for target in targets] == ["image1", "image2"]


def test_a_consumer_that_no_longer_exists_is_dropped_from_the_entry():
    """Failure to reinfer the consumer means the image destination is unknown, equivalent to never having inferred ``consumer``; retaining the old value
    would make request construction modify the graph using a nonexistent target."""
    graph = reference_workflow()
    graph["30"]["inputs"]["reference_image"] = ["8", 0]
    del graph["22"]
    saved = [reference_entry("20", "参考图 A", "22", "image1")]

    target = rematch(graph, {"reference_images": saved}).keys["reference_images"].selected_targets[0]

    assert target["node"] == "20"
    assert "consumer" not in target


def test_a_carried_over_entry_picks_up_a_consumer_it_never_had():
    """An absent ``consumer`` is not a user choice, but failed prior inference; populate it when this inference succeeds."""
    entry = {"node": "20", "input": "image", "class_type": "LoadImage", "title": "参考图 A"}

    target = rematch(reference_workflow(), {"reference_images": [entry]}).keys["reference_images"].selected_targets[0]

    assert target["consumer"] == {"node": "22", "input": "image1", "class_type": "ImageStitch", "title": "Image Stitch"}


def test_two_nodes_of_one_class_that_swapped_numbers_are_not_carried_over_by_id():
    """Positive/negative ``CLIPTextEncode`` nodes swap IDs: mismatched titles prevent ID reuse; migrate each prompt to the node with its matching title."""
    result = rematch(swapped("6", "7"), {"prompt": [POSITIVE], "negative_prompt": [NEGATIVE]})

    assert origins(result, "prompt") == ["rematched"]
    assert result.keys["prompt"].selected_targets[0]["node"] == "7"
    assert result.keys["negative_prompt"].selected_targets[0]["node"] == "6"


def test_a_swapped_node_whose_title_is_nowhere_in_the_new_graph_needs_confirmation():
    """If titles mismatch and title lookup fails, treat the entry as lost; rerun inference for the whole key and await confirmation instead of silently retaining it."""
    graph = swapped("6", "7")
    del graph["6"]["_meta"]
    del graph["7"]["_meta"]

    result = rematch(graph, {"prompt": [POSITIVE]})

    assert result.keys["prompt"].state is BindingState.NEEDS_CONFIRMATION
    assert InferenceNote.BINDING_LOST.value in {note.code.value for note in result.keys["prompt"].notes}
    assert result.keys["prompt"].selected_targets == ()


def test_an_entry_and_a_node_that_both_have_no_title_are_still_carried_over():
    """Exports may omit all of ``_meta``; if neither side has titles, comparison establishes nothing, so retain the binding as before."""
    graph = workflow()
    del graph["6"]["_meta"]
    untitled = {**POSITIVE, "title": ""}

    result = rematch(graph, {"prompt": [untitled]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]


def test_an_entry_that_never_recorded_a_title_is_still_carried_over():
    """``title`` is optional in definition schemas; its absence in a handwritten definition does not prove the original node lacked a title."""
    entry = {"node": "6", "input": "text", "class_type": "CLIPTextEncode"}

    result = rematch(workflow(), {"prompt": [entry]})

    assert result.keys["prompt"].state is BindingState.AUTO_SELECTED
    assert origins(result, "prompt") == ["kept"]
