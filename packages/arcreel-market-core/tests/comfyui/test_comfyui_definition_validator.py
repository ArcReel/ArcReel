"""ComfyUI validation: complete bindings on real literal fields, with credentials entering only through auth."""

from __future__ import annotations

from typing import Any

import pytest
from definition_factories import comfyui_endpoint_definition

from arcreel_market_core.comfyui.validator import REMOVED_FIELD_REASONS
from arcreel_market_core.endpoint_definition import DefinitionDiagnostics, validate_definition
from arcreel_market_core.validation_messages import code_translator


class TestAcceptedDefinitions:
    def test_a_fully_bound_definition_passes(self):
        assert validate_definition(comfyui_endpoint_definition()).valid

    def test_an_empty_list_is_an_explicit_no_support(self):
        """Empty lists mean the workflow does not support that dimension, as valid as never inferred, except the two required bindings."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["negative_prompt"] = []

        assert validate_definition(definition).valid

    def test_a_binding_may_target_a_value_wrapped_field(self):
        """``{"__value__": [...]}`` explicitly declares an array literal, not a link, and can be bound."""
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
        """Report one out-of-scope-key error; node-level secondary errors would bury the image-endpoint-unsupported-key diagnostic."""
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

    def test_a_target_class_type_that_drifted_is_refused(self):
        """Rematching identifies by type: wrong types silently move bindings to nodes users never selected."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"][0]["class_type"] = "T5TextEncode"

        codes = _codes(validate_definition(definition))

        assert ("bindings.prompt[0].class_type", "comfyui_class_type_mismatch") in codes

    def test_a_target_input_wired_from_upstream_is_refused(self):
        """Upstream nodes produce link-field values at runtime, overwriting populated values."""
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


class TestOneFieldOneBinding:
    def test_two_semantic_keys_cannot_write_the_same_field(self):
        """Construction populates by semantic key; sharing one field gives users the positive prompt as the negative prompt."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["negative_prompt"][0]["node"] = "6"

        codes = _codes(validate_definition(definition))

        assert ("bindings.negative_prompt[0].input", "comfyui_target_collision") in codes

    def test_two_entries_of_one_key_cannot_write_the_same_field(self):
        """Two reference-image slots sharing a location let the second overwrite the first."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["prompt"].append(dict(definition["bindings"]["prompt"][0]))

        assert ("bindings.prompt[1].input", "comfyui_target_collision") in _codes(validate_definition(definition))

    def test_a_read_only_target_does_not_claim_the_field_it_reads(self):
        """Read-only targets only read, so sharing write locations is not a conflict."""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["fps"] = [
            {"node": "5", "input": "width", "class_type": "EmptyLatentImage", "direction": "read"}
        ]

        assert validate_definition(definition).valid

    def test_the_node_level_output_target_claims_no_field(self):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["output"][0] |= {"node": "5", "class_type": "EmptyLatentImage"}

        assert validate_definition(definition).valid


