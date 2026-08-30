"""声明式端点请求模板渲染。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import (
    AssetData,
    DefinitionErrorCode,
    build_context,
    encode_inputs,
    render_request,
)
from lib.custom_provider.endpoint_definition.template_engine import TemplateRenderError


def _context(**parameters: object):
    return build_context({"base_url": "https://example.test", "api_key": "K/1", **parameters}, {})


def test_render_request_preserves_types_expands_each_and_guards_optional_assets():
    inputs = encode_inputs(
        {
            "first": {"source": "start_image", "encoding": "data_uri"},
            "refs": {"source": "reference_images", "encoding": "base64"},
            "tail": {"source": "end_image", "encoding": "data_uri"},
        },
        {
            "start_image": AssetData("image/png", b"first"),
            "reference_images": [AssetData("image/jpeg", b"a"), AssetData("image/webp", b"b")],
        },
    )
    context = build_context(
        {
            "base_url": "https://example.test",
            "api_key": "K/1",
            "model": "vendor/model",
            "prompt": "hello",
            "duration": 5,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "seed": None,
        },
        inputs,
    )
    request = render_request(
        {
            "method": "POST",
            "url": "{{ base_url }}/v/{{ model }}",
            "headers": {"X-Size": "{{ width }}x{{ height }}"},
            "body": {
                "prompt": "{{ prompt }}",
                "duration": "{{ duration }}",
                "seed": "{{ seed }}",
                "first": "{{ inputs.first }}",
                "refs": [
                    {"$each": {"in": "inputs.refs", "as": "ref", "item": {"data": "{{ ref }}", "n": "{{ index }}"}}}
                ],
                "last": {"$when": "tail", "data": "{{ inputs.tail }}"},
            },
        },
        context,
        enum_maps={"duration": {"5": "5s"}},
        auth={"headers": {"Authorization": "Bearer {{ api_key }}"}, "query": {"key": "{{ api_key }}"}},
    )

    assert request.url == "https://example.test/v/vendor/model?key=K%2F1"
    assert request.headers == {"X-Size": "720x1280", "Authorization": "Bearer K/1"}
    assert request.body == {
        "prompt": "hello",
        "duration": "5s",
        "first": "data:image/png;base64,Zmlyc3Q=",
        "refs": [{"data": "YQ==", "n": 0}, {"data": "Yg==", "n": 1}],
    }


def test_mixed_text_stringifies_and_missing_value_fails_loud():
    context = _context(prompt="hi", duration=5, seed=None)
    request = render_request(
        {"method": "POST", "url": "{{ base_url }}", "body": {"mix": "seed={{ prompt }}-{{ duration }}"}},
        context,
    )

    assert request.body == {"mix": "seed=hi-5"}

    with pytest.raises(TemplateRenderError) as exc_info:
        render_request(
            {"method": "POST", "url": "{{ base_url }}", "body": {"mix": "seed={{ seed }}"}},
            context,
        )
    assert exc_info.value.code is DefinitionErrorCode.TEMPLATE_TEXT_VARIABLE_NULL


def test_enum_map_miss_fails_loud():
    with pytest.raises(TemplateRenderError) as exc_info:
        render_request(
            {"method": "POST", "url": "{{ base_url }}", "body": {"d": "{{ duration }}"}},
            _context(duration=7),
            enum_maps={"duration": {"5": "5s"}},
        )
    assert exc_info.value.code is DefinitionErrorCode.ENUM_MAP_VALUE_MISSING


def test_undeclared_variable_has_a_specific_failure_code():
    with pytest.raises(TemplateRenderError) as exc_info:
        render_request(
            {"method": "POST", "url": "{{ base_url }}", "body": {"value": "{{ missing }}"}},
            _context(),
        )

    assert exc_info.value.code is DefinitionErrorCode.UNDECLARED_VARIABLE


def test_non_string_rendered_url_has_a_specific_failure_code():
    with pytest.raises(TemplateRenderError) as exc_info:
        render_request({"method": "POST", "url": "{{ port }}"}, _context(port=443))

    assert exc_info.value.code is DefinitionErrorCode.TEMPLATE_URL_NOT_STRING


def test_unknown_asset_encoding_has_a_specific_failure_code():
    with pytest.raises(TemplateRenderError) as exc_info:
        encode_inputs(
            {"first": {"source": "start_image", "encoding": "hex"}},
            {"start_image": AssetData("image/png", b"image")},
        )

    assert exc_info.value.code is DefinitionErrorCode.UNKNOWN_ASSET_ENCODING


def test_each_runtime_value_that_is_not_a_list_has_a_specific_failure_code():
    with pytest.raises(TemplateRenderError) as exc_info:
        render_request(
            {
                "method": "POST",
                "url": "{{ base_url }}",
                "body": [{"$each": {"in": "refs", "as": "ref", "item": "{{ ref }}"}}],
            },
            _context(refs=1),
        )

    assert exc_info.value.code is DefinitionErrorCode.EACH_VALUE_NOT_LIST


def test_enum_maps_apply_to_url_headers_and_auth_query_alike():
    request = render_request(
        {
            "method": "POST",
            "url": "{{ base_url }}/v?res={{ resolution }}",
            "headers": {"X-Res": "{{ resolution }}"},
            "body": {"res": "{{ resolution }}"},
        },
        _context(resolution="720p"),
        enum_maps={"resolution": {"720p": "hd"}},
        auth={"query": {"quality": "{{ resolution }}"}},
    )

    assert request.url == "https://example.test/v?res=hd&quality=hd"
    assert request.headers == {"X-Res": "hd"}
    assert request.body == {"res": "hd"}


def test_auth_query_appends_without_reencoding_existing_url_query():
    request = render_request(
        {"method": "GET", "url": "{{ base_url }}/v?model={{ model }}&note=a b"},
        _context(model="vendor/m"),
        auth={"query": {"key": "{{ api_key }}"}},
    )

    assert request.url == "https://example.test/v?model=vendor/m&note=a b&key=K%2F1"


def test_auth_query_rejects_parameter_already_present_in_url():
    with pytest.raises(TemplateRenderError) as exc_info:
        render_request(
            {"method": "GET", "url": "{{ base_url }}/v?key=static"},
            _context(),
            auth={"query": {"key": "{{ api_key }}"}},
        )
    assert exc_info.value.code is DefinitionErrorCode.AUTH_QUERY_CONFLICT


def test_auth_header_overrides_request_header_case_insensitively():
    request = render_request(
        {"method": "GET", "url": "{{ base_url }}", "headers": {"authorization": "Basic x"}},
        _context(),
        auth={"headers": {"Authorization": "Bearer {{ api_key }}"}},
    )

    assert request.headers == {"Authorization": "Bearer K/1"}


def test_object_each_spreads_key_value_pairs_into_its_parent():
    inputs = encode_inputs(
        {"refs": {"source": "reference_images", "encoding": "base64"}},
        {"reference_images": [AssetData("image/png", b"a"), AssetData("image/png", b"b")]},
    )
    request = render_request(
        {
            "method": "POST",
            "url": "https://example.test",
            "body": {
                "model": "wf",
                "$each": {"in": "inputs.refs", "as": "ref", "key": "image_{{ index }}", "value": "{{ ref }}"},
            },
        },
        build_context({}, inputs),
    )

    assert request.body == {"model": "wf", "image_0": "YQ==", "image_1": "Yg=="}


def test_empty_each_drops_its_parent_key():
    request = render_request(
        {
            "method": "POST",
            "url": "https://example.test",
            "body": {
                "images": {
                    "$each": {"in": "inputs.refs", "as": "ref", "key": "image_{{ index }}", "value": "{{ ref }}"}
                }
            },
        },
        build_context({}, {"refs": []}),
    )

    assert request.body == {}


def test_empty_array_each_drops_its_parent_key():
    request = render_request(
        {
            "method": "POST",
            "url": "https://example.test",
            "body": {"images": [{"$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}]},
        },
        build_context({}, {"refs": []}),
    )

    assert request.body == {}


@pytest.mark.parametrize(
    ("assets", "expected"),
    [
        ({"reference_images": [AssetData("image/png", b"a")]}, {"images": ["YQ=="]}),
        ({}, {}),
    ],
)
def test_array_each_honours_sibling_when_guard(assets, expected):
    inputs = encode_inputs({"refs": {"source": "reference_images", "encoding": "base64"}}, assets)
    request = render_request(
        {
            "method": "POST",
            "url": "https://example.test",
            "body": {"images": [{"$when": "refs", "$each": {"in": "inputs.refs", "as": "ref", "item": "{{ ref }}"}}]},
        },
        build_context({}, inputs),
    )

    assert request.body == expected
