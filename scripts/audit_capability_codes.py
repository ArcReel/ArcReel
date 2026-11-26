"""核对能力异常构造点、已登记错误码与各语言模板参数。"""

from __future__ import annotations

import ast
import string
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lib.generation.task_failure import CAPABILITY_FAILURE_CODES
from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.references.reference_compression import ReferencePayloadFloorError

_ALLOWED_DYNAMIC_SCOPES: dict[str, int] = {}
_CAPABILITY_EXCEPTIONS = {"ImageCapabilityError", "VideoCapabilityError", "ReferencePayloadFloorError"}
_SCANNED_ROOTS = ("lib", "server")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _assignment_pairs(target: ast.expr, value: ast.expr) -> Iterator[tuple[str, ast.expr]]:
    if isinstance(target, ast.Name):
        yield target.id, value
    elif (
        isinstance(target, ast.Tuple | ast.List)
        and isinstance(value, ast.Tuple | ast.List)
        and len(target.elts) == len(value.elts)
        and not any(isinstance(item, ast.Starred) for item in (*target.elts, *value.elts))
    ):
        for child, item in zip(target.elts, value.elts, strict=True):
            yield from _assignment_pairs(child, item)


_Scope = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda


_Aliases = dict[str, frozenset[str]]


_Paths = dict[str, _Aliases]


def _join(target: _Aliases, source: _Aliases) -> None:
    for name, types in source.items():
        target[name] = target.get(name, frozenset()) | types


def _add_path(paths: _Paths, exit_kind: str, state: _Aliases) -> None:
    _join(paths.setdefault(exit_kind, {}), state)