class TestReferenceImageConsumer:
    """``consumer`` records which node input receives this image; construction uses it to choose graph modification or repeat-filling."""

    def test_a_consumer_consistent_with_the_workflow_passes(self):
        assert validate_definition(_reference_endpoint()).valid

    def test_a_consumer_node_must_exist_in_the_workflow(self):
        definition = _reference_endpoint()
        definition["bindings"]["reference_images"][0]["consumer"]["node"] = "404"

        codes = _codes(validate_definition(definition))

        assert ("bindings.reference_images[0].consumer.node", "comfyui_node_not_found") in codes

    def test_a_consumer_input_must_exist_on_that_node(self):
        definition = _reference_endpoint()
        definition["bindings"]["reference_images"][0]["consumer"]["input"] = "image9"

        codes = _codes(validate_definition(definition))

        assert ("bindings.reference_images[0].consumer.input", "comfyui_input_not_found") in codes

    def test_a_consumer_class_type_that_drifted_is_refused(self):
        """Type mismatch bases the decision to modify the graph for fewer images on an outdated graph."""
        definition = _reference_endpoint()
        definition["bindings"]["reference_images"][0]["consumer"]["class_type"] = "ImageStitch"

        codes = _codes(validate_definition(definition))

        assert ("bindings.reference_images[0].consumer.class_type", "comfyui_class_type_mismatch") in codes

    def test_a_consumer_the_loader_does_not_feed_is_refused(self):
        """An existing, correctly typed input unrelated to the slot makes construction modify an unrelated input."""
        definition = _reference_endpoint()
        definition["workflow"]["22"] = {
            "class_type": "ImageBatch",
            "inputs": {"image1": ["8", 0], "image2": ["8", 0]},
            "_meta": {"title": "别处的批次"},
        }
        definition["bindings"]["reference_images"][0]["consumer"] |= {"node": "22", "title": "别处的批次"}

        codes = _codes(validate_definition(definition))

        assert ("bindings.reference_images[0].consumer.input", "comfyui_consumer_not_fed") in codes

    def test_a_consumer_reached_through_an_intermediate_node_is_accepted(self):
        """Inferred consumers need not be direct: relay nodes may precede the image-receiving input."""
        definition = _reference_endpoint()
        definition["workflow"]["19"] = {"class_type": "ImageScale", "inputs": {"image": ["20", 0]}}
        definition["workflow"]["21"]["inputs"]["image1"] = ["19", 0]

        assert validate_definition(definition).valid

    def test_a_consumer_input_holding_a_literal_is_refused(self):
        """An unconnected input cannot be fed by this slot."""
        definition = _reference_endpoint()
        definition["workflow"]["21"]["inputs"]["image1"] = "写死的图.png"

        codes = _codes(validate_definition(definition))

        assert ("bindings.reference_images[0].consumer.input", "comfyui_consumer_not_fed") in codes

    def test_a_consumer_without_its_class_type_is_refused_by_the_schema(self):
        """Construction looks up by ``class_type``; without it the record is unusable."""
        definition = _reference_endpoint()
        del definition["bindings"]["reference_images"][0]["consumer"]["class_type"]

        assert not validate_definition(definition).valid


