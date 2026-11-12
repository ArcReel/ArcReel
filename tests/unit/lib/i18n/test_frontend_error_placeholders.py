"""前后端所有同名错误键自动比较占位符与数字格式。"""

import ast
import importlib
import re
import string
from pathlib import Path

import pytest

from lib.i18n import SUPPORTED_LOCALES

# 前端本地场景的同名键，不由后端错误驱动；不借本次检查改文案。
_LOCAL_KEYS = {"unauthorized", "source_conflict", "source_unsupported_format"}
_ENTRY = re.compile(r"""^\s*(["'])([\w.]+)\1:\s*((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')),?\s*$""", re.M)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_shared_error_placeholders_match(locale):
    backend = importlib.import_module(f"lib.i18n.{locale}.errors").MESSAGES
    backend = backend | importlib.import_module(f"lib.i18n.{locale}.validation").MESSAGES
    source = (Path(__file__).resolve().parents[4] / "frontend/src/i18n" / locale / "errors.ts").read_text()
    frontend = {key: ast.literal_eval(value) for _, key, value in _ENTRY.findall(source)}
    declared = {a or b for a, b in re.findall(r"""^\s*(?:["']([\w.]+)["']|([\w.]+)):\s*""", source, re.M)}
    assert set(frontend) == declared, "A frontend error entry was not parsed"
    assert frontend
    assert frontend.keys() & backend.keys() >= _LOCAL_KEYS
    for key in frontend.keys() & backend.keys() - _LOCAL_KEYS:
        expected = set()
        for _, name, spec, _ in string.Formatter().parse(backend[key]):
            if not name:
                continue
            if spec and spec != "g":
                assert re.fullmatch(r"\.\df", spec), (key, spec)
                digits = spec[1]
                expected.add((name, f"minimumFractionDigits:{digits};maximumFractionDigits:{digits}"))
            else:
                expected.add((name, ""))
        actual = {
            (name.strip(), re.sub(r"\s+", "", options or ""))
            for name, options in re.findall(r"\{\{\s*([\w.]+)(?:,\s*number(?:\((.*?)\))?)?\s*\}\}", frontend[key])
        }
        assert actual == expected, (locale, key, expected, actual)
