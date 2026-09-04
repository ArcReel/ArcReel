"""text_utils 单元测试：strip_json_code_fences 覆盖大小写变体、裸 JSON、仅开/闭栏、空白等输入；normalize_newlines 覆盖三种换行。"""

import json

import pytest

from lib.text_utils import normalize_newlines, strip_json_code_fences


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '```Json\n{"a": 1}\n```',
        '```jSoN\n{"a": 1}\n```',
        '``` json\n{"a": 1}\n```',
        '```  JSON\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '{"a": 1}',
        '  ```json\n{"a": 1}\n```  ',
        '\n\n```json\n{"a": 1}\n```\n\n',
    ],
)
def test_stripped_output_parses_as_json(raw: str) -> None:
    """剥离后必须能被 json.loads 正确解析为等价对象。"""
    assert json.loads(strip_json_code_fences(raw)) == {"a": 1}


def test_only_opening_fence() -> None:
    """仅含开栏标记：去掉开栏前缀后可解析。"""
    assert json.loads(strip_json_code_fences('```json\n{"a": 1}')) == {"a": 1}


def test_only_opening_fence_no_language() -> None:
    """仅含无语言标注的开栏标记。"""
    assert json.loads(strip_json_code_fences('```\n{"a": 1}')) == {"a": 1}


def test_only_closing_fence() -> None:
    """仅含闭栏标记：去掉尾部 ``` 后可解析。"""
    assert json.loads(strip_json_code_fences('{"a": 1}\n```')) == {"a": 1}


def test_bare_json_untouched() -> None:
    """无栅栏的裸 JSON 仅做两端 strip，内容不变。"""
    assert strip_json_code_fences('  {"a": 1}  ') == '{"a": 1}'


def test_case_insensitive_opening_marker() -> None:
    """大小写变体开栏标记均剥离 7 字前缀，不把语言标注残留进正文。"""
    assert strip_json_code_fences('```JSON\n{"a": 1}\n```').startswith("{")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\r\nb\r\n", "a\nb\n"),
        ("a\rb\r", "a\nb\n"),
        ("a\nb\n", "a\nb\n"),
        ("a\r\n\rb", "a\n\nb"),
    ],
)
def test_normalize_newlines_unifies_every_line_ending(raw: str, expected: str) -> None:
    assert normalize_newlines(raw) == expected