class _ScopeScan:
    def __init__(self, entry: _Aliases, calls: dict[ast.Call, frozenset[str]]) -> None:
        self.ever_seen = entry.copy()
        self.calls = calls
        self.nested: dict[_Scope, None] = {}
        self.protected: list[_Aliases] = []

    def bind(self, aliases: _Aliases, name: str, types: frozenset[str]) -> None:
        if types:
            aliases[name] = types
            for seen in (self.ever_seen, *self.protected):
                seen[name] = seen.get(name, frozenset()) | types
        else:
            aliases.pop(name, None)

    def block(self, nodes: Iterable[ast.AST], entry: _Aliases) -> _Paths:
        paths: _Paths = {"normal": entry.copy()}
        for node in nodes:
            if (state := paths.pop("normal", None)) is None:
                break
            for kind, result in self.visit(node, state).items():
                _add_path(paths, kind, result)
        return paths

    def branches(self, branches: Iterable[Iterable[ast.AST]], entry: _Aliases) -> _Paths:
        paths: _Paths = {}
        for branch in branches:
            for kind, result in self.block(branch, entry).items():
                _add_path(paths, kind, result)
        return paths

    def loop(self, node: ast.For | ast.AsyncFor | ast.While, entry: _Aliases) -> _Paths:
        head = entry.copy()
        if not isinstance(node, ast.While):
            self.visit(node.iter, head)
        paths: _Paths = {}
        while True:
            iteration = head.copy()
            if isinstance(node, ast.While):
                self.visit(node.test, iteration)
            exhaustion = iteration.copy()
            if not isinstance(node, ast.While):
                self.target(node.target, iteration, {})
            result = self.block(node.body, iteration)
            previous = head.copy()
            for kind, state in result.items():
                if kind in ("normal", "continue"):
                    _join(head, state)
                else:
                    _add_path(paths, "normal" if kind == "break" else kind, state)
            if head == previous:
                break
        for kind, state in self.block(node.orelse, exhaustion).items():
            _add_path(paths, kind, state)
        return paths

    def try_block(self, node: ast.Try | ast.TryStar, entry: _Aliases) -> _Paths:
        exceptional = entry.copy()
        self.protected.append(exceptional)
        paths = self.block(node.body, entry)
        normal = paths.pop("normal", None)
        handler_entry = exceptional.copy()
        if normal is not None:
            for kind, state in self.block(node.orelse, normal).items():
                _add_path(paths, kind, state)
        for handler in node.handlers:
            local = handler_entry.copy()
            if handler.type:
                self.visit(handler.type, local)
            if handler.name:
                local.pop(handler.name, None)
            for kind, state in self.block(handler.body, local).items():
                if handler.name:
                    state.pop(handler.name, None)
                _add_path(paths, kind, state)
                if isinstance(node, ast.TryStar):
                    _join(handler_entry, state)
        self.protected.pop()
        _add_path(paths, "raise", exceptional)
        if not node.finalbody:
            return paths
        final: _Paths = {}
        for incoming, state in paths.items():
            for outgoing, result in self.block(node.finalbody, state).items():
                _add_path(final, incoming if outgoing == "normal" else outgoing, result)
        return final

    def target(self, target: ast.expr, aliases: _Aliases, bindings: _Aliases) -> None:
        if isinstance(target, ast.Name):
            self.bind(aliases, target.id, bindings.get(target.id, frozenset()))
        elif isinstance(target, ast.List | ast.Tuple):
            for child in target.elts:
                self.target(child, aliases, bindings)
        elif isinstance(target, ast.Starred):
            self.target(target.value, aliases, bindings)
        else:
            self.visit(target, aliases)

    def visit(self, node: ast.AST, aliases: _Aliases) -> _Paths:
        if isinstance(node, ast.If | ast.IfExp):
            self.visit(node.test, aliases)
            if isinstance(node, ast.If):
                return self.branches([node.body, node.orelse], aliases)
            joined = self.branches([[node.body], [node.orelse]], aliases)["normal"]
            aliases.clear()
            aliases.update(joined)
        elif isinstance(node, ast.BoolOp):
            joined = {}
            for operand in node.values:
                self.visit(operand, aliases)
                _join(joined, aliases)
            aliases.update(joined)
        elif isinstance(node, ast.Match):
            self.visit(node.subject, aliases)
            branches = [[case] for case in node.cases]
            if not any(
                case.guard is None and isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
                for case in node.cases
            ):
                branches.append([])
            return self.branches(branches, aliases)
        elif isinstance(node, ast.match_case):
            for pattern in ast.walk(node.pattern):
                if isinstance(pattern, ast.MatchAs | ast.MatchStar) and pattern.name:
                    aliases.pop(pattern.name, None)
                elif isinstance(pattern, ast.MatchMapping) and pattern.rest:
                    aliases.pop(pattern.rest, None)
            return self.block(ast.iter_child_nodes(node), aliases)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            return self.loop(node, aliases)
        elif isinstance(node, ast.Try | ast.TryStar):
            return self.try_block(node, aliases)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                self.visit(item.context_expr, aliases)
                if item.optional_vars:
                    self.target(item.optional_vars, aliases, {})
            suppressed = aliases.copy()
            self.protected.append(suppressed)
            paths = self.block(node.body, aliases)
            self.protected.pop()
            _add_path(paths, "normal", suppressed)
            return paths
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            self.nested[node] = None
            body = {node.body} if isinstance(node, ast.Lambda) else set(node.body)
            for child in ast.iter_child_nodes(node):
                if child not in body:
                    self.visit(child, aliases)
            if not isinstance(node, ast.Lambda):
                types = _CAPABILITY_EXCEPTIONS.intersection({node.name}) if isinstance(node, ast.ClassDef) else set()
                self.bind(aliases, node.name, frozenset(types))
        elif isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                name = alias.name.rsplit(".", 1)[-1]
                self.bind(aliases, alias.asname or alias.name, frozenset({name} & _CAPABILITY_EXCEPTIONS))
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr):
            if node.value is not None:
                self.visit(node.value, aliases)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                bindings = {
                    name: _exception_names(value, aliases)
                    for target in targets
                    for name, value in _assignment_pairs(target, node.value)
                }
                for target in targets:
                    self.target(target, aliases, bindings)
            if isinstance(node, ast.AnnAssign):
                self.visit(node.annotation, aliases)
        else:
            if isinstance(node, ast.Call) and (names := _exception_names(node.func, aliases)):
                self.calls[node] = self.calls.get(node, frozenset()) | names
            for child in ast.iter_child_nodes(node):
                self.visit(child, aliases)
            if isinstance(node, ast.Break | ast.Continue | ast.Return | ast.Raise):
                return {type(node).__name__.lower(): aliases}
        return {"normal": aliases}


