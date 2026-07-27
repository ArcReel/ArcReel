"""能力类失败原因：落库存 code+params，读侧按语言渲染。

守住两件事：
1. `CAPABILITY_FAILURE_CODES` 覆盖代码里全部 capability-error raise 点（AST 扫描，防漂移）；
2. 同一条落库 error_message 在 zh/en/vi 下渲染成各自语言。
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from lib import task_failure
from lib.generation_worker import _encode_task_failure_message
from lib.i18n import MESSAGES
from lib.i18n import _ as i18n_translate
from lib.image_backends.base import ImageCapabilityError
from lib.reference_compression import ReferencePayloadFloorError
from lib.task_failure import (
    CAPABILITY_FAILURE_CODES,
    FAILURE_CODE_KEYS,
    bound_reason,
    encode_failure,
    render_failure,
)
from lib.video_backends.base import VideoCapabilityError

# AST 守卫扫的是真实源码树、渲染断言用的是真实 i18n 目录，不 mock 任何被测入口。
pytestmark = pytest.mark.integration

_CAPABILITY_EXCEPTIONS = {"ImageCapabilityError", "VideoCapabilityError", "ReferencePayloadFloorError"}
_SCANNED_ROOTS = ("lib", "server")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _exception_name(func: ast.expr) -> str | None:
    """取构造表达式的类名，`VideoCapabilityError(...)` 与 `base.VideoCapabilityError(...)` 同看待。"""
    if isinstance(func, ast.Name):
        return func.id if func.id in _CAPABILITY_EXCEPTIONS else None
    if isinstance(func, ast.Attribute):
        return func.attr if func.attr in _CAPABILITY_EXCEPTIONS else None
    return None


def _enclosing_function(tree: ast.Module, lineno: int) -> str:
    """取包住 ``lineno`` 的最内层函数名，不在函数体内时返回 ``<module>``。"""
    innermost = "<module>"
    best_start = -1
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        if node.lineno <= lineno <= end and node.lineno > best_start:
            innermost = node.name
            best_start = node.lineno
    return innermost


def _static_str_choices(node: ast.expr) -> list[str] | None:
    """穷举表达式可能取到的 code 字面量，穷举不全时返回 ``None``。

    只认字符串常量与条件表达式（``"a" if cond else "b"``）——后者两个分支都是字面量时才算
    静态可枚举。``ast.walk`` 那种「找到任一字符串常量就当字面量」的宽松写法在
    ``"lit" if cond else dynamic_code`` 上会 fail-open：动态分支产出的未登记 code 两道
    守卫都拦不住，最终以裸机器码落到界面上。
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else None
    if isinstance(node, ast.IfExp):
        body = _static_str_choices(node.body)
        orelse = _static_str_choices(node.orelse)
        if body is None or orelse is None:
            return None
        return body + orelse
    return None


def _keyword_value(node: ast.Call, name: str) -> ast.expr | None:
    """取调用的 ``name=`` 实参表达式，没有则返回 ``None``。"""
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _scan_tree(tree: ast.Module, rel: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """扫描单棵语法树里的 capability-error 构造点。

    返回 (code -> 首个出处, 无法静态求值 code 的构造点列表)。覆盖两种写法：构造点直接给 code
    字面量（位置参数或 ``code=`` 关键字，含 ``"a" if cond else "b"`` 这类条件选码），以及
    agnes 那样把 code 经 ``error_code=`` 关键字透传给内部 helper（此时构造点的 code 是变量，
    字面量在 helper 调用方）。
    """
    codes: dict[str, str] = {}
    dynamic_sites: list[tuple[str, str]] = []
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    constructions = [(node, name) for node in calls if (name := _exception_name(node.func)) is not None]
    if not constructions:
        return codes, dynamic_sites

    for node, name in constructions:
        where = f"{rel}:{node.lineno}"
        # 三个能力异常的首参都叫 ``code``，位置与关键字两种写法都合法。只看位置参数会让
        # ``ReferencePayloadFloorError(code="new")`` 落进「无实参」分支被登记成默认码，
        # 真正的 new code 既不入集合也不记为动态点，两道守卫一起失效。
        code_expr = node.args[0] if node.args else _keyword_value(node, "code")
        if code_expr is None:
            # ``**kwargs`` 解包里可能藏着 code，静态看不见。此时不能当「没给 code」处理：
            # ``ReferencePayloadFloorError(**{"code": "new"})`` 会被登记成默认码，真正的
            # new code 两道守卫一起放行。无位置参数也无显式 ``code=`` 时，只有确定没有解包
            # 才敢认默认码。
            if any(keyword.arg is None for keyword in node.keywords):
                dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
            elif name == "ReferencePayloadFloorError":
                # 唯一带默认 code 的能力异常（构造点常省略实参）。
                codes.setdefault(ReferencePayloadFloorError().code, where)
            else:
                dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
            continue
        literals = _static_str_choices(code_expr)
        if literals is not None:
            for literal in literals:
                codes.setdefault(literal, where)
        else:
            # code 来自变量 / 模块常量，或条件表达式里混了动态分支：静态枚举不全，
            # 登记漂移会漏网。记下来单独断言。
            dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))

    # ``error_code=`` 只在确有 capability 构造点的文件里采集，避免同名 kwarg 的
    # 无关模块被误当 capability code 报「未登记」假阳性。非字面量的 error_code
    # 同样记成不可扫描点：helper 内部的构造点已按 scope 豁免，调用方再静默放过
    # 就两道守卫全失效，未登记 code 会一路落到用户可见的裸机器码。
    for node in calls:
        error_code = _keyword_value(node, "error_code")
        if error_code is None:
            continue
        literals = _static_str_choices(error_code)
        if literals is not None:
            for literal in literals:
                codes.setdefault(literal, f"{rel}:{node.lineno}")
        else:
            dynamic_sites.append((f"{rel}:{node.lineno}", f"{rel}::{_enclosing_function(tree, node.lineno)}"))
    return codes, dynamic_sites


