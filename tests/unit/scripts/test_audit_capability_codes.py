import ast

import pytest

from scripts.audit_capability_codes import _static_str_choices, audit, scan_tree


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ('"video_duration_invalid"', ["video_duration_invalid"]),
        ('"a" if cond else "b"', ["a", "b"]),
        ('"a" if cond else dynamic_code', None),
        ('dynamic_code if cond else "b"', None),
        ("dynamic_code", None),
        ('CODES["a"]', None),
        ('f"video_{suffix}"', None),
    ],
)
def test_static_code_enumeration_rejects_partially_dynamic_expressions(expr, expected):
    """只有整个表达式的取值都能穷举时才算字面量，混了动态分支一律记为不可扫描。

    条件表达式里但凡有一支是变量，动态那支产出的未登记 code 就绕过了两道漂移守卫。
    """
    assert _static_str_choices(ast.parse(expr, mode="eval").body) == expected


@pytest.mark.parametrize(
    ("source", "expected_codes", "expected_dynamic"),
    [
        ('VideoCapabilityError("video_duration_not_supported")', ["video_duration_not_supported"], 0),
        ('VideoCapabilityError(code="video_duration_not_supported")', ["video_duration_not_supported"], 0),
        ("VideoCapabilityError(code=chosen)", [], 1),
        ('ReferencePayloadFloorError(**{"code": "new_code"})', [], 1),
        ("VideoCapabilityError(**overrides)", [], 1),
        ("ReferencePayloadFloorError()", ["ref_payload_floor_exceeded"], 0),
        ('ReferencePayloadFloorError(code="new_code")', ["new_code"], 0),
        ("ReferencePayloadFloorError(code=chosen)", [], 1),
        ('_encode_image(error_code="image_capability_missing_i2i")', ["image_capability_missing_i2i"], 0),
    ],
)
def test_scanner_reads_code_from_keyword_form(source, expected_codes, expected_dynamic):
    """``code=`` 关键字写法与位置参数同等对待，非字面量一律 fail-closed。

    只看位置参数会让 ``ReferencePayloadFloorError(code="new_code")`` 被登记成默认码：
    真实的新 code 既不进已发现集合也不记为动态点，两道漂移守卫一起放行。
    """
    prelude = 'VideoCapabilityError("video_duration_invalid")\n'
    sites = scan_tree(ast.parse(prelude + source), "synthetic.py")

    assert sorted(sites.codes) == sorted({"video_duration_invalid", *expected_codes})
    assert len(sites.dynamic) == expected_dynamic


@pytest.mark.parametrize(
    ("import_line", "call"),
    [
        ("from arcreel_market_core.video_backend_contract import VideoCapabilityError as VCE", 'VCE("new_code")'),
        ("import arcreel_market_core.video_backend_contract as base", 'base.VideoCapabilityError("new_code")'),
        (
            "from lib.references.reference_compression import ReferencePayloadFloorError as RPFE",
            'RPFE(code="new_code")',
        ),
    ],
)
def test_scanner_resolves_import_aliases(import_line, call):
    """经 ``as`` 改名导入的构造点照样扫得到，否则该点两道守卫一起失效。"""
    sites = scan_tree(ast.parse(f"{import_line}\n{call}\n"), "synthetic.py")

    assert sorted(sites.codes) == ["new_code"]
    assert sites.dynamic == []


def test_audit_reports_each_violation_in_aliased_constructions(tmp_path):
    source = tmp_path / "server"
    source.mkdir()
    (source / "probe.py").write_text(
        "from errors import ＶideoCapabilityError as VCE\n"
        "VCE('unregistered_probe')\n"
        "VCE(chosen)\n"
        "VCE('video_duration_not_supported', duration=7)\n",
        encoding="utf-8",
    )

    errors = audit(tmp_path)

    assert "server/probe.py:2 未登记错误码 unregistered_probe" in errors
    assert "server/probe.py:3 未允许的动态错误码 (server/probe.py::<module>)" in errors
    for locale in ("zh", "en", "vi"):
        assert f"server/probe.py:4 (video_duration_not_supported, {locale}) 缺参数 ['supported']" in errors
