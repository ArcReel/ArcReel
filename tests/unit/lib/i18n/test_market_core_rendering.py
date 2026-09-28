"""arcreel-market-core 的诊断经应用翻译目录渲染：嵌套翻译键、约束细节与抓取失败原因按请求语言成文。"""

from __future__ import annotations

import pytest

from arcreel_market_core.endpoint_definition import JsonPathEvaluationError, extract_value, validate_definition
from arcreel_market_core.market import MarketIssue, MarketIssueCode
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition, make_translator


class TestDefinitionDiagnostics:
    def test_payload_message_is_prose_in_the_requested_locale(self):
        definition = custom_endpoint_definition()
        definition["submit"]["body"]["api_key"] = "{{ api_key }}"

        payload = validate_definition(definition).to_payload(make_translator("en"))

        assert payload["errors"][0]["code"] == "api_key_outside_auth"
        assert "auth" in payload["errors"][0]["message"]

    def test_removed_field_message_embeds_the_translated_reason(self):
        definition = custom_endpoint_definition()
        definition["poll"]["interval_seconds"] = 5

        payload = validate_definition(definition).to_payload(make_translator("zh"))

        assert "运行时策略" in payload["errors"][0]["message"]

    def test_comfyui_removed_section_embeds_the_translated_reason(self):
        definition = comfyui_endpoint_definition(capabilities={"first_frame": True})

        payload = validate_definition(definition).to_payload(make_translator("zh"))

        assert "节点绑定" in payload["errors"][0]["message"]

    @pytest.mark.parametrize(
        ("locale", "expected"),
        [
            ("zh", "取值不符合格式约定：不符合要求的格式：\\S"),
            ("en", "Value does not match the format: Required format not matched: \\S"),
            ("vi", "Giá trị không đúng định dạng: Không khớp định dạng bắt buộc: \\S"),
        ],
    )
    def test_jsonschema_detail_is_rendered_in_the_requested_locale(self, locale: str, expected: str):
        definition = custom_endpoint_definition()
        definition["submit"]["url"] = " "

        payload = validate_definition(definition).to_payload(make_translator(locale))

        assert payload["errors"][0]["message"] == expected

    @pytest.mark.parametrize(
        ("locale", "expected"),
        [
            ("zh", "不符合定义格式：取值落入了禁止范围"),
            ("en", "Does not match the definition format: The value matches a forbidden constraint"),
            ("vi", "Không khớp định dạng định nghĩa: Giá trị thuộc phạm vi bị cấm"),
        ],
    )
    def test_jsonschema_fallback_detail_is_rendered_in_the_requested_locale(self, locale: str, expected: str):
        definition = custom_endpoint_definition()
        definition["schema_version"] = "1.0.0\n"

        payload = validate_definition(definition).to_payload(make_translator(locale))

        assert payload["errors"][0]["code"] == "schema_violation"
        assert payload["errors"][0]["message"] == expected

    def test_jsonpath_evaluation_failure_reads_as_prose(self):
        with pytest.raises(JsonPathEvaluationError) as caught:
            extract_value(["$[?@.a == 1e400]"], {"a": 1})

        assert caught.value.message.render(make_translator("en")) == (
            "Could not evaluate extraction path: $[?@.a == 1e400]"
        )


def test_market_issue_renders_as_a_located_prose_line():
    issue = MarketIssue(
        "arcreel-market.json",
        "entries[0].version",
        MarketIssueCode.PROJECTION_MISMATCH,
        {"field": "version", "index_value": "9.9.9", "definition_value": "1.0.0"},
    )

    assert issue.render(make_translator("en")) == (
        'arcreel-market.json:entries[0].version: [projection_mismatch] Index field version is "9.9.9", '
        'which differs from "1.0.0" in the definition meta'
    )
