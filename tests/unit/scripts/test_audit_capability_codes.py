import ast

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
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
        ('ImageCapabilityError("image_endpoint_mismatch_no_t2i")', ["image_endpoint_mismatch_no_t2i"], 0),
        ('ImageCapabilityError(code="image_endpoint_mismatch_no_t2i")', ["image_endpoint_mismatch_no_t2i"], 0),
        ("ImageCapabilityError(code=chosen)", [], 1),
        ("VideoCapabilityError(code=chosen)", [], 1),
        ('ReferencePayloadFloorError(**{"code": "new_code"})', ["new_code"], 0),
        ("VideoCapabilityError(**overrides)", [], 1),
        ('VideoCapabilityError(**{"code": "known", **overrides})', [], 1),
        ("ReferencePayloadFloorError()", ["ref_payload_floor_exceeded"], 0),
        ('ReferencePayloadFloorError(code="new_code")', ["new_code"], 0),
        ("ReferencePayloadFloorError(code=chosen)", [], 1),
    ],
)
def test_scanner_reads_code_from_keyword_form(source, expected_codes, expected_dynamic):
    """``code=`` 关键字写法与位置参数同等对待，非字面量一律 fail-closed。

    只看位置参数会让 ``ReferencePayloadFloorError(code="new_code")`` 被登记成默认码：
    真实的新 code 既不进已发现集合也不记为动态点，两道漂移守卫一起放行。
    """
    sites = scan_tree(ast.parse(source), "synthetic.py")

    assert sorted(sites.codes) == sorted(expected_codes)
    assert len(sites.dynamic) == expected_dynamic


@pytest.mark.parametrize(
    ("setup_line", "call"),
    [
        ("from arcreel_market_core.video_backend_contract import VideoCapabilityError as VCE", 'VCE("new_code")'),
        ("import arcreel_market_core.video_backend_contract as base", 'base.VideoCapabilityError("new_code")'),
        ("from lib.backends.image_backends.base import ImageCapabilityError as ICE", 'ICE("new_code")'),
        ("import lib.backends.image_backends.base as img_base", 'img_base.ImageCapabilityError("new_code")'),
        (
            "from lib.references.reference_compression import ReferencePayloadFloorError as RPFE",
            'RPFE(code="new_code")',
        ),
        ("Err = ImageCapabilityError", 'Err("new_code")'),
        (
            "from arcreel_market_core.video_backend_contract import VideoCapabilityError as VCE\nErr = VCE",
            'Err("new_code")',
        ),
    ],
)
def test_scanner_resolves_import_and_local_aliases(setup_line, call):
    """经导入别名、属性访问或局部变量别名到达的能力异常构造点都应被扫到。"""
    sites = scan_tree(ast.parse(f"{setup_line}\n{call}\n"), "synthetic.py")

    assert sorted(sites.codes) == ["new_code"]
    assert sites.dynamic == []


@pytest.mark.parametrize(
    ("snippet", "expected_error"),
    [
        (
            "VideoCapabilityError('unregistered_direct_video')\n",
            "server/probe.py:1 未登记错误码 unregistered_direct_video",
        ),
        (
            "ImageCapabilityError('unregistered_direct_image')\n",
            "server/probe.py:1 未登记错误码 unregistered_direct_image",
        ),
        (
            "ReferencePayloadFloorError('unregistered_floor')\n",
            "server/probe.py:1 未登记错误码 unregistered_floor",
        ),
        (
            "import errors as base\nbase.VideoCapabilityError('unregistered_attr')\n",
            "server/probe.py:2 未登记错误码 unregistered_attr",
        ),
        (
            "from errors import ＶideoCapabilityError as VCE\nVCE('unregistered_nfkc_alias')\n",
            "server/probe.py:2 未登记错误码 unregistered_nfkc_alias",
        ),
        (
            "Err = ImageCapabilityError\nErr('unregistered_local_alias')\n",
            "server/probe.py:2 未登记错误码 unregistered_local_alias",
        ),
        (
            "[label, (Err,)] = ('video', [VideoCapabilityError])\nErr('unregistered_unpacked')\n",
            "server/probe.py:2 未登记错误码 unregistered_unpacked",
        ),
    ],
)
def test_audit_catches_every_syntax_form_reaching_capability_exceptions(tmp_path, snippet, expected_error):
    source = tmp_path / "server"
    source.mkdir()
    (source / "probe.py").write_text(snippet, encoding="utf-8")

    errors = audit(tmp_path)

    assert expected_error in errors


