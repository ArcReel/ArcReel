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
from tests.factories import custom_endpoint_definition, make_translator


@pytest.mark.parametrize(("required", "declared_t2v"), [(True, False), (False, True)])
def test_text_to_video_declaration_matching_required_inputs_is_accepted(required: bool, declared_t2v: bool):
    definition = custom_endpoint_definition()
    definition["inputs"]["first_frame"]["required"] = required
    definition["capabilities"]["text_to_video"] = declared_t2v

    assert validate_definition(definition).valid


@pytest.mark.parametrize(("required", "declared_t2v"), [(True, True), (False, False)])
def test_declared_text_to_video_must_match_required_inputs(required: bool, declared_t2v: bool):
    """显式位与必需图输入两向一致：声明支持文生却要求图、声明不支持却不要求图都是错。"""
    definition = custom_endpoint_definition()
    definition["inputs"]["first_frame"]["required"] = required
    definition["capabilities"]["text_to_video"] = declared_t2v

    assert ("capabilities.text_to_video", "capability_incoherent") in _codes(validate_definition(definition))


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"reference_audio_mode": "direct", "max_reference_audio_count": 0}, "capabilities.reference_audio_mode"),
        ({"reference_audio_per_image": True}, "capabilities.reference_audio_per_image"),
        (
            {"first_frame": False, "first_frame_ratio_adaptive_only": True},
            "capabilities.first_frame_ratio_adaptive_only",
        ),
    ],
)
def test_capability_group_rejects_incoherent_combinations(changes: dict[str, object], path: str):
    definition = custom_endpoint_definition()
    definition["capabilities"].update(changes)

    assert (path, "capability_incoherent") in _codes(validate_definition(definition))


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

    def test_secondary_retrieval_may_be_the_only_source_of_the_video_url(self):
        """产物地址由 result 节取时，轮询不必再编一条 video_url 路径。"""
        definition = _full_featured()
        del definition["poll"]["extract"]["video_url"]
        diagnostics = validate_definition(definition)
        assert diagnostics.valid, _codes(diagnostics)


