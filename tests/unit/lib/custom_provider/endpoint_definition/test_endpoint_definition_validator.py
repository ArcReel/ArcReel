"""声明式定义共享校验器：合法定义放行，违规定义产出定位到字段的结构化诊断。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.custom_provider.endpoint_definition import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    message_key,
    validate_definition,
)
from lib.custom_provider.endpoint_definition.validator import REMOVED_FIELD_REASONS
from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from tests.factories import make_translator


def custom_endpoint_definition() -> dict[str, Any]:
    """最小可用定义：单张首帧、提交 + 轮询、扁平取值，校验零错误零警告。用例就地改出反例。"""
    return {
        "kind": "declarative",
        "schema_version": "1.0.0",
        "meta": {"name": "示例端点", "author": "ArcReel", "version": "0.1.0"},
        "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
        "inputs": {"first_frame": {"source": "start_image", "encoding": "data_uri"}},
        "enum_maps": {"duration": {"5": 5, "10": 10}},
        "submit": {
            "method": "POST",
            "url": "{{ base_url }}/v1/video/create",
            "body": {
                "model": "{{ model }}",
                "prompt": "{{ prompt }}",
                "image": "{{ inputs.first_frame }}",
                "duration": "{{ duration }}",
            },
            "extract": {"task_id": ["$.task_id"], "error": ["$.error.message"]},
        },
        "poll": {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/fetch/{{ task_id }}",
            "extract": {"status": ["$.status"], "video_url": ["$.video_url"], "error": ["$.error"]},
        },
        "status_map": {"pending": "queued", "processing": "running", "completed": "succeeded", "failed": "failed"},
        "capabilities": {"first_frame": True},
    }


def _codes(diagnostics: DefinitionDiagnostics) -> list[tuple[str, str]]:
    return [(issue.path, issue.code.value) for issue in diagnostics.errors]


def _first(diagnostics: DefinitionDiagnostics, code: DefinitionErrorCode) -> tuple[str, str]:
    matches = [(issue.path, issue.code.value) for issue in diagnostics.errors if issue.code is code]
    assert matches, f"{code.value} 未出现，实际诊断：{_codes(diagnostics)}"
    return matches[0]


def _full_featured() -> dict[str, Any]:
    """把可选构造全部用上的一份定义：$each、$when、派生尺寸、二次取件、失败路径、JSON-in-string。"""
    definition = custom_endpoint_definition()
    definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
    definition["inputs"]["tail"] = {"source": "end_image", "encoding": "data_uri"}
    definition["submit"]["body"]["images"] = [
        {"$each": {"in": "inputs.refs", "as": "ref", "item": {"url": "{{ ref }}", "order": "{{ index }}"}}}
    ]
    definition["submit"]["body"]["tail"] = {
        "$when": "tail",
        "image": "{{ inputs.tail }}",
        "size": "{{ width }}x{{ height }}",
    }
    definition["capabilities"].update({"last_frame": True, "max_reference_images": 4})
    definition["poll"]["extract"]["failure"] = ["$.base_resp[?@.status_code != 0].status_msg"]
    definition["poll"]["extract"]["result_id"] = ["$.file_id"]
    definition["poll"]["extract"]["usage"] = {"duration_seconds": {"paths": ["$.usage.seconds"], "accept": "scalar"}}
    definition["result"] = {
        "method": "GET",
        "url": "{{ base_url }}/v1/files/{{ result_id }}",
        "extract": {"video_url": [{"path": "$.data.result_json", "json_decode": True, "then": ["$.download_url"]}]},
    }
    return definition


class TestAcceptedDefinitions:
    def test_minimal_definition_is_clean(self):
        diagnostics = validate_definition(custom_endpoint_definition())
        assert diagnostics.valid
        assert not diagnostics.warnings

    def test_credential_free_endpoint_may_leave_auth_empty(self):
        definition = custom_endpoint_definition()
        definition["auth"] = {}
        assert validate_definition(definition).valid

    def test_response_body_itself_may_be_the_value(self):
        definition = custom_endpoint_definition()
        definition["submit"]["extract"]["task_id"] = ["$"]
        assert validate_definition(definition).valid

    def test_full_featured_definition_is_clean(self):
        diagnostics = validate_definition(_full_featured())
        assert diagnostics.valid, _codes(diagnostics)
        assert not diagnostics.warnings


class TestStructuralIssues:
    def test_missing_required_field_points_at_its_container(self):
        definition = custom_endpoint_definition()
        del definition["meta"]["author"]
        assert _first(validate_definition(definition), DefinitionErrorCode.MISSING_FIELD) == (
            "meta",
            "missing_field",
        )

    def test_unknown_kind_is_rejected(self):
        definition = custom_endpoint_definition()
        definition["kind"] = "python"
        assert _first(validate_definition(definition), DefinitionErrorCode.INVALID_ENUM_VALUE)[0] == "kind"

    def test_stray_top_level_field_is_unknown(self):
        definition = custom_endpoint_definition()
        definition["api_key"] = "sk-xxx"
        assert _first(validate_definition(definition), DefinitionErrorCode.UNKNOWN_FIELD)[0] == "$"

    @pytest.mark.parametrize(
        ("section", "field", "value"),
        [
            ("poll", "interval_seconds", 5),
            ("poll", "success_status_codes", [200]),
            ("submit", "query", {"key": "value"}),
        ],
    )
    def test_removed_request_field_says_where_it_went(self, section: str, field: str, value: object):
        definition = custom_endpoint_definition()
        definition[section][field] = value
        assert _first(validate_definition(definition), DefinitionErrorCode.REMOVED_FIELD) == (
            section,
            "removed_field",
        )

    def test_removed_extract_source_is_named(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["status"] = {"paths": ["$.status"], "source": "headers"}
        assert validate_definition(definition).errors[0].code is DefinitionErrorCode.REMOVED_FIELD

    def test_then_without_json_decode_is_incomplete(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = [{"path": "$.a", "then": ["$.b"]}]
        assert _first(validate_definition(definition), DefinitionErrorCode.MISSING_FIELD)[0] == (
            "poll.extract.video_url[0]"
        )

    @pytest.mark.parametrize(
        ("directive", "code"),
        [
            (
                {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}", "key": "k"},
                DefinitionErrorCode.EACH_SHAPE_INVALID,
            ),
            ({"in": "inputs.refs", "as": "ref", "key": "k"}, DefinitionErrorCode.EACH_SHAPE_INVALID),
            ({"in": "refs", "as": "ref", "item": "{{ ref }}"}, DefinitionErrorCode.INVALID_VALUE),
        ],
    )
    def test_malformed_each_is_rejected(self, directive: dict[str, Any], code: DefinitionErrorCode):
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["submit"]["body"]["images"] = [{"$each": directive}]
        diagnostics = validate_definition(definition)
        assert _first(diagnostics, code)[0].startswith("submit.body.images[0].$each")


class TestCredentialWriteSite:
    def test_api_key_may_not_leave_the_auth_section(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["api_key"] = "{{ api_key }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.API_KEY_OUTSIDE_AUTH) == (
            "submit.body.api_key",
            "api_key_outside_auth",
        )

    def test_request_header_may_not_shadow_the_auth_header(self):
        definition = custom_endpoint_definition()
        definition["submit"]["headers"] = {"authorization": "Bearer leaked"}
        assert _first(validate_definition(definition), DefinitionErrorCode.AUTH_HEADER_CONFLICT) == (
            "submit.headers.authorization",
            "auth_header_conflict",
        )

    def test_url_may_not_carry_the_auth_query_parameter(self):
        definition = custom_endpoint_definition()
        definition["auth"] = {"query": {"token": "{{ api_key }}"}}
        definition["submit"]["url"] = "{{ base_url }}/v1/video/create?token=inline"
        assert _first(validate_definition(definition), DefinitionErrorCode.AUTH_QUERY_CONFLICT) == (
            "submit.url",
            "auth_query_conflict",
        )

    def test_non_empty_auth_must_write_the_credential(self):
        definition = custom_endpoint_definition()
        definition["auth"] = {"headers": {"X-Client": "arcreel"}}
        assert _first(validate_definition(definition), DefinitionErrorCode.AUTH_WITHOUT_API_KEY) == (
            "auth",
            "auth_without_api_key",
        )


class TestCapabilityConsistency:
    def test_declared_capability_without_the_asset_is_a_lie(self):
        definition = custom_endpoint_definition()
        definition["capabilities"]["last_frame"] = True
        assert _first(validate_definition(definition), DefinitionErrorCode.CAPABILITY_DECLARED_WITHOUT_INPUT) == (
            "capabilities.last_frame",
            "capability_declared_without_input",
        )

    def test_sent_asset_without_the_declaration_is_hidden(self):
        definition = custom_endpoint_definition()
        definition["inputs"]["tail"] = {"source": "end_image", "encoding": "data_uri"}
        definition["submit"]["body"]["image_tail"] = "{{ inputs.tail }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.CAPABILITY_INPUT_WITHOUT_DECLARATION) == (
            "capabilities.last_frame",
            "capability_input_without_declaration",
        )

    def test_reference_images_capability_counts_the_declared_maximum(self):
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["submit"]["body"]["images"] = [{"$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}]
        definition["capabilities"]["max_reference_images"] = 0
        assert _first(validate_definition(definition), DefinitionErrorCode.CAPABILITY_INPUT_WITHOUT_DECLARATION) == (
            "capabilities.max_reference_images",
            "capability_input_without_declaration",
        )

    def test_declared_asset_must_be_referenced(self):
        definition = custom_endpoint_definition()
        definition["inputs"]["tail"] = {"source": "end_image", "encoding": "data_uri"}
        definition["capabilities"]["last_frame"] = True
        assert _first(validate_definition(definition), DefinitionErrorCode.INPUT_NOT_REFERENCED) == (
            "inputs.tail",
            "input_not_referenced",
        )


class TestPlaceholderScope:
    def test_unknown_variable_is_named(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["tier"] = "{{ service_tier }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.UNDECLARED_VARIABLE) == (
            "submit.body.tier",
            "undeclared_variable",
        )

    def test_task_id_is_not_available_at_submit_time(self):
        definition = custom_endpoint_definition()
        definition["submit"]["url"] = "{{ base_url }}/v1/video/{{ task_id }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.TASK_ID_OUT_OF_SCOPE)[0] == "submit.url"

    def test_result_id_needs_the_poll_extraction(self):
        definition = _full_featured()
        del definition["poll"]["extract"]["result_id"]
        assert _first(validate_definition(definition), DefinitionErrorCode.RESULT_ID_WITHOUT_EXTRACT)[0] == "result.url"

    def test_list_asset_may_not_be_interpolated_directly(self):
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["submit"]["body"]["images"] = "{{ inputs.refs }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.LIST_INPUT_REQUIRES_EACH) == (
            "submit.body.images",
            "list_input_requires_each",
        )

    def test_each_must_iterate_a_declared_list_asset(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["images"] = [
            {"$each": {"in": "inputs.first_frame", "as": "ref", "item": "{{ ref }}"}}
        ]
        assert _first(validate_definition(definition), DefinitionErrorCode.EACH_IN_NOT_LIST_INPUT)[0] == (
            "submit.body.images[0].$each.in"
        )

    def test_when_must_guard_a_declared_asset(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["tail"] = {"$when": "missing", "image": "{{ prompt }}"}
        assert _first(validate_definition(definition), DefinitionErrorCode.WHEN_UNKNOWN_INPUT)[0] == (
            "submit.body.tail.$when"
        )

    def test_assets_are_only_available_to_the_submit_request(self):
        definition = custom_endpoint_definition()
        definition["poll"]["url"] = "{{ base_url }}/v1/fetch/{{ task_id }}/{{ inputs.first_frame }}"
        assert _first(validate_definition(definition), DefinitionErrorCode.INPUT_OUT_OF_SCOPE)[0] == "poll.url"

    def test_polling_may_not_spread_a_list_asset_either(self):
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["submit"]["body"]["images"] = [{"$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}]
        definition["capabilities"]["max_reference_images"] = 4
        definition["poll"]["body"] = [{"$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}]
        assert _first(validate_definition(definition), DefinitionErrorCode.INPUT_OUT_OF_SCOPE)[0] == (
            "poll.body[0].$each.in"
        )

    def test_polling_may_not_guard_on_an_asset(self):
        definition = custom_endpoint_definition()
        definition["poll"]["body"] = {"tail": {"$when": "first_frame", "flag": "1"}}
        assert _first(validate_definition(definition), DefinitionErrorCode.INPUT_OUT_OF_SCOPE)[0] == (
            "poll.body.tail.$when"
        )


class TestDictionaries:
    def test_only_tier_parameters_may_be_mapped(self):
        definition = custom_endpoint_definition()
        definition["enum_maps"]["prompt"] = {"a": "b"}
        assert _first(validate_definition(definition), DefinitionErrorCode.ENUM_MAP_VARIABLE_NOT_ALLOWED) == (
            "enum_maps.prompt",
            "enum_map_variable_not_allowed",
        )

    def test_expired_is_not_a_declarative_status(self):
        definition = custom_endpoint_definition()
        definition["status_map"]["gone"] = "expired"
        assert _first(validate_definition(definition), DefinitionErrorCode.STATUS_MAP_TARGET_INVALID) == (
            "status_map.gone",
            "status_map_target_invalid",
        )


class TestExtractionPaths:
    @pytest.mark.parametrize(
        ("path_expression", "code"),
        [
            ("$..url", DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT),
            ("$['url','uri']", DefinitionErrorCode.JSONPATH_UNION),
            ("$.data[0:9:2].url", DefinitionErrorCode.JSONPATH_SLICE_STEP),
            ("$.data[?length(@.url) > 0]", DefinitionErrorCode.JSONPATH_FUNCTION_EXTENSION),
        ],
    )
    def test_forbidden_construct_is_reported_at_its_slot(self, path_expression: str, code: DefinitionErrorCode):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = ["$.video_url", path_expression]
        assert _first(validate_definition(definition), code) == ("poll.extract.video_url[1]", code.value)

    def test_forbidden_construct_inside_a_json_decode_suffix_is_reported(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = [
            {"path": "$.data.payload", "json_decode": True, "then": ["$..url"]}
        ]
        assert _first(validate_definition(definition), DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT)[0] == (
            "poll.extract.video_url[0].then[0]"
        )

    def test_usage_paths_are_checked_too(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["usage"] = {"duration_seconds": ["$..seconds"]}
        assert _first(validate_definition(definition), DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT)[0] == (
            "poll.extract.usage.duration_seconds[0]"
        )


class TestWarnings:
    def test_polling_without_the_task_id_is_only_a_warning(self):
        definition = custom_endpoint_definition()
        definition["poll"]["url"] = "{{ base_url }}/v1/video/latest"
        diagnostics = validate_definition(definition)
        assert diagnostics.valid
        assert [issue.code for issue in diagnostics.warnings] == [DefinitionErrorCode.POLL_WITHOUT_TASK_ID]

    def test_wildcard_ordering_is_only_a_warning(self):
        definition = custom_endpoint_definition()
        definition["poll"]["extract"]["video_url"] = ["$.outputs[*].url"]
        diagnostics = validate_definition(definition)
        assert diagnostics.valid
        assert [issue.code for issue in diagnostics.warnings] == [DefinitionErrorCode.JSONPATH_WILDCARD_ORDER]


class TestDiagnosticPayload:
    def test_payload_carries_path_code_and_rendered_message(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["api_key"] = "{{ api_key }}"
        payload = validate_definition(definition).to_payload(make_translator("en"))
        assert payload["errors"][0]["path"] == "submit.body.api_key"
        assert payload["errors"][0]["code"] == "api_key_outside_auth"
        assert "auth" in payload["errors"][0]["message"]

    def test_removed_field_message_embeds_the_translated_reason(self):
        definition = custom_endpoint_definition()
        definition["poll"]["interval_seconds"] = 5
        payload = validate_definition(definition).to_payload(make_translator("zh"))
        assert "运行时策略" in payload["errors"][0]["message"]

    def test_every_code_reads_as_prose_in_every_locale(self):
        keys = {message_key(code) for code in DefinitionErrorCode} | set(REMOVED_FIELD_REASONS.values())
        for key in sorted(keys):
            for locale in SUPPORTED_LOCALES:
                assert key in MESSAGES[locale], f"{key} 缺 {locale} 文案"
