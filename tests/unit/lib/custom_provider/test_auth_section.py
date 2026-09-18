"""``auth`` 节的共用检查与渲染：两种 kind 共读一份，凭证只从占位符来。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.custom_provider.auth_section import check_auth_section, render_auth
from lib.custom_provider.definition_diagnostics import DefinitionErrorCode, DefinitionIssue
from lib.custom_provider.endpoint_definition import validate_definition
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition


def _api_key_only(path: str, name: str) -> list[DefinitionIssue]:
    """ComfyUI 那一侧的变量作用域：``api_key`` 以外一概不认。"""
    return [DefinitionIssue(path, DefinitionErrorCode.UNDECLARED_VARIABLE, {"name": name})]


def _codes(auth: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues = check_auth_section(auth, variable_issues=_api_key_only)
    return [issue.code.value for issue in issues.errors], [issue.code.value for issue in issues.warnings]


class TestSharedChecks:
    def test_an_empty_section_has_nothing_to_say(self):
        assert _codes({}) == ([], [])

    def test_two_empty_tables_are_the_same_as_no_section_at_all(self):
        """``{"headers": {}}`` 一个头都不会发出去，与整节缺席等效；两种 kind 在这里判得一样。"""
        assert _codes({"headers": {}, "query": {}}) == ([], [])

    def test_a_section_that_never_references_the_api_key_is_refused(self):
        errors, _ = _codes({"headers": {"X-Token": "写死的值"}})

        assert errors == ["auth_without_api_key"]

    def test_a_broken_placeholder_is_reported_even_when_a_good_one_sits_beside_it(self):
        errors, _ = _codes({"headers": {"Authorization": "{{api_key}}-{{ api_key | upper }}"}})

        assert errors == ["malformed_placeholder"]

    def test_a_literal_credential_is_a_warning_not_an_error(self):
        errors, warnings = _codes({"headers": {"A": "{{ api_key }}", "B": "sk-9f2c41ab77de05631b8a"}})

        assert errors == []
        assert warnings == ["auth_literal_credential"]

    def test_header_names_are_compared_case_insensitively(self):
        errors, _ = _codes({"headers": {"Authorization": "{{ api_key }}", "authorization": "{{ api_key }}"}})

        assert errors == ["header_name_duplicate"]

    def test_a_dotted_header_name_keeps_its_whole_name(self):
        """定位路径按点号拼，从末段反推键名会把 ``X-Trace.Id`` 截成 ``Id``。"""
        headers, _query = render_auth({"headers": {"X-Trace.Id": "{{ api_key }}"}}, api_key="k-1")

        assert headers == {"X-Trace.Id": "k-1"}


class TestRender:
    def test_the_api_key_fills_every_template_in_both_tables(self):
        headers, query = render_auth(
            {"headers": {"Authorization": "Bearer {{ api_key }}"}, "query": {"token": "{{api_key}}"}},
            api_key="k-1",
        )

        assert headers == {"Authorization": "Bearer k-1"}
        assert query == {"token": "k-1"}

    def test_an_empty_api_key_renders_nothing_at_all(self):
        """空凭证发出去会被反代以 401 拒掉一个本该放行的请求。"""
        assert render_auth({"headers": {"Authorization": "Bearer {{ api_key }}"}}, api_key="") == ({}, {})


class TestBothKindsShareIt:
    @pytest.mark.parametrize(
        ("factory", "auth"),
        [
            (custom_endpoint_definition, {"headers": {"Authorization": "Bearer {{ api_key }}", "authorization": "x"}}),
            (comfyui_endpoint_definition, {"headers": {"Authorization": "Bearer {{ api_key }}", "authorization": "x"}}),
        ],
    )
    def test_the_same_broken_auth_is_refused_on_both_kinds(self, factory: Any, auth: dict[str, Any]):
        """同一份 auth 写法在两种 kind 上必须得到同一个判定——分成两份实现就会各自漂移。"""
        diagnostics = validate_definition(factory(auth=auth))

        assert "header_name_duplicate" in [issue.code.value for issue in diagnostics.errors]
