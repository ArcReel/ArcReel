"""API 格式 workflow 的图原语。"""

from lib.custom_provider.comfyui.graph import (
    ancestors,
    class_type_of,
    consumers_by_node,
    dependency_depth,
    link_of,
    node_title,
    resolve_literal,
    unwrap_value,
)

CONSTANTS = {"PrimitiveInt": "value"}


def chain() -> dict:
    return {
        "1": {"class_type": "PrimitiveInt", "inputs": {"value": 81}, "_meta": {"title": "Duration"}},
        "2": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": 832, "length": ["1", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"latent_image": ["2", 0], "seed": 7}},
        "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0]}},
    }


def test_a_length_two_array_is_a_link_and_a_wrapped_array_is_not():
    assert link_of(["3", 1]) == ("3", 1)
    assert link_of({"__value__": ["a", "b"]}) is None
    assert unwrap_value({"__value__": ["a", "b"]}) == ["a", "b"]
    assert unwrap_value(16) == 16


def test_titles_and_class_types_tolerate_nodes_without_meta():
    assert node_title({"class_type": "SaveVideo"}) == ""
    assert node_title({"_meta": {"title": "存视频"}}) == "存视频"
    assert class_type_of({"inputs": {}}) == ""


def test_reverse_edges_name_the_consumer_input_and_the_upstream_slot():
    consumers = consumers_by_node(
        {"1": {"class_type": "A", "inputs": {}}, "2": {"class_type": "B", "inputs": {"x": ["1", 3]}}}
    )
    assert [(c.node, c.input, c.class_type, c.slot) for c in consumers["1"]] == [("2", "x", "B", 3)]


def test_dependency_depth_counts_the_longest_upstream_chain():
    workflow = chain()
    assert dependency_depth(workflow, "1") == 0
    assert dependency_depth(workflow, "4") == 3


def test_dependency_depth_stops_on_a_cycle_instead_of_recursing():
    """合法 workflow 是 DAG，但导入的是用户文件：遇到环要收敛到有限值而不是撞进无限递归。"""
    cyclic = {"1": {"class_type": "A", "inputs": {"x": ["2", 0]}}, "2": {"class_type": "B", "inputs": {"y": ["1", 0]}}}
    assert dependency_depth(cyclic, "1") <= len(cyclic)


def test_ancestors_exclude_the_node_itself():
    assert ancestors(chain(), "4") == frozenset({"1", "2", "3"})
    assert ancestors(chain(), "1") == frozenset()


def test_a_literal_field_resolves_to_itself():
    landing = resolve_literal(chain(), "2", "width", constants=CONSTANTS)
    assert (landing.node, landing.input, landing.value) == ("2", "width", 832)


def test_a_wired_field_resolves_up_to_the_constant_node():
    landing = resolve_literal(chain(), "2", "length", constants=CONSTANTS)
    assert (landing.node, landing.input, landing.value) == ("1", "value", 81)


def test_a_wired_field_has_no_landing_once_the_chain_reaches_a_computing_node():
    workflow = chain()
    workflow["1"] = {"class_type": "ComfyMathExpression", "inputs": {"expression": "a * 16 + 1"}}
    assert resolve_literal(workflow, "2", "length", constants=CONSTANTS).found is False


def test_an_absent_node_or_input_has_no_landing():
    assert resolve_literal(chain(), "99", "width", constants=CONSTANTS).found is False
    assert resolve_literal(chain(), "2", "height", constants=CONSTANTS).found is False