def test_audit_reports_dynamic_and_missing_params_across_all_supported_locales(tmp_path):
    source = tmp_path / "server"
    source.mkdir()
    (source / "probe.py").write_text(
        "from errors import ＶideoCapabilityError as VCE\n"
        "VCE(chosen)\n"
        "VCE('video_duration_not_supported', duration=7)\n",
        encoding="utf-8",
    )

    errors = audit(tmp_path)

    assert "server/probe.py:2 未允许的动态错误码 (server/probe.py::<module>)" in errors
    for locale in SUPPORTED_LOCALES:
        assert f"server/probe.py:3 (video_duration_not_supported, {locale}) 缺参数 ['supported']" in errors


def test_unrelated_error_code_keywords_are_not_construction_sites():
    tree = ast.parse("VideoCapabilityError(code)\ndict(error_code='provider_timeout')")
    sites = scan_tree(tree, "provider.py")
    assert sites.codes == {}
    assert sites.dynamic == [("provider.py:1", "provider.py::<module>")]


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ("**{}", True),
        ("**{'supported': '5, 10'}", False),
        ("**{**{'supported': '5, 10'}}", False),
        ("**extra", False),
    ],
)
def test_template_checks_known_unpacked_keys(kwargs, missing):
    tree = ast.parse(f"VideoCapabilityError('video_duration_not_supported', duration=7, {kwargs})")
    sites = scan_tree(tree, "provider.py")
    assert len(sites.missing_params) == (len(SUPPORTED_LOCALES) if missing else 0)


def test_template_checks_nested_format_fields(monkeypatch):
    monkeypatch.setitem(MESSAGES["en"], "video_duration_invalid", "{duration:>{width}}")
    tree = ast.parse("VideoCapabilityError('video_duration_invalid', duration=7)")
    sites = scan_tree(tree, "provider.py")
    assert sites.missing_params == ["provider.py:1 (video_duration_invalid, en) 缺参数 ['width']"]


def test_constructor_aliases_follow_function_scope():
    tree = ast.parse(
        "from errors import VideoCapabilityError as Imported\n"
        "def fail():\n"
        "    Err = Imported\n"
        "    def nested():\n"
        "        Err('real_code')\n"
        "def read():\n"
        "    def Err(value): return value\n"
        "    Err('unrelated')\n"
        "def shadow(Imported):\n"
        "    Imported('also_unrelated')\n"
    )
    assert set(scan_tree(tree, "provider.py").codes) == {"real_code"}


@pytest.mark.parametrize(
    "binding",
    [
        "if flag:\n    Err = VideoCapabilityError\nelse:\n    Err = ValueError",
        "if flag:\n    Err = ValueError\nelse:\n    Err = VideoCapabilityError",
        "Err = VideoCapabilityError\nif flag:\n    Err = ValueError",
        "Err = VideoCapabilityError if flag else ValueError",
        "match flag:\n    case True:\n        Err = VideoCapabilityError\n    case _:\n        Err = ValueError",
    ],
)
def test_audit_rejects_constructor_alias_from_any_branch(tmp_path, binding):
    source = tmp_path / "server" / "probe.py"
    source.parent.mkdir()
    source.write_text(f"{binding}\nraise Err('unregistered_branch')\n", encoding="utf-8")
    assert f"server/probe.py:{len(binding.splitlines()) + 1} 未登记错误码 unregistered_branch" in audit(tmp_path)


