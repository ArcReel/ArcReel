"""节点绑定名录上的读取原语：``fps`` 只读绑定各自读出的字面值。"""

from __future__ import annotations

from definition_factories import comfyui_endpoint_definition

from arcreel_market_core.comfyui.bindings import fps_literals


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