class TestStructuralIssues:
    def test_missing_required_field_points_at_its_container(self):
        definition = custom_endpoint_definition()
        del definition["meta"]["author"]
        assert _first(validate_definition(definition), DefinitionErrorCode.MISSING_FIELD) == (
            "meta",
            "missing_field",
        )

    @pytest.mark.parametrize("field", ["schema_version", "meta_version"])
    def test_version_with_trailing_newline_is_rejected(self, field: str):
        """Python 的 `$` 会放过末尾换行，semver 另有一条禁换行的判定兜住。"""
        definition = custom_endpoint_definition()
        if field == "schema_version":
            definition["schema_version"] = "1.0.0\n"
        else:
            definition["meta"]["version"] = "0.1.0\n"
        assert validate_definition(definition).errors

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

    @pytest.mark.parametrize("section", ["submit", "poll"])
    @pytest.mark.parametrize("url", ["", " ", "\t", "\n"])
    def test_blank_request_url_is_rejected(self, section: str, url: str):
        """全空白的 URL 没有请求目标，保存时就该拒，不留到运行时发请求才失败。"""
        definition = custom_endpoint_definition()
        definition[section]["url"] = url
        assert validate_definition(definition).errors, f"{section}.url 为 {url!r} 应被结构层拦下"

    @pytest.mark.parametrize("header", ["X_API_KEY", "X.Foo", "X~Foo"])
    def test_tchar_header_names_are_accepted(self, header: str):
        """键取 HTTP 字段名的 tchar 集：下划线等合法记号是真实供应商在用的写法，不该拦。"""
        definition = custom_endpoint_definition()
        definition["submit"]["headers"] = {header: "arcreel"}
        assert not validate_definition(definition).errors

    @pytest.mark.parametrize("value", ["ok\r\nInjected: yes", "ok\nInjected: yes"])
    def test_header_value_with_line_break_is_rejected(self, value: str):
        """字段值里的换行是另起一个头；HTTP 客户端本就会拒，保存时先拦下。"""
        definition = custom_endpoint_definition()
        definition["submit"]["headers"] = {"X-Client": value}
        assert validate_definition(definition).errors

    def test_header_name_with_trailing_newline_is_rejected(self):
        """换行不是 tchar；Python 的 `$` 会放过末尾换行，故另有一条禁换行的判定。"""
        definition = custom_endpoint_definition()
        definition["submit"]["headers"] = {"X-Client\n": "arcreel"}
        assert validate_definition(definition).errors

    def test_poll_must_extract_the_video_url_without_secondary_retrieval(self):
        """没有 result 节时产物地址只能从轮询响应取，缺了就无处可取。"""
        definition = custom_endpoint_definition()
        del definition["poll"]["extract"]["video_url"]
        assert _first(validate_definition(definition), DefinitionErrorCode.MISSING_FIELD)[0] == "poll.extract"

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

    @pytest.mark.parametrize(
        ("body_value", "directive"),
        [
            ({"$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}, "对象位置写了 item"),
            (
                [{"$each": {"in": "inputs.refs", "as": "ref", "key": "{{ index }}", "value": "{{ ref }}"}}],
                "数组位置写了 key/value",
            ),
        ],
    )
    def test_each_form_must_match_its_position(self, body_value: object, directive: str):
        """数组位置铺元素、对象位置铺键值对：写反了没有可执行的展开语义。"""
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["capabilities"]["max_reference_images"] = 4
        definition["submit"]["body"]["images"] = body_value
        diagnostics = validate_definition(definition)
        assert _first(diagnostics, DefinitionErrorCode.EACH_POSITION_MISMATCH)[0].endswith("$each"), directive

    def test_each_alias_may_not_shadow_index(self):
        """`index` 在循环体内恒为序号：拿它当元素别名，两个值就再也分不开。"""
        definition = custom_endpoint_definition()
        definition["inputs"]["refs"] = {"source": "reference_images", "encoding": "base64"}
        definition["capabilities"]["max_reference_images"] = 4
        definition["submit"]["body"]["images"] = [
            {"$each": {"in": "inputs.refs", "as": "index", "item": "{{ index }}"}}
        ]
        diagnostics = validate_definition(definition)
        assert _first(diagnostics, DefinitionErrorCode.EACH_ALIAS_RESERVED)[0].endswith("$each.as")

    @pytest.mark.parametrize("hint", ["http://", "http:// bad", "not a uri", "https://api.example ", "https://a b"])
    def test_unusable_base_url_hint_is_rejected(self, hint: str):
        definition = custom_endpoint_definition()
        definition["meta"]["hints"] = {"base_url": hint}
        assert validate_definition(definition).errors, f"{hint} 应被结构层拦下"

    def test_negative_reference_audio_budget_is_rejected(self):
        """负的音频总时长上限会让每个非空音频请求都超限，端点接受了却什么也生成不了。"""
        definition = custom_endpoint_definition()
        definition["capabilities"]["max_reference_audio_total_seconds"] = -1
        diagnostics = validate_definition(definition)
        assert diagnostics.errors, "负的时长上限应被结构层拦下"

    @pytest.mark.parametrize("field_path", [("meta", "homepage"), ("meta", "hints", "base_url")])
    def test_non_http_url_hint_is_rejected(self, field_path: tuple[str, ...]):
        """`format: uri` 在 jsonschema 里只是注解（未装 uri 检查器时恒为真），闸门是同处的 pattern。"""
        definition = custom_endpoint_definition()
        container: Any = definition
        for key in field_path[:-1]:
            container = container.setdefault(key, {})
        container[field_path[-1]] = "not a uri"
        diagnostics = validate_definition(definition)
        assert diagnostics.errors, f"{'.'.join(field_path)} 非 http(s) 应被结构层拦下"


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

    def test_percent_encoded_url_query_still_collides(self):
        """按服务端解析后的名字比对：`%74oken` 解码即 `token`，同一参数会被写两遍。"""
        definition = custom_endpoint_definition()
        definition["auth"] = {"query": {"token": "{{ api_key }}"}}
        definition["submit"]["url"] = "{{ base_url }}/v1/video/create?%74oken=inline"
        assert _first(validate_definition(definition), DefinitionErrorCode.AUTH_QUERY_CONFLICT)[0] == "submit.url"

    def test_plus_encoded_url_query_still_collides(self):
        """查询串里的 `+` 解析为空格：`api+key` 与 `api key` 是同一个参数。"""
        definition = custom_endpoint_definition()
        definition["auth"] = {"query": {"api key": "{{ api_key }}"}}
        definition["submit"]["url"] = "{{ base_url }}/v1/video/create?api+key=inline"
        assert _first(validate_definition(definition), DefinitionErrorCode.AUTH_QUERY_CONFLICT)[0] == "submit.url"

    @pytest.mark.parametrize("path", ["auth", "submit"])
    def test_case_colliding_header_names_are_rejected(self, path: str):
        """HTTP 头名不区分大小写：同一张表里写两种大小写，两条会一起发出去。"""
        definition = custom_endpoint_definition()
        if path == "auth":
            definition["auth"] = {"headers": {"Authorization": "Bearer {{ api_key }}", "authorization": "x"}}
        else:
            definition["submit"]["headers"] = {"X-Client": "arcreel", "x-client": "dup"}
        assert _first(validate_definition(definition), DefinitionErrorCode.HEADER_NAME_DUPLICATE)[0].startswith(path)

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
    @pytest.mark.parametrize(
        "template",
        [
            "{{ prompt | upper }}",
            "{{ inputs.first_frame[0] }}",
            "{{ prompt",
            "{{ 'literal' }}",
        ],
    )
    def test_malformed_placeholder_is_reported(self, template: str):
        """格式只认裸变量：写成 Jinja 那样不会被当占位符，不报就会原样发给供应商。"""
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["prompt"] = template
        diagnostics = validate_definition(definition)
        assert _first(diagnostics, DefinitionErrorCode.MALFORMED_PLACEHOLDER)[0] == "submit.body.prompt"

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

    def test_only_caller_optional_parameters_may_have_defaults(self):
        definition = custom_endpoint_definition()
        definition["defaults"] = {"prompt": "a cat"}
        assert _first(validate_definition(definition), DefinitionErrorCode.DEFAULT_VARIABLE_NOT_ALLOWED) == (
            "defaults.prompt",
            "default_variable_not_allowed",
        )

    def test_a_default_of_the_wrong_type_is_rejected(self):
        """缺省值渲染前原样进上下文：数值型 aspect_ratio 会让宽高派生拿不到档位。"""
        definition = custom_endpoint_definition()
        definition["defaults"] = {"aspect_ratio": 16}
        assert _first(validate_definition(definition), DefinitionErrorCode.DEFAULT_VALUE_TYPE_INVALID) == (
            "defaults.aspect_ratio",
            "default_value_type_invalid",
        )

    def test_a_bool_default_cannot_impersonate_an_int(self):
        """bool 是 int 的子类，True 不得冒充合法的 duration。"""
        definition = custom_endpoint_definition()
        definition["defaults"] = {"duration": True}
        assert _first(validate_definition(definition), DefinitionErrorCode.DEFAULT_VALUE_TYPE_INVALID) == (
            "defaults.duration",
            "default_value_type_invalid",
        )

    def test_a_default_outside_the_enum_map_is_rejected(self):
        """缺省值写的是 ArcReel 侧的值，渲染时照常查表：查不到就等于每次不指定该参数都渲染失败。"""
        definition = custom_endpoint_definition()
        definition["enum_maps"] = {"resolution": {"768p": "768P"}}
        definition["defaults"] = {"resolution": "1080p"}
        assert _first(validate_definition(definition), DefinitionErrorCode.DEFAULT_VALUE_NOT_IN_ENUM_MAP) == (
            "defaults.resolution",
            "default_value_not_in_enum_map",
        )

    def test_a_default_within_the_enum_map_passes(self):
        definition = custom_endpoint_definition()
        definition["enum_maps"] = {"resolution": {"768p": "768P"}}
        definition["defaults"] = {"resolution": "768p"}

        assert validate_definition(definition).valid

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