class TestSingleFrameRateSourceOfTruth:
    def test_two_read_only_fps_bindings_with_different_literals_are_refused(self):
        """One frame rate applies to all frame-count targets; two literals make the workflow's frame rate ambiguous."""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["21"] = {"class_type": "CreateVideo", "inputs": {"fps": 24}}
        definition["bindings"]["fps"].append(
            {"node": "21", "input": "fps", "class_type": "CreateVideo", "direction": "read"}
        )

        assert ("bindings.fps", "comfyui_fps_conflict") in _codes(validate_definition(definition))

    def test_a_manually_typed_fps_that_contradicts_the_binding_is_refused_too(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["bindings"]["frames"] = [
            {"node": "5", "input": "length", "class_type": "EmptyLatentImage", "fps": 30}
        ]

        assert ("bindings.fps", "comfyui_fps_conflict") in _codes(validate_definition(definition))

    def test_several_sources_agreeing_on_one_value_pass(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["21"] = {"class_type": "CreateVideo", "inputs": {"fps": 16}}
        definition["bindings"]["fps"].append(
            {"node": "21", "input": "fps", "class_type": "CreateVideo", "direction": "read"}
        )
        definition["workflow"]["5"]["inputs"]["length"] = 81
        definition["bindings"]["frames"] = [
            {"node": "5", "input": "length", "class_type": "EmptyLatentImage", "fps": 16}
        ]

        assert validate_definition(definition).errors == ()

    def test_an_image_endpoint_is_out_of_scope(self):
        """Image endpoints lack frame-count/frame-rate semantic keys and do not enter this check."""
        assert validate_definition(_image_endpoint()).errors == ()


class TestAuthScope:
    def test_the_api_key_placeholder_is_refused_outside_auth(self):
        """The workflow is embedded unchanged; placeholders inside have no effect and distribute credentials with exports."""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["6"]["inputs"]["text"] = "{{ api_key }}"

        assert ("workflow.6.inputs.text", "api_key_outside_auth") in _codes(validate_definition(definition))

    def test_a_non_empty_auth_section_must_reference_the_api_key(self):
        definition = comfyui_endpoint_definition(auth={"headers": {"X-Proxy-Token": "写死的密钥"}})

        assert ("auth", "auth_without_api_key") in _codes(validate_definition(definition))

    def test_auth_knows_no_variable_other_than_the_api_key(self):
        definition = comfyui_endpoint_definition(auth={"query": {"token": "{{ api_key }}", "url": "{{ base_url }}"}})

        assert ("auth.query.url", "undeclared_variable") in _codes(validate_definition(definition))

    def test_a_malformed_placeholder_next_to_a_good_one_is_reported(self):
        """Mixed forms pass the api_key-reference check: malformed templates reach the proxy unchanged, obscuring the mistake."""
        definition = comfyui_endpoint_definition(
            auth={"headers": {"Authorization": "{{api_key}}-{{ api_key | upper }}"}}
        )

        diagnostics = validate_definition(definition)

        assert ("auth.headers.Authorization", "malformed_placeholder") in _codes(diagnostics)

    @pytest.mark.parametrize("name", ["filename", "subfolder", "type"])
    def test_a_credential_cannot_take_a_name_the_artifact_download_already_needs(self, name: str):
        """Artifact retrieval needs these three parameters; credentials cannot share their keys.

        With collisions, submission and polling pass but downloads lack credentials: the proxy returns 401, wasting an execution that already produced
        output. Reject when saving, while users can still correct it.
        """
        definition = comfyui_endpoint_definition(auth={"query": {name: "{{ api_key }}"}})

        assert (f"auth.query.{name}", "auth_query_reserved") in _codes(validate_definition(definition))

    def test_a_credential_query_under_any_other_name_is_fine(self):
        assert validate_definition(comfyui_endpoint_definition(auth={"query": {"token": "{{ api_key }}"}})).valid

    def test_a_literal_credential_is_warned_about_without_blocking_the_save(self):
        """Literal credentials leak unchanged through export and Copy as Mine, but are valid configuration and only warn."""
        definition = comfyui_endpoint_definition(
            auth={"headers": {"Authorization": "Bearer {{ api_key }}", "X-Team": "sk-9f2c41ab77de05631b8a"}}
        )

        diagnostics = validate_definition(definition)

        assert diagnostics.valid
        assert ("auth.headers.X-Team", "auth_literal_credential") in _warning_codes(diagnostics)

    def test_header_names_differing_only_in_case_are_refused(self):
        """HTTP header names ignore case; both are sent, and which the server receives depends on implementation."""
        definition = comfyui_endpoint_definition(
            auth={"headers": {"Authorization": "Bearer {{ api_key }}", "authorization": "{{ api_key }}"}}
        )

        assert ("auth.headers.authorization", "header_name_duplicate") in _codes(validate_definition(definition))


class TestDiagnosticPayload:
    def test_a_capabilities_section_is_reported_as_removed_with_its_reason(self):
        definition = comfyui_endpoint_definition(capabilities={"first_frame": True})

        payload = validate_definition(definition).to_payload(code_translator)

        assert payload["errors"][0]["code"] == "removed_field"
        assert REMOVED_FIELD_REASONS["capabilities"] in payload["errors"][0]["message"]

    def test_a_structural_error_stops_the_semantic_layer(self):
        """Without valid structure, binding/workflow cross-checks have no foundation and only produce misleading secondary errors."""
        definition = comfyui_endpoint_definition()
        definition["workflow"] = []
        definition["bindings"]["prompt"] = []

        assert _codes(validate_definition(definition)) == [("workflow", "invalid_type")]


def _reference_endpoint() -> dict[str, Any]:
    """Definition with reference-image slots: image loaders merge through ``ImageBatch``."""
    definition = comfyui_endpoint_definition()
    definition["workflow"]["20"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
    definition["workflow"]["21"] = {
        "class_type": "ImageBatch",
        "inputs": {"image1": ["20", 0], "image2": ["20", 0]},
        "_meta": {"title": "Image Batch"},
    }
    definition["bindings"]["reference_images"] = [
        {
            "node": "20",
            "input": "image",
            "class_type": "LoadImage",
            "consumer": {"node": "21", "input": "image1", "class_type": "ImageBatch", "title": "Image Batch"},
        }
    ]
    return definition


def _image_endpoint(**bindings: object) -> dict[str, object]:
    """Image-endpoint definition: remove all video-only semantic keys, then add the key under test."""
    definition = comfyui_endpoint_definition(media_type="image")
    for key in ("start_image", "end_image", "frames", "fps"):
        definition["bindings"].pop(key, None)
    definition["bindings"].update(bindings)
    return definition


def _codes(diagnostics: DefinitionDiagnostics) -> list[tuple[str, str]]:
    return [(issue.path, issue.code.value) for issue in diagnostics.errors]


def _warning_codes(diagnostics: DefinitionDiagnostics) -> list[tuple[str, str]]:
    return [(issue.path, issue.code.value) for issue in diagnostics.warnings]