def test_unrelated_branch_calls_and_overwritten_bindings_are_ignored():
    tree = ast.parse(
        "Err = ValueError\n"
        "if flag:\n"
        "    Err = VideoCapabilityError\n"
        "else:\n"
        "    Err('unrelated_in_else')\n"
        "if flag:\n"
        "    def Err(value): return value\n"
        "else:\n"
        "    Err = TypeError\n"
        "Err('unrelated_after_join')\n"
    )
    assert scan_tree(tree, "provider.py").codes == {}


def test_match_keeps_unmatched_path_and_respects_capture_bindings():
    tree = ast.parse(
        "Err = VideoCapabilityError\n"
        "match value:\n"
        "    case [Err]:\n"
        "        Err('unrelated_capture')\n"
        "Err('possible_after_match')\n"
        "match value:\n"
        "    case Err:\n"
        "        pass\n"
        "Err('unrelated_after_capture')\n"
    )
    assert set(scan_tree(tree, "provider.py").codes) == {"possible_after_match"}


def test_join_preserves_distinct_constructor_defaults():
    tree = ast.parse("Err = ReferencePayloadFloorError if flag else VideoCapabilityError\nErr()")
    sites = scan_tree(tree, "provider.py")
    assert set(sites.codes) == {"ref_payload_floor_exceeded"}
    assert sites.dynamic == [("provider.py:2", "provider.py::<module>")]


@pytest.mark.parametrize(
    "snippet",
    [
        "Err = VideoCapabilityError\ndef fail(): Err('unregistered_flow')\nfail()\nErr = ValueError",
        "def fail(): Err('unregistered_flow')\nErr = VideoCapabilityError\nfail()",
        "Err = VideoCapabilityError\nfor item in items: Err = ValueError\nErr('unregistered_flow')",
        "async def run():\n    Err = VideoCapabilityError\n    async for item in items: Err = ValueError\n    Err('unregistered_flow')",
        "Err = VideoCapabilityError\nwhile flag: Err = ValueError\nErr('unregistered_flow')",
        "Err = ValueError\nwhile flag:\n    Err('unregistered_flow')\n    Err = VideoCapabilityError\n    continue",
        "Err = ValueError\nfor item in items:\n    Err = VideoCapabilityError\n    break\nelse: Err = ValueError\nErr('unregistered_flow')",
        "Err = VideoCapabilityError\nErr = Err('unregistered_flow')",
        "Err = VideoCapabilityError\nErr: Exception = Err('unregistered_flow')",
        "Err = VideoCapabilityError\nitems[Err('unregistered_flow')] = data",
        "try: Err = VideoCapabilityError\nexcept ValueError: Err = ValueError\nErr('unregistered_flow')",
        "try:\n    Err = VideoCapabilityError\n    work()\n    Err = ValueError\nexcept ValueError: Err('unregistered_flow')",
        "try: work()\nexcept* ValueError: Err = VideoCapabilityError\nelse: Err = ValueError\nfinally: Err('unregistered_flow')",
    ],
)
def test_audit_rejects_unregistered_code_on_reachable_path(tmp_path, snippet):
    source = tmp_path / "server" / "probe.py"
    source.parent.mkdir()
    source.write_text(snippet, encoding="utf-8")
    line = next(i for i, text in enumerate(snippet.splitlines(), 1) if "'unregistered_flow'" in text)
    assert f"server/probe.py:{line} 未登记错误码 unregistered_flow" in audit(tmp_path)


def test_finally_and_exhausted_loop_else_replace_bindings():
    tree = ast.parse(
        "Err = VideoCapabilityError\n"
        "for item in items: pass\n"
        "else: Err = ValueError\n"
        "Err('ordinary_loop')\n"
        "try: Err = VideoCapabilityError\n"
        "finally: Err = ValueError\n"
        "Err('ordinary_finally')\n"
    )
    assert scan_tree(tree, "provider.py").codes == {}


def test_loop_revisits_count_each_dynamic_call_site_once():
    tree = ast.parse("Err = VideoCapabilityError\nwhile flag:\n    Err(code)\n    Other = Err\n    Other(code)")
    assert scan_tree(tree, "provider.py").dynamic == [
        ("provider.py:3", "provider.py::<module>"),
        ("provider.py:5", "provider.py::<module>"),
    ]
