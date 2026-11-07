"""text_utils: strip_json_code_fences covers case variants, bare JSON, lone opening/closing fences, whitespace; normalize_newlines covers three line endings."""

import json

import pytest

from lib.infra.text_utils import normalize_newlines, strip_json_code_fences


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
    """Stripped text must json.loads into equivalent objects."""
    assert json.loads(strip_json_code_fences(raw)) == {"a": 1}


def test_only_opening_fence() -> None:
    """Opening fence only: removing its prefix leaves parseable JSON."""
    assert json.loads(strip_json_code_fences('```json\n{"a": 1}')) == {"a": 1}


def test_only_opening_fence_no_language() -> None:
    """Opening fence only without language annotations."""
    assert json.loads(strip_json_code_fences('```\n{"a": 1}')) == {"a": 1}


def test_only_closing_fence() -> None:
    """Closing fence only: removing trailing ``` leaves parseable JSON."""
    assert json.loads(strip_json_code_fences('{"a": 1}\n```')) == {"a": 1}


def test_bare_json_untouched() -> None:
    """Bare JSON without fences only strips outer whitespace, preserving content."""
    assert strip_json_code_fences('  {"a": 1}  ') == '{"a": 1}'


def test_case_insensitive_opening_marker() -> None:
    """Case-varied opening fences strip seven-character prefixes without leaking language annotations into bodies."""
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