def _scan_raised_codes() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """扫描 lib/ + server/ 里全部 capability-error 构造点。"""
    codes: dict[str, str] = {}
    dynamic_sites: list[tuple[str, str]] = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found, dynamic = _scan_tree(tree, str(path.relative_to(_REPO_ROOT)))
            for code, where in found.items():
                codes.setdefault(code, where)
            dynamic_sites.extend(dynamic)
    return codes, dynamic_sites


# code 由变量/形参传入、静态扫不出字面量的构造点，按「文件::所在函数」豁免（不按行号，
# 行号会随无关改动漂移；也不按文件，否则同文件里任意新增的动态构造点都会被一并放过）。
# 仅允许「同文件内由 ``error_code=`` 字面量喂入的 helper」这一种形态——helper 自身扫不出，
# 但它的每个调用方都被上面的 kwarg 采集覆盖。值是该 scope 允许的动态构造点数量：豁免是
# 定额而非空白支票，函数内再多一个动态构造点就得重新自证，不会被顺带放行。
_ALLOWED_DYNAMIC_SCOPES = {"lib/video_backends/agnes.py::_encode_image": 2}


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
        # ``**`` 解包里可能藏着 code，静态看不见：不能当「没给 code」而登记默认码。
        ('ReferencePayloadFloorError(**{"code": "new_code"})', [], 1),
        ("VideoCapabilityError(**overrides)", [], 1),
        # 默认 code 只在真的没给 code 时才算数：给了关键字就按关键字扫。
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
    # ``error_code=`` 仅在确有 capability 构造点的文件里采集，合成源码补一个构造点作陪衬。
    # 陪衬的 code 与各用例都不重合，且断言取全等而非差集——否则被测 code 与陪衬同名时，
    # 两边一起减掉会让「一条都没采集到」也照样通过。
    prelude = 'VideoCapabilityError("video_duration_invalid")\n'
    codes, dynamic = _scan_tree(ast.parse(prelude + source), "synthetic.py")

    assert sorted(codes) == sorted({"video_duration_invalid", *expected_codes})
    assert len(dynamic) == expected_dynamic


def test_capability_codes_registered_no_drift():
    """新增 capability code 未登记时在 CI 失败，而不是等到线上落库时才 KeyError 降级。"""
    raised, _ = _scan_raised_codes()
    unregistered = {code: where for code, where in raised.items() if code not in CAPABILITY_FAILURE_CODES}
    assert not unregistered, f"以下 capability code 未登记进 CAPABILITY_FAILURE_CODES: {unregistered}"

    stale = CAPABILITY_FAILURE_CODES - raised.keys()
    assert not stale, f"以下已登记 code 在代码里已无 raise 点，应清理: {sorted(stale)}"


def test_no_unscannable_capability_construction_sites():
    """构造点的 code 必须能静态扫出，否则漂移守卫会 fail-open 地放过新 code。

    新出现的动态写法要么改成字面量，要么显式进白名单并自证 code 已登记。
    """
    _, dynamic_sites = _scan_raised_codes()
    unexpected = sorted({where for where, scope in dynamic_sites if scope not in _ALLOWED_DYNAMIC_SCOPES})
    assert not unexpected, f"以下构造点的 code 非字面量，漂移守卫扫不到: {unexpected}"

    counted = Counter(scope for _, scope in dynamic_sites)
    assert counted == Counter(_ALLOWED_DYNAMIC_SCOPES), (
        f"白名单 scope 的动态构造点数量与定额不符，新增的那个未自证 code 已登记: {counted}"
    )


@pytest.mark.parametrize("code", sorted(CAPABILITY_FAILURE_CODES))
def test_capability_code_has_all_three_locales(code):
    """每个 code 就是 errors 目录的 key（identity 映射），三语都必须有模板。"""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def _translator(locale: str):
    """与 ``lib.i18n.get_translator`` 同构：把 locale 绑进去，读侧就是这么调的。"""

    def translate(key: str, **params) -> str:
        return i18n_translate(key, locale=locale, **params)

    return translate


