"""核对能力异常构造点、已登记错误码与三语模板参数。"""

from __future__ import annotations

import ast
import string
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lib.generation.task_failure import CAPABILITY_FAILURE_CODES
from lib.i18n import MESSAGES
from lib.references.reference_compression import ReferencePayloadFloorError

_ALLOWED_DYNAMIC_SCOPES: dict[str, int] = {}
_CAPABILITY_EXCEPTIONS = {"ImageCapabilityError", "VideoCapabilityError", "ReferencePayloadFloorError"}
_SCANNED_ROOTS = ("lib", "server")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _calls_and_aliases(tree: ast.Module) -> tuple[list[ast.Call], dict[str, str]]:
    calls: list[ast.Call] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.append(node)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                original = alias.name.rsplit(".", 1)[-1]
                if original in _CAPABILITY_EXCEPTIONS and alias.asname:
                    aliases[alias.asname] = original
    return calls, aliases


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


@dataclass
class ConstructionSites:
    codes: dict[str, str] = field(default_factory=dict)
    dynamic: list[tuple[str, str]] = field(default_factory=list)
    missing_params: list[str] = field(default_factory=list)


def scan_tree(tree: ast.Module, rel: str) -> ConstructionSites:
    sites = ConstructionSites()
    calls, aliases = _calls_and_aliases(tree)
    constructions = [(node, name) for node in calls if (name := _exception_name(node.func, aliases)) is not None]
    if not constructions:
        return sites

    def record(node: ast.Call, literals: list[str] | None) -> None:
        where = f"{rel}:{node.lineno}"
        if literals is None:
            sites.dynamic.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
        else:
            for code in literals:
                sites.codes.setdefault(code, where)

    for node, name in constructions:
        code_expr = node.args[0] if node.args else _keyword_value(node, "code")
        unpacked = any(keyword.arg is None for keyword in node.keywords)
        literals = _static_str_choices(code_expr) if code_expr is not None else None
        if code_expr is None and not unpacked and name == "ReferencePayloadFloorError":
            literals = [ReferencePayloadFloorError().code]
        record(node, literals)
        if literals is None or unpacked:
            continue
        provided = {keyword.arg for keyword in node.keywords if keyword.arg != "code"}
        for code in literals:
            for locale in ("zh", "en", "vi"):
                template = MESSAGES[locale].get(code, "")
                required = {key for _, key, _, _ in string.Formatter().parse(template) if key}
                if absent := required - provided:
                    sites.missing_params.append(f"{rel}:{node.lineno} ({code}, {locale}) 缺参数 {sorted(absent)}")

    for node in calls:
        if (error_code := _keyword_value(node, "error_code")) is not None:
            record(node, _static_str_choices(error_code))
    return sites


def audit(root: Path) -> list[str]:
    errors: list[str] = []
    found: set[str] = set()
    counts: Counter[str] = Counter()
    for directory in _SCANNED_ROOTS:
        for path in sorted((root / directory).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            normalized_source = unicodedata.normalize("NFKC", source)
            if not any(name in normalized_source for name in _CAPABILITY_EXCEPTIONS):
                continue
            sites = scan_tree(ast.parse(source, filename=rel), rel)
            found.update(sites.codes)
            errors.extend(
                f"{where} 未登记错误码 {code}"
                for code, where in sites.codes.items()
                if code not in CAPABILITY_FAILURE_CODES
            )
            for where, scope in sites.dynamic:
                counts[scope] += 1
                if scope not in _ALLOWED_DYNAMIC_SCOPES:
                    errors.append(f"{where} 未允许的动态错误码 ({scope})")
            errors.extend(sites.missing_params)
    if stale := CAPABILITY_FAILURE_CODES - found:
        errors.append(f"已登记但无构造点的错误码: {sorted(stale)}")
    if counts != Counter(_ALLOWED_DYNAMIC_SCOPES):
        errors.append(f"动态构造点数量与白名单不符: {counts}")
    return errors


if __name__ == "__main__":
    problems = audit(_REPO_ROOT)
    for problem in problems:
        print(problem, file=sys.stderr)
    sys.exit(bool(problems))
