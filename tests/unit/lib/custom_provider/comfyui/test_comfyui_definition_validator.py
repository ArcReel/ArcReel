"""ComfyUI 定义的校验：绑定齐备且落在真实的字面值字段上，凭证只从 auth 节写入。"""

from __future__ import annotations

import pytest

from lib.custom_provider.comfyui.validator import REMOVED_FIELD_REASONS
from lib.custom_provider.endpoint_definition import DefinitionDiagnostics, validate_definition
from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from tests.factories import comfyui_endpoint_definition, make_translator


class TestAcceptedDefinitions:
    def test_a_fully_bound_definition_passes(self):
        assert validate_definition(comfyui_endpoint_definition()).valid

    def test_an_empty_list_is_an_explicit_no_support(self):
        """空列表是「这份 workflow 不支持该维度」，与「从未推断」同样合法，只有必绑两项例外。"""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["negative_prompt"] = []

        assert validate_definition(definition).valid

    def test_a_binding_may_target_a_value_wrapped_field(self):
        """``{"__value__": [...]}`` 是作者声明的字面数组，不是连线，绑得上去。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["6"]["inputs"]["text"] = {"__value__": ["一只猫", "在屋顶"]}

        assert validate_definition(definition).valid

    def test_auth_may_be_absent_because_comfyui_itself_has_no_credentials(self):
        definition = comfyui_endpoint_definition()

        assert "auth" not in definition
        assert validate_definition(definition).valid

    def test_an_empty_auth_section_is_accepted(self):
        assert validate_definition(comfyui_endpoint_definition(auth={})).valid

    def test_an_auth_header_writing_the_api_key_is_accepted(self):
        definition = comfyui_endpoint_definition(auth={"headers": {"X-Proxy-Token": "{{ api_key }}"}})

        assert validate_definition(definition).valid

    def test_an_image_endpoint_keeps_the_seven_keys_it_is_allowed(self):
        assert validate_definition(_image_endpoint()).valid


class TestRequiredBindings:
    @pytest.mark.parametrize("key", ["prompt", "output"])
    @pytest.mark.parametrize("state", ["empty", "absent"])
    def test_prompt_and_output_must_be_bound(self, key: str, state: str):
        definition = comfyui_endpoint_definition()
        if state == "empty":
            definition["bindings"][key] = []
        else:
            del definition["bindings"][key]

        assert (f"bindings.{key}", "comfyui_binding_required") in _codes(validate_definition(definition))


class TestMediaTypeWhitelist:
    @pytest.mark.parametrize(
        ("key", "target"),
        [
            ("start_image", {"node": "4", "input": "ckpt_name", "class_type": "CheckpointLoaderSimple"}),
            ("end_image", {"node": "4", "input": "ckpt_name", "class_type": "CheckpointLoaderSimple"}),
            ("frames", {"node": "5", "input": "batch_size", "class_type": "EmptyLatentImage"}),
            ("fps", {"node": "9", "input": "fps", "class_type": "SaveVideo", "direction": "read"}),
        ],
    )
    def test_an_image_endpoint_refuses_the_time_axis_and_frame_keys(self, key: str, target: dict[str, object]):
        definition = _image_endpoint(**{key: [target]})

        assert (f"bindings.{key}", "comfyui_binding_key_not_allowed") in _codes(validate_definition(definition))

    def test_an_out_of_scope_key_does_not_also_report_its_targets(self):
        """越界键只报一条：再报一串定位到节点的次生错误只会淹没「图像端点没有这一项」。"""
        definition = _image_endpoint(
            frames=[{"node": "缺席的节点", "input": "batch_size", "class_type": "EmptyLatentImage"}]
        )

        assert _codes(validate_definition(definition)) == [("bindings.frames", "comfyui_binding_key_not_allowed")]


class TestTargetsAgainstTheWorkflow:
    def test_a_target_node_must_exist_in_the_workflow(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"][0]["node"] = "404"

        assert ("bindings.prompt[0].node", "comfyui_node_not_found") in _codes(validate_definition(definition))

    def test_a_target_input_must_exist_on_that_node(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"][0]["input"] = "prompt"

        assert ("bindings.prompt[0].input", "comfyui_input_not_found") in _codes(validate_definition(definition))

    def test_a_target_input_wired_from_upstream_is_refused(self):
        """连线字段的值运行时由上游产生，往那里填值只会被覆盖。"""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"][0]["input"] = "clip"

        assert ("bindings.prompt[0].input", "comfyui_input_is_link") in _codes(validate_definition(definition))

    def test_an_output_target_only_needs_its_node_to_exist(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["output"][0]["node"] = "404"

        assert _codes(validate_definition(definition)) == [("bindings.output[0].node", "comfyui_node_not_found")]

    def test_a_value_wrapped_two_element_array_is_not_taken_for_a_link(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["3"]["inputs"]["model"] = {"__value__": ["4", 0]}
        definition["bindings"]["seed"][0]["input"] = "model"

        assert validate_definition(definition).valid


class TestAuthScope:
    def test_the_api_key_placeholder_is_refused_outside_auth(self):
        """workflow 是原样内嵌的底稿，里面写占位符既不生效，又会随导出文件把凭证分发出去。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["6"]["inputs"]["text"] = "{{ api_key }}"

        assert ("workflow.6.inputs.text", "api_key_outside_auth") in _codes(validate_definition(definition))

    def test_a_non_empty_auth_section_must_reference_the_api_key(self):
        definition = comfyui_endpoint_definition(auth={"headers": {"X-Proxy-Token": "写死的密钥"}})

        assert ("auth", "auth_without_api_key") in _codes(validate_definition(definition))

    def test_auth_knows_no_variable_other_than_the_api_key(self):
        definition = comfyui_endpoint_definition(auth={"query": {"token": "{{ api_key }}", "url": "{{ base_url }}"}})

        assert ("auth.query.url", "undeclared_variable") in _codes(validate_definition(definition))


class TestDiagnosticPayload:
    def test_a_capabilities_section_is_reported_as_removed_with_its_reason(self):
        definition = comfyui_endpoint_definition(capabilities={"first_frame": True})

        payload = validate_definition(definition).to_payload(make_translator("zh"))

        assert payload["errors"][0]["code"] == "removed_field"
        assert "节点绑定" in payload["errors"][0]["message"]

    def test_every_removed_reason_reads_as_prose_in_every_locale(self):
        for key in sorted(REMOVED_FIELD_REASONS.values()):
            for locale in SUPPORTED_LOCALES:
                assert key in MESSAGES[locale], f"{key} 缺 {locale} 文案"

    def test_a_structural_error_stops_the_semantic_layer(self):
        """结构不成立时绑定与 workflow 的交叉检查无从谈起，继续跑只会产出误导性的次生错误。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"] = []
        definition["bindings"]["prompt"] = []

        assert _codes(validate_definition(definition)) == [("workflow", "invalid_type")]


def _image_endpoint(**bindings: object) -> dict[str, object]:
    """一份图像端点定义：视频专属的语义键先摘干净，再按用例补上要试的那个键。"""
    definition = comfyui_endpoint_definition(media_type="image")
    for key in ("start_image", "end_image", "frames", "fps"):
        definition["bindings"].pop(key, None)
    definition["bindings"].update(bindings)
    return definition


def _codes(diagnostics: DefinitionDiagnostics) -> list[tuple[str, str]]:
    return [(issue.path, issue.code.value) for issue in diagnostics.errors]
