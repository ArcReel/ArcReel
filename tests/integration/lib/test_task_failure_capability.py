"""能力类失败原因：落库存 code+params，读侧按语言渲染。

守住两件事：
1. `CAPABILITY_FAILURE_CODES` 覆盖代码里全部 capability-error raise 点（AST 扫描，防漂移）；
2. 同一条落库 error_message 在 zh/en/vi 下渲染成各自语言。
"""

from __future__ import annotations

import ast
import json
import string
from collections import Counter
from pathlib import Path

import pytest

from lib import task_failure
from lib.api_errors import ConflictError
from lib.config.resolver import VideoBucketCapabilityError, VideoCapability
from lib.db.repositories.task_repo import _encode_bounded_cascade_failure
from lib.generation_worker import _encode_task_failure_message
from lib.i18n import MESSAGES
from lib.i18n import _ as i18n_translate
from lib.image_backends.base import ImageCapabilityError
from lib.narration_delivery import (
    USE_TTS,
    NarratedVideoDurationBlockedError,
    NarrationDeliveryPreparation,
    NarrationTtsStatus,
    prepare_narrated_video_duration,
)
from lib.reference_compression import ReferencePayloadFloorError
from lib.reference_video.request_projection import ProjectionProblem, ReferenceProjectionBlockedError
from lib.task_failure import (
    CAPABILITY_FAILURE_CODES,
    FAILURE_CODE_KEYS,
    NARRATION_DELIVERY_FAILURE_CODES,
    REFERENCE_PROJECTION_FAILURE_CODES,
    bound_reason,
    collapse_cascade_reason,
    encode_failure,
    render_failure,
)
from lib.video_backends.base import VideoCapabilityError

# AST 守卫扫的是真实源码树、渲染断言用的是真实 i18n 目录，不 mock 任何被测入口。

