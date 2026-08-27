"""JSONPath 受限子集解析器：哪些写法进得来、哪些被挡在门外。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoint_definition import DefinitionErrorCode, JsonPathSubsetError, parse_json_path
from lib.custom_provider.endpoint_definition.jsonpath_subset import SegmentKind


class TestAcceptedPaths:
    @pytest.mark.parametrize(
        "source",
        [
            "$.video_url",
            "$.data.result.url",
            "$['data']['file-url']",
            "$.data[0].url",
            "$.data[-1].url",
            "$.data[*].url",
            "$.data[0:3].url",
            "$.data[?@.file_type == 'mp4'].url",
            "$.data[?@.width > 0 && @.ready].url",
            "$.data[?!@.failed || @.status != 'error'].url",
            "$.data[?@.score == -1.5e3].url",
            "$.data[1:2:]",
            "$.data [0] .url",
            "$",
            "$.数据.地址",
        ],
    )
    def test_subset_forms_parse(self, source: str):
        assert parse_json_path(source).source == source

    def test_segments_expose_kinds(self):
        parsed = parse_json_path("$.data[*][?@.ok].url[2]")
        assert [segment.kind for segment in parsed.segments] == [
            SegmentKind.NAME,
            SegmentKind.WILDCARD,
            SegmentKind.FILTER,
            SegmentKind.NAME,
            SegmentKind.INDEX,
        ]

    def test_wildcard_is_reported(self):
        assert parse_json_path("$.outputs[*].url").has_wildcard
        assert not parse_json_path("$.outputs[0].url").has_wildcard


class TestRejectedConstructs:
    @pytest.mark.parametrize(
        ("source", "code"),
        [
            ("$..url", DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT),
            ("$.data[?@..url]", DefinitionErrorCode.JSONPATH_RECURSIVE_DESCENT),
            ("$['a','b']", DefinitionErrorCode.JSONPATH_UNION),
            ("$[0,1]", DefinitionErrorCode.JSONPATH_UNION),
            ("$.data[0:10:2]", DefinitionErrorCode.JSONPATH_SLICE_STEP),
            ("$.data[?length(@.url) > 0]", DefinitionErrorCode.JSONPATH_FUNCTION_EXTENSION),
            ("$.data[?count(@.url) > 0]", DefinitionErrorCode.JSONPATH_FUNCTION_EXTENSION),
            ("$.data[?@.id == $.want]", DefinitionErrorCode.JSONPATH_FILTER_ROOT_REFERENCE),
            ("$.data[?@.*]", DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR),
            ("$.data[?@['a'][*]]", DefinitionErrorCode.JSONPATH_FILTER_NON_SINGULAR),
            ("$.data[?@.url =~ 'mp4']", DefinitionErrorCode.JSONPATH_REGEX_OPERATOR),
            ("video_url", DefinitionErrorCode.JSONPATH_MISSING_ROOT),
            (" $.url", DefinitionErrorCode.JSONPATH_SURROUNDING_WHITESPACE),
            ("$.data[", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$.data.", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$data", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$.data[01]", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$.data[?@.size == 01]", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$.data[?!@.ready == true]", DefinitionErrorCode.JSONPATH_SYNTAX),
            ("$.data[?@.name == 'a\\\"b']", DefinitionErrorCode.JSONPATH_SYNTAX),
        ],
    )
    def test_forbidden_form_carries_its_code(self, source: str, code: DefinitionErrorCode):
        with pytest.raises(JsonPathSubsetError) as excinfo:
            parse_json_path(source)
        assert excinfo.value.code is code
        assert excinfo.value.source == source

    def test_position_counts_characters_from_one(self):
        with pytest.raises(JsonPathSubsetError) as excinfo:
            parse_json_path("$..url")
        assert excinfo.value.position == 2

    def test_non_string_is_rejected(self):
        with pytest.raises(JsonPathSubsetError) as excinfo:
            parse_json_path(["$.url"])
        assert excinfo.value.code is DefinitionErrorCode.JSONPATH_NOT_A_STRING