def _construction_calls(scope: _Scope, inherited: _Aliases, calls: dict[ast.Call, frozenset[str]]) -> None:
    aliases = inherited.copy()
    body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        for arg in ast.iter_child_nodes(scope.args):
            if isinstance(arg, ast.arg):
                aliases.pop(arg.arg, None)
    scan = _ScopeScan(aliases, calls)
    scan.block(body, aliases)
    for child in scan.nested:
        _construction_calls(child, inherited if isinstance(scope, ast.ClassDef) else scan.ever_seen, calls)


def _exception_names(func: ast.expr, aliases: _Aliases) -> frozenset[str]:
    if isinstance(func, ast.Name):
        return aliases.get(func.id, frozenset())
    if isinstance(func, ast.Attribute) and func.attr in _CAPABILITY_EXCEPTIONS:
        return frozenset({func.attr})
    if isinstance(func, ast.NamedExpr):
        return _exception_names(func.value, aliases)
    if isinstance(func, ast.IfExp):
        return _exception_names(func.body, aliases) | _exception_names(func.orelse, aliases)
    return frozenset()


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


def _dict_keywords(value: ast.expr) -> dict[str, ast.expr] | None:
    if not isinstance(value, ast.Dict):
        return None
    result: dict[str, ast.expr] = {}
    for key, item in zip(value.keys, value.values, strict=True):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            result[key.value] = item
        elif key is None and (nested := _dict_keywords(item)) is not None:
            result.update(nested)
        else:
            return None
    return result


def _template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _, name, spec, _ in string.Formatter().parse(template):
        if name:
            fields.add(name.split(".", 1)[0].split("[", 1)[0])
        if spec:
            fields.update(_template_fields(spec))
    return fields


@dataclass
class ConstructionSites:
    codes: dict[str, str] = field(default_factory=dict)
    dynamic: list[tuple[str, str]] = field(default_factory=list)
    missing_params: list[str] = field(default_factory=list)


def scan_tree(tree: ast.Module, rel: str) -> ConstructionSites:
    sites = ConstructionSites()
    calls: dict[ast.Call, frozenset[str]] = {}
    _construction_calls(tree, {name: frozenset({name}) for name in _CAPABILITY_EXCEPTIONS}, calls)
    for node, names in calls.items():
        provided: dict[str, ast.expr] = {}
        complete = True
        for keyword in node.keywords:
            if keyword.arg is not None:
                provided[keyword.arg] = keyword.value
            elif (unpacked := _dict_keywords(keyword.value)) is not None:
                provided.update(unpacked)
            else:
                complete = False
        code_expr = node.args[0] if node.args else provided.pop("code", None)
        literals = _static_str_choices(code_expr) if code_expr is not None else None
        if code_expr is None and complete and "ReferencePayloadFloorError" in names:
            default_code = ReferencePayloadFloorError().code
            sites.codes.setdefault(default_code, f"{rel}:{node.lineno}")
            if len(names) == 1:
                literals = [default_code]
        where = f"{rel}:{node.lineno}"
        if literals is None:
            sites.dynamic.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
            continue
        for code in literals:
            sites.codes.setdefault(code, where)
            if not complete:
                continue
            for locale in SUPPORTED_LOCALES:
                template = MESSAGES[locale].get(code, "")
                required = _template_fields(template)
                if absent := required - provided.keys():
                    sites.missing_params.append(f"{rel}:{node.lineno} ({code}, {locale}) 缺参数 {sorted(absent)}")

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