def test_stored_reason_renders_per_locale():
    """同一条落库 error_message 在三语下渲染成三种文案。"""
    stored = _encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    assert stored == '[video_duration_not_supported] {"duration": 7, "supported": "5, 10"}'

    for locale in ("zh", "en", "vi"):
        # 期望值独立取自该 locale 的模板目录，而非拿渲染结果互相比对——只断言"三者不同"
        # 的话，zh/en/vi 模板或 locale 映射被互换仍会通过。
        expected = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert render_failure(stored, _translator(locale)) == expected


def test_encode_covers_every_capability_exception_type():
    """三类能力异常都走结构化编码，不再落成烘焙文案。"""
    cases = [
        ImageCapabilityError("image_endpoint_mismatch_no_t2i", model="gpt-image-1"),
        VideoCapabilityError("video_start_image_unreadable", model="kling-v1", name="a.png"),
        ReferencePayloadFloorError(),
    ]
    for exc in cases:
        stored = _encode_task_failure_message(exc)
        assert stored.startswith(f"[{exc.code}]")
        assert render_failure(stored, _translator("en")) != stored


def test_encode_stringifies_non_json_params():
    """params 值不是 JSON 原生类型时降级为字符串，编码不得在失败路径里抛错。

    ``video_duration_invalid`` 回显的是调用方拒绝的原始值，类型不可控。
    """
    stored = _encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=Path("weird")))
    assert json.loads(stored.split("] ", 1)[1]) == {"duration": "weird"}


def test_unregistered_capability_code_degrades_to_passthrough_text():
    """code 未登记时退回非结构化文本，读侧原样透传——失败原因不丢，任务不卡 running。"""
    exc = VideoCapabilityError("video_totally_new_code", model="x")
    stored = _encode_task_failure_message(exc)
    assert stored == "video_totally_new_code"
    assert render_failure(stored, _translator("zh")) == stored


def test_non_capability_exception_still_passes_through():
    stored = _encode_task_failure_message(RuntimeError("provider socket closed"))
    assert stored == "provider socket closed"
    assert render_failure(stored, _translator("en")) == stored


@pytest.mark.parametrize(
    "legacy",
    [
        None,
        "",
        "<AioRpcError of RPC that terminated with:\n\tstatus = StatusCode.OUT_OF_RANGE>",
        "不支持的资源类型: reference_videos",
        "[video_duration_not_supported] not-json",
        "[some_retired_code] {}",
    ],
)
def test_legacy_rows_render_without_error(legacy):
    """存量行（空值 / 纯文本 / 无法解析的伪结构化）读取不炸，一律原样返回。"""
    assert render_failure(legacy, _translator("en")) == legacy


def _encode_cascade(dependency_task_id: str, reason: str) -> str:
    return encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)


def test_cascade_reason_renders_upstream_reason_per_locale():
    """被上游连坐的任务不能退化成裸 [code]：级联包裹与其内层原因都按读侧语言渲染。"""
    upstream = _encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    stored = _encode_cascade("5a38f38e", upstream)

    rendered = {locale: render_failure(stored, _translator(locale)) for locale in ("zh", "en", "vi")}
    assert len({*rendered.values()}) == 3, rendered
    for locale, text in rendered.items():
        assert "video_duration_not_supported" not in text
        assert "5a38f38e" in text
        inner = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert inner in text


def test_cascade_reason_with_unstructured_upstream_still_renders():
    """上游原因是 provider 原始异常文本时，包裹层仍本地化，内层原样透传。"""
    stored = _encode_cascade("5a38f38e", "provider rejected")
    rendered = render_failure(stored, _translator("zh"))
    assert rendered != stored
    assert "provider rejected" in (rendered or "")


def test_render_degrades_when_json_hits_recursion_limit(monkeypatch):
    """params 短而深时 ``json.loads`` 自身会撞递归上限。

    渲染发生在 HTTP 请求内，异常逸出就是 500，因此与畸形 JSON 同样处置：原样透传，原因不丢。
    """
    stored = '[video_reference_images_unreadable] {"names": [[[1]]]}'

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert render_failure(stored, _translator("en")) == stored


def test_bound_reason_shrinks_oversized_non_string_params():
    """非字符串参数撑爆预算时也要留下可解析的信封。

    ``video_duration_invalid`` 回显调用方拒绝的原始配置值，导入或手工编辑的 project.json
    能把它写成一个大数组；JSON 原生类型不经 ``default=str``，没有字符串参数可收窄时旧路径
    会退到裸切片，把信封切在 JSON 中途，读侧解析失败后原样吐出整串机器码。
    """
    stored = _encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=list(range(2000))))
    assert len(stored) > 2000

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    # 仍是合法信封：读侧解析得出来，渲染成本地化文案而非裸机器码。
    rendered = render_failure(bounded, _translator("en"))
    assert rendered is not None
    assert rendered != bounded
    assert "video_duration_invalid" not in rendered


def test_bound_reason_degrades_when_param_json_hits_recursion_limit(monkeypatch):
    """嵌套过深的参数量不出长度时降级为省略号，而不是把异常抛进落库路径。"""
    stored = '[video_duration_invalid] {"duration": [[[1]]]}' + "x" * 3000

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "dumps", _boom)

    assert task_failure._as_shrinkable([[[1]]]) == "…"
    assert len(bound_reason(stored, 2000)) <= 2000