_CAPABILITY_EXCEPTIONS = {"ImageCapabilityError", "VideoCapabilityError", "ReferencePayloadFloorError"}
_SCANNED_ROOTS = ("lib", "server")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """收集本模块里能力异常的导入别名，映射到原类名。

    ``from ... import VideoCapabilityError as VCE`` 后调用点只叫 ``VCE``，只比对当前名称
    会让该构造点两道守卫都扫不到——未登记 code 由 ``_try_encode_failure()`` 降级为
    ``str(exc)``，最终以裸机器码落到界面上。
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom | ast.Import):
            continue
        for alias in node.names:
            original = alias.name.rsplit(".", 1)[-1]
            if original in _CAPABILITY_EXCEPTIONS and alias.asname:
                aliases[alias.asname] = original
    return aliases


def _exception_name(func: ast.expr, aliases: dict[str, str]) -> str | None:
    """取构造表达式的类名。

    `VideoCapabilityError(...)`、`base.VideoCapabilityError(...)` 与导入别名 `VCE(...)`
    同看待——别名经 ``aliases`` 还原成原类名。
    """
    if isinstance(func, ast.Name):
        if func.id in _CAPABILITY_EXCEPTIONS:
            return func.id
        return aliases.get(func.id)
    if isinstance(func, ast.Attribute):
        if func.attr in _CAPABILITY_EXCEPTIONS:
            return func.attr
        return aliases.get(func.attr)
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
    aliases = _import_aliases(tree)
    constructions = [(node, name) for node in calls if (name := _exception_name(node.func, aliases)) is not None]
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


@pytest.mark.parametrize(
    ("import_line", "call"),
    [
        ("from lib.video_backends.base import VideoCapabilityError as VCE", 'VCE("new_code")'),
        ("import lib.video_backends.base as base", 'base.VideoCapabilityError("new_code")'),
        ("from lib.reference_compression import ReferencePayloadFloorError as RPFE", 'RPFE(code="new_code")'),
    ],
)
def test_scanner_resolves_import_aliases(import_line, call):
    """经 ``as`` 改名导入的构造点照样扫得到，否则该点两道守卫一起失效。"""
    codes, dynamic = _scan_tree(ast.parse(f"{import_line}\n{call}\n"), "synthetic.py")

    assert sorted(codes) == ["new_code"]
    assert dynamic == []


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


def _template_placeholders(template: str) -> set[str]:
    """取模板里的具名占位符集合。"""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def _scan_param_contracts() -> list[tuple[str, str, set[str]]]:
    """采集能静态看全的构造点：(出处, code, 该点显式传入的 param 名)。

    只收 code 与 kwargs 都静态可见的直接构造点。带 ``**`` 解包的调用看不全实参，纳入会产生
    假阳性；``error_code=`` 那类透传给 helper 的调用，params 由 helper 自己组装，也不在此列。
    """
    contracts: list[tuple[str, str, set[str]]] = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            rel = str(path.relative_to(_REPO_ROOT))
            tree = ast.parse(path.read_text(encoding="utf-8"))
            aliases = _import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _exception_name(node.func, aliases) is None:
                    continue
                if any(keyword.arg is None for keyword in node.keywords):
                    continue
                code_expr = node.args[0] if node.args else _keyword_value(node, "code")
                literals = _static_str_choices(code_expr) if code_expr is not None else None
                if code_expr is None:
                    literals = [ReferencePayloadFloorError().code]
                if literals is None:
                    continue
                provided = {keyword.arg for keyword in node.keywords if keyword.arg and keyword.arg != "code"}
                for literal in literals:
                    contracts.append((f"{rel}:{node.lineno}", literal, provided))
    return contracts


def test_capability_construction_sites_supply_every_template_param():
    """构造点必须给全三语模板所需的参数，否则用户会看到未替换的 ``{name}``。

    ``lib/i18n/__init__.py::_`` 在 ``format`` 抛异常时吞掉异常、返回未格式化的模板，缺参数
    不会响亮失败：读侧照常返回一段带花括号占位符的文案。code 已登记、三语模板齐全这两道守卫
    都拦不住这种漏传，因为它们只看 key 存不存在。
    """
    missing: list[str] = []
    for where, code, provided in _scan_param_contracts():
        for locale in ("zh", "en", "vi"):
            template = MESSAGES[locale].get(code)
            if template is None:
                continue  # 缺模板由 test_capability_code_has_all_three_locales 负责
            absent = _template_placeholders(template) - provided
            if absent:
                missing.append(f"{where} ({code}, {locale}) 缺参数 {sorted(absent)}")
    assert not missing, "以下构造点漏传模板参数，读侧会渲染出裸占位符:\n" + "\n".join(missing)


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


@pytest.mark.parametrize("code", sorted(REFERENCE_PROJECTION_FAILURE_CODES))
def test_projection_failure_codes_are_machine_encodable_in_all_locales(code: str):
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


@pytest.mark.parametrize(
    ("problem", "expected_params"),
    [
        (
            ProjectionProblem(
                code="reference_supported_durations_invalid",
                blocking=True,
                params=(("provider", "fake"), ("model", "bad-model")),
            ),
            {"provider": "fake", "model": "bad-model"},
        ),
        (
            ProjectionProblem(
                code="reference_asset_missing",
                blocking=True,
                params=(
                    ("missing", (("character", "张三"),)),
                    ("missing_text", "character: 张三"),
                ),
            ),
            {
                "missing": (("character", "张三"),),
                "missing_text": "character: 张三",
            },
        ),
    ],
)
def test_projection_failure_preserves_canonical_code_and_params_for_localized_tasks(
    problem: ProjectionProblem, expected_params: dict[str, object]
):
    stored = _encode_task_failure_message(ReferenceProjectionBlockedError(problem))
    assert json.loads(stored.split("] ", 1)[1]) == json.loads(json.dumps(expected_params, ensure_ascii=False))
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][problem.code].format(**expected_params)
        assert render_failure(stored, _translator(locale)) == expected


@pytest.mark.parametrize("code", sorted(NARRATION_DELIVERY_FAILURE_CODES))
def test_narration_delivery_failure_codes_are_machine_encodable_in_all_locales(code: str) -> None:
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_changed_tts_tier_worker_rejection_preserves_confirmation_coordinates() -> None:
    narration = NarrationDeliveryPreparation(
        delivery=USE_TTS,
        unit_id="E1S01",
        speech_mode=None,
        tts_status=NarrationTtsStatus.CURRENT,
        artifact_path="audio/segment_E1S01.wav",
        basis_digest="basis",
        actual_duration_seconds=9.5,
        problems=(),
    )
    preparation = prepare_narrated_video_duration(
        narration=narration,
        planned_duration_seconds=4,
        supported_durations=(4, 8, 12),
        confirmed_request_duration_seconds=8,
    )

    stored = _encode_task_failure_message(NarratedVideoDurationBlockedError(preparation))

    assert stored.startswith("[reference_duration_confirmation_required]")
    assert json.loads(stored.split("] ", 1)[1]) == {
        "adjustment": "up",
        "current_visual_duration": None,
        "duration_input": 9.5,
        "request_duration": 12,
        "script_duration": 4,
    }


def test_active_narrated_video_worker_rejection_is_localizable() -> None:
    stored = _encode_task_failure_message(
        ConflictError("tts_conflicts_with_active_narrated_video", resource_id="E1U01")
    )

    assert stored == '[tts_conflicts_with_active_narrated_video] {"resource_id": "E1U01"}'
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale]["tts_conflicts_with_active_narrated_video"].format(resource_id="E1U01")
        assert render_failure(stored, _translator(locale)) == expected


@pytest.mark.parametrize(
    ("code", "capability"),
    [
        ("video_capability_missing_i2v", "i2v"),
        ("video_capability_missing_r2v", "r2v"),
        ("video_capability_reference_unavailable", "r2v"),
    ],
)
def test_encode_video_bucket_capability_error_renders_per_locale(code: str, capability: VideoCapability):
    """视频解析闸异常（VideoBucketCapabilityError）同走结构化编码，读侧三语渲染。

    三个 code 逐个断言渲染结果：只覆盖其中一个的话，另两条模板的占位符拼错或参数缺失
    仍会通过 —— key 齐备性由 ``test_video_bucket_codes_registered_with_all_three_locales`` 单独保证。
    """
    exc = VideoBucketCapabilityError(
        code=code,
        capability=capability,
        provider_id="minimax",
        model_id="MiniMax-Hailuo-2.3",
        message=f"lacks {capability}",
    )
    stored = _encode_task_failure_message(exc)
    assert stored.startswith(f"[{code}]")
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][code].format(provider="minimax", model="MiniMax-Hailuo-2.3")
        assert render_failure(stored, _translator(locale)) == expected


@pytest.mark.parametrize(
    "code",
    [
        "video_capability_missing_i2v",
        "video_capability_missing_r2v",
        "video_capability_reference_unavailable",
    ],
)
def test_video_bucket_codes_registered_with_all_three_locales(code):
    """解析闸的 code 是 errors 目录 key 的 identity 映射，三语模板齐备。"""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_encode_stringifies_non_json_params():
    """params 值不是 JSON 原生类型时降级为字符串，编码不得在失败路径里抛错。

    ``video_duration_invalid`` 回显的是调用方拒绝的原始值，类型不可控。
    """
    stored = _encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=Path("weird")))
    assert json.loads(stored.split("] ", 1)[1]) == {"duration": "weird"}


def test_bound_reason_preserves_scalar_param_types():
    """裁剪不得改掉同条失败里其它参数的类型。

    裁剪是整个 params 一起过一遍的：只要有任一参数超长触发裁剪，同条失败携带的全部标量都会
    跟着走一遍归一。把 JSON 原生标量转成文本会在重新编码时被加上引号存回去（``42`` 变
    ``"42"``、``True`` 变 ``"true"``、``None`` 变 ``"null"``），落库信封的参数类型就变了；
    模板若用带类型说明符的占位符（如 ``{n:d}``），``str.format`` 抛出的异常会被
    ``lib/i18n/__init__.py::_`` 吞掉，最终把裸占位符显示给用户。
    """
    stored = encode_failure("resume_expired_detail", detail="x" * 3000, retries=3, ratio=1.5, expired=True, note=None)

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    params = json.loads(bounded.split("] ", 1)[1])
    assert params["retries"] == 3 and isinstance(params["retries"], int)
    assert params["ratio"] == 1.5 and isinstance(params["ratio"], float)
    assert params["expired"] is True
    assert params["note"] is None
    # 超长的那个仍被收窄。
    assert len(params["detail"]) < 3000


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


def test_deep_cascade_chain_keeps_root_cause_renderable():
    """长依赖链上每层都要渲染成本地化文案，而不是嵌一段被切坏的内层信封。

    逐层包裹近指数增长：每层把上一层整个信封重新编码进 JSON，转义使长度翻倍，七层左右撑破
    落库预算，裁剪只能从尾部切字符，把内层切在 JSON 中途——外层仍可解析，读侧却把残缺内层
    当普通文本原样嵌进本地化文案。编码前折叠掉中间层后，串长与链深无关。
    """
    upstream = _encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    inner = MESSAGES["zh"]["video_duration_not_supported"].format(duration=7, supported="5, 10")

    stored = upstream
    lengths = set()
    for depth in range(1, 21):
        stored = _encode_bounded_cascade_failure(dependency_task_id=f"{depth:032d}", reason=stored)
        lengths.add(len(stored))
        rendered = render_failure(stored, _translator("zh"))
        assert rendered is not None
        # 直接依赖与根本原因两项都在，且没有任何机器码碎片漏出。
        assert f"{depth:032d}" in rendered
        assert inner in rendered
        assert "[cascade_blocked_dependency]" not in rendered
        assert "video_duration_not_supported" not in rendered

    assert len(lengths) == 1, f"折叠后串长应与链深无关，实际出现多种长度: {sorted(lengths)}"


def test_collapse_cascade_reason_leaves_non_cascade_reason_untouched():
    """非级联原因（含 provider 原始文本）不该被折叠动到。"""
    upstream = _encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration="x"))
    assert collapse_cascade_reason(upstream) == upstream
    assert collapse_cascade_reason("provider rejected") == "provider rejected"


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


def test_bound_reason_shrinks_escape_heavy_params():
    """转义后翻倍的参数也要收窄成合法信封，而不是被判定「砍不动」退回裸切片。

    超额长度以编码后的字符计，而 ``"`` / ``\\`` 经 JSON 转义占两列：拿它直接当要砍的原始
    字符数比较，会把原始长度小于超额长度、但实际收窄后装得下的参数误判成无从收窄。
    """
    names = "\\" * 900
    stored = encode_failure("image_reference_images_unreadable", names=names)
    # 转义让编码长度约为原始长度的两倍——正是误判成立的前提。
    assert len(stored) > 2 * len(names)

    bounded = bound_reason(stored, 900)

    assert len(bounded) <= 900
    rendered = render_failure(bounded, _translator("en"))
    assert rendered is not None
    assert "image_reference_images_unreadable" not in rendered
    # 预算够放下相当一部分参数时就不该削空——诊断内容是这条原因的全部价值。
    assert len(json.loads(bounded.split(" ", 1)[1])["names"]) > 100


def _oversized_envelope() -> str:
    """一个超出 2000 字符预算、且整体仍是合法 ``[code] {params}`` 的信封。

    超长部分必须落在 JSON 内部：拼在 ``}`` 之后的尾巴会让 ``_STRUCTURED_RE`` 失配，裁剪在
    进入 ``json`` 之前就返回，测不到本节要测的降级路径。
    """
    stored = encode_failure("video_duration_invalid", duration="x" * 3000)
    assert len(stored) > 2000
    return stored


def test_as_shrinkable_degrades_when_param_json_hits_recursion_limit(monkeypatch):
    """嵌套过深的参数量不出长度时降级为省略号，而不是把异常抛进落库路径。

    只断言 ``_as_shrinkable`` 本身：经它归一后 ``params`` 全是扁平字符串，``encode_failure``
    的 ``dumps`` 已无从撞上限，再去断言 ``bound_reason`` 只能靠全局打桩造出不可达的场景。
    """

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "dumps", _boom)

    assert task_failure._as_shrinkable([[[1]]]) == "…"


def test_bound_reason_degrades_when_params_json_load_hits_recursion_limit(monkeypatch):
    """params 深到 ``json.loads`` 自身撞递归上限时退回字符裁剪，而不是把异常抛进落库路径。"""
    stored = _oversized_envelope()

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert bound_reason(stored, 2000) == stored[:2000]
