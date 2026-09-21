#!/usr/bin/env python3
"""目录归组预演：用映射表与现有导入图算出新包边界下的包间环，不移动任何文件。

输入是同目录的 ``module_map.toml``（旧模块路径 → 新模块路径），导入图由 ``ast`` 扫描
``lib/`` 与 ``server/`` 全部源码得到——含函数体内的延迟导入与 ``TYPE_CHECKING`` 块，口径与
import-linter 的静态图一致。预演把每个模块按映射表改名后：

- 以 ``lib``、``server.services`` 与映射表新建的每个包为父包，在其直接子项（子包或模块）之间
  求强连通分量，口径同 import-linter 的 ``acyclic_siblings`` 契约；位置与内部都不动的既有子包
  不在此列，它们内部的环与归组无关；
- 对每个分量给出最小权重的断环边集（边权 = 跨包的模块级 import 边数），与映射表里
  ``[accepted_edges]`` 登记的存量豁免比对；
- 检查路线中立约束、核心库对服务端的依赖、映射表覆盖面与测试镜像移动。

用法::

    uv run python scripts/regroup/preview.py            # 打印报告
    uv run python scripts/regroup/preview.py --write    # 写入 preview-report.md
    uv run python scripts/regroup/preview.py --check    # 报告过期或约束不成立时退出码 1

零第三方依赖，只用标准库。
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = Path(__file__).with_name("module_map.toml")
REPORT_PATH = Path(__file__).with_name("preview-report.md")
SOURCE_ROOTS = ("lib", "server")
CYCLE_ANCESTORS = ("lib", "server.services")
TEST_ROOTS = ("tests/unit", "tests/integration")
# 分量超过此规模时退回贪心排序；精确求解按子集动态规划，复杂度 O(2^n · n²)
EXACT_FAS_LIMIT = 16

Edge = tuple[str, str]


@dataclass(frozen=True)
class ModuleMap:
    packages: dict[str, str]
    moves: dict[str, str]
    keep: list[str]
    rulings: dict[str, str]
    accepted_edges: dict[str, str]
    route_neutral_sources: list[str]
    route_specific: str

    def rename(self, module: str) -> str:
        """最长前缀匹配：命中映射表的模块或其子模块按新路径改名，其余原样返回。"""
        parts = module.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            target = self.moves.get(prefix)
            if target is not None:
                return ".".join([target, *parts[i:]])
        return module


def load_map(path: Path = MAP_PATH) -> ModuleMap:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    route = data["route_neutrality"]
    return ModuleMap(
        packages=dict(data["packages"]),
        moves=dict(data["moves"]),
        keep=list(data["keep"]["modules"]),
        rulings=dict(data.get("rulings", {})),
        accepted_edges=dict(data.get("accepted_edges", {})),
        route_neutral_sources=list(route["sources"]),
        route_specific=route["forbidden"],
    )


# ---------------------------------------------------------------- 导入图


def discover_modules(root: Path = REPO_ROOT) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for top in SOURCE_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = path
    return modules


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def build_import_graph(modules: Mapping[str, Path]) -> dict[str, set[str]]:
    """模块 → 它直接 import 的项目内模块。``from a import b`` 中 b 是子模块时记到子模块。"""
    graph: dict[str, set[str]] = {m: set() for m in modules}
    for module, path in modules.items():
        is_package = path.name == "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _add_edge(graph, modules, module, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = _resolve_relative(module, is_package, node.level, node.module)
                else:
                    base = node.module or ""
                for alias in node.names:
                    candidate = f"{base}.{alias.name}"
                    if candidate in modules:
                        _add_edge(graph, modules, module, candidate)
                    else:
                        _add_edge(graph, modules, module, base)
    return graph


def _add_edge(graph: dict[str, set[str]], modules: Mapping[str, Path], importer: str, imported: str) -> None:
    parts = imported.split(".")
    while parts and ".".join(parts) not in modules:
        parts.pop()
    if not parts:
        return
    target = ".".join(parts)
    if target != importer:
        graph[importer].add(target)


def rename_graph(graph: Mapping[str, set[str]], mapping: ModuleMap) -> dict[str, set[str]]:
    renamed: dict[str, set[str]] = defaultdict(set)
    for importer, imported in graph.items():
        new_importer = mapping.rename(importer)
        renamed[new_importer].update(mapping.rename(m) for m in imported)
        renamed[new_importer].discard(new_importer)
    return dict(renamed)


# ---------------------------------------------------------------- 包间环


def _child_of(ancestor: str, module: str) -> str | None:
    if not module.startswith(ancestor + "."):
        return None
    return ancestor + "." + module[len(ancestor) + 1 :].split(".")[0]


def sibling_edges(graph: Mapping[str, set[str]], ancestor: str) -> dict[Edge, list[Edge]]:
    """``ancestor`` 的直接子项之间的依赖 → 构成它的模块级 import 边。"""
    result: dict[Edge, list[Edge]] = defaultdict(list)
    for importer, imported in graph.items():
        src = _child_of(ancestor, importer)
        if src is None:
            continue
        for target in imported:
            dst = _child_of(ancestor, target)
            if dst is not None and dst != src:
                result[(src, dst)].append((importer, target))
    return {k: sorted(v) for k, v in result.items()}


def strongly_connected(nodes: Iterable[str], edges: Iterable[Edge]) -> list[list[str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        adjacency[a].append(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0

    def visit(v: str) -> None:
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on_stack.add(v)
        for w in adjacency[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    for node in sorted(set(nodes)):
        if node not in index:
            visit(node)
    return sorted(components)


def layer_order(component: list[str], weights: Mapping[Edge, int]) -> list[str]:
    """给分量内的包排出自底向上的顺序，使「下层 import 上层」的逆向边总权最小。"""
    n = len(component)
    w = [[weights.get((component[i], component[j]), 0) for j in range(n)] for i in range(n)]
    if n <= EXACT_FAS_LIMIT:
        best = [float("inf")] * (1 << n)
        choice = [-1] * (1 << n)
        best[0] = 0
        for placed in range(1 << n):
            if best[placed] == float("inf"):
                continue
            for v in range(n):
                if placed >> v & 1:
                    continue
                # v 放在已排的 placed 之上：placed 里 import v 的边成为逆向边
                cost = best[placed] + sum(w[u][v] for u in range(n) if placed >> u & 1)
                nxt = placed | 1 << v
                if cost < best[nxt]:
                    best[nxt] = cost
                    choice[nxt] = v
        order: list[int] = []
        state = (1 << n) - 1
        while state:
            v = choice[state]
            order.append(v)
            state &= ~(1 << v)
        order.reverse()
    else:
        remaining = set(range(n))
        order = []
        while remaining:
            # 贪心：每次放上「import 剩余包最少、被剩余包 import 最多」的一层
            v = max(
                sorted(remaining),
                key=lambda x: sum(w[u][x] for u in remaining) - sum(w[x][u] for u in remaining),
            )
            order.append(v)
            remaining.discard(v)
    return [component[i] for i in order]


def topological_order(component: list[str], edges: Iterable[Edge]) -> list[str]:
    """无环的包间依赖按自底向上排序（被依赖者在前），同层按名字排序。"""
    imports: dict[str, set[str]] = {c: set() for c in component}
    for a, b in edges:
        imports[a].add(b)
    order: list[str] = []
    while imports:
        ready = sorted(c for c, deps in imports.items() if not deps - set(order))
        order.extend(ready)
        for c in ready:
            del imports[c]
    return order


def backward_edges(order: list[str], edges: Iterable[Edge]) -> list[Edge]:
    position = {pkg: i for i, pkg in enumerate(order)}
    return sorted((a, b) for a, b in edges if a in position and b in position and position[a] < position[b])


def _parse_edge(text: str) -> Edge:
    left, right = (part.strip() for part in text.split("->"))
    return left, right


@dataclass
class CycleFinding:
    ancestor: str
    component: list[str]
    order: list[str]
    breakers: list[Edge]
    evidence: dict[Edge, list[Edge]]
    unwaived: list[Edge]


def module_cycle_ids(graph: Mapping[str, set[str]]) -> dict[str, int]:
    """模块 → 它所在的模块级强连通分量编号（只含成环的模块）。"""
    edges = [(a, b) for a, targets in graph.items() for b in targets]
    return {m: i for i, comp in enumerate(strongly_connected(graph.keys(), edges)) for m in comp}


def find_cycles(graph: Mapping[str, set[str]], mapping: ModuleMap) -> tuple[list[CycleFinding], list[str]]:
    """逐个父包求兄弟子项之间的环；``[accepted_edges]`` 以包对登记，移除后该父包须无环。"""
    accepted = {_parse_edge(e) for e in mapping.accepted_edges}
    seen_pairs: set[Edge] = set()
    findings: list[CycleFinding] = []
    for ancestor in sorted({*CYCLE_ANCESTORS, *mapping.packages}):
        edges = sibling_edges(graph, ancestor)
        seen_pairs.update(edges)
        nodes = {n for pair in edges for n in pair}
        residual = [k for k in edges if k not in accepted]
        still_cyclic = {n for c in strongly_connected(nodes, residual) for n in c}
        for component in strongly_connected(nodes, edges):
            members = set(component)
            inner = {k: v for k, v in edges.items() if k[0] in members and k[1] in members}
            if members & still_cyclic:
                order = layer_order(component, {k: len(v) for k, v in inner.items()})
                breakers = backward_edges(order, inner)
                unwaived = [e for e in breakers if e not in accepted]
            else:
                breakers = sorted(k for k in inner if k in accepted)
                order = topological_order(component, [k for k in inner if k not in accepted])
                unwaived = []
            findings.append(CycleFinding(ancestor, component, order, breakers, inner, unwaived))
    stale = sorted(f"{a} -> {b}" for a, b in accepted if (a, b) not in seen_pairs)
    return findings, stale


# ---------------------------------------------------------------- 其他约束


def reachable(graph: Mapping[str, set[str]], start: str) -> dict[str, str]:
    """从 start 出发可达的模块 → 到达它的前一跳，用于还原依赖链。"""
    parent: dict[str, str] = {start: start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for nxt in sorted(graph.get(current, ())):
            if nxt not in parent:
                parent[nxt] = current
                frontier.append(nxt)
    return parent


def _chain(parent: Mapping[str, str], end: str) -> list[str]:
    chain = [end]
    while parent[chain[-1]] != chain[-1]:
        chain.append(parent[chain[-1]])
    return list(reversed(chain))


def route_neutral_violations(graph: Mapping[str, set[str]], mapping: ModuleMap) -> list[list[str]]:
    forbidden = mapping.route_specific
    sources = sorted(m for m in graph if any(m == s or m.startswith(s + ".") for s in mapping.route_neutral_sources))
    violations: list[list[str]] = []
    for source in sources:
        parent = reachable(graph, source)
        hits = sorted(m for m in parent if m == forbidden or m.startswith(forbidden + "."))
        if hits:
            violations.append(_chain(parent, hits[0]))
    return violations


def lib_to_server_edges(graph: Mapping[str, set[str]]) -> list[Edge]:
    return sorted(
        (m, t)
        for m, imported in graph.items()
        if m == "lib" or m.startswith("lib.")
        for t in imported
        if t == "server" or t.startswith("server.")
    )


def coverage_problems(modules: Mapping[str, Path], mapping: ModuleMap) -> list[str]:
    problems: list[str] = []
    for module in sorted(modules):
        parts = module.split(".")
        if len(parts) != 2 or parts[0] != "lib":
            continue
        if module not in mapping.moves and module not in mapping.keep:
            problems.append(f"核心库顶层 {module} 既不在 [moves] 也不在 [keep]")
        if module in mapping.keep and modules[module].name != "__init__.py":
            problems.append(f"核心库顶层 {module} 是散文件，不能留在顶层")
    problems.extend(
        f"应用服务层 {module} 未映射"
        for module in sorted(modules)
        if module.startswith("server.services.") and module.count(".") == 2 and module not in mapping.moves
    )
    problems.extend(f"[moves] 旧路径 {old} 不存在" for old in sorted(mapping.moves) if old not in modules)
    renamed_targets = Counter(mapping.moves.values())
    for new, count in sorted(renamed_targets.items()):
        if count > 1:
            problems.append(f"新路径 {new} 被 {count} 个旧路径占用")
        if new in modules and new not in mapping.moves:
            problems.append(f"新路径 {new} 与现存模块重名")
    known_parents = {*mapping.packages, *modules, *mapping.moves.values()}
    for old, new in sorted(mapping.moves.items()):
        parent = new.rsplit(".", 1)[0]
        if parent not in known_parents:
            problems.append(f"{old} 的新父包 {parent} 未在 [packages] 登记")
        if old.rsplit(".", 1)[1] != new.rsplit(".", 1)[1] and old not in mapping.rulings:
            problems.append(f"{old} 改名为 {new} 但 [rulings] 没有说明理由")
    return problems


# ---------------------------------------------------------------- 既有契约


@dataclass
class ContractCheck:
    name: str
    renamed: list[tuple[str, str]]
    ignore_count: int
    old_violations: list[str]
    new_violations: list[str]


def _members(graph: Mapping[str, set[str]], expression: str) -> set[str]:
    return {m for m in graph if m == expression or m.startswith(expression + ".")}


def _reaches(graph: Mapping[str, set[str]], sources: set[str], targets: set[str], *, direct_only: bool) -> bool:
    if direct_only:
        return any(graph.get(s, set()) & targets for s in sources)
    seen = set(sources)
    frontier = list(sources)
    while frontier:
        for nxt in graph.get(frontier.pop(), ()):
            if nxt in targets:
                return True
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


def contract_violations(graph: Mapping[str, set[str]], contract: Mapping[str, Any]) -> list[str]:
    """按 import 链近似 import-linter 的 layers / forbidden 判定，只用于比较改名前后是否一致。"""
    ignored = {_parse_edge(str(e)) for e in contract.get("ignore_imports", [])}
    pruned = {m: {t for t in targets if (m, t) not in ignored} for m, targets in graph.items()}
    violations: list[str] = []
    if contract["type"] == "layers":
        containers = list(contract.get("containers", [""]))
        for container in containers:
            prefix = f"{container}." if container else ""
            layers = [
                [prefix + tail.strip() for tail in str(raw).replace("|", ":").split(":")] for raw in contract["layers"]
            ]
            for hi in range(len(layers)):
                higher = set().union(*(_members(pruned, e) for e in layers[hi]))
                for lo in range(hi + 1, len(layers)):
                    lower = set().union(*(_members(pruned, e) for e in layers[lo]))
                    if _reaches(pruned, lower, higher, direct_only=False):
                        violations.append(f"{' : '.join(layers[lo])} → {' : '.join(layers[hi])}")
    elif contract["type"] == "forbidden":
        direct_only = bool(contract.get("allow_indirect_imports", False))
        violations.extend(
            f"{source} → {target}"
            for source in contract["source_modules"]
            for target in contract["forbidden_modules"]
            if _reaches(pruned, _members(pruned, source), _members(pruned, target), direct_only=direct_only)
        )
    return violations


def rename_contract(contract: Mapping[str, Any], mapping: ModuleMap) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    renamed: list[tuple[str, str]] = []

    def expr(name: str) -> str:
        new = mapping.rename(name)
        if new != name:
            renamed.append((name, new))
        return new

    result = dict(contract)
    for key in ("source_modules", "forbidden_modules"):
        if key in contract:
            result[key] = [expr(str(m)) for m in contract[key]]
    if "ignore_imports" in contract:
        result["ignore_imports"] = [
            " -> ".join(expr(side) for side in _parse_edge(str(e))) for e in contract["ignore_imports"]
        ]
    if "layers" in contract:
        (container,) = contract.get("containers", [""])
        prefix = f"{container}." if container else ""
        layers = []
        for raw in contract["layers"]:
            delimiter = "|" if "|" in str(raw) else ":"
            tails = [expr(prefix + t.strip()).removeprefix(prefix) for t in str(raw).split(delimiter)]
            layers.append(f" {delimiter} ".join(tails))
        result["layers"] = layers
    return result, sorted(set(renamed))


def check_contracts(
    old_graph: Mapping[str, set[str]], new_graph: Mapping[str, set[str]], mapping: ModuleMap
) -> list[ContractCheck]:
    """既有契约按映射表改写模块路径后，在新导入图上的判定须与旧图一致。"""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    checks: list[ContractCheck] = []
    for contract in config["tool"]["importlinter"]["contracts"]:
        new_contract, renamed = rename_contract(contract, mapping)
        checks.append(
            ContractCheck(
                name=contract["name"],
                renamed=renamed,
                ignore_count=len(contract.get("ignore_imports", [])),
                old_violations=contract_violations(old_graph, contract),
                new_violations=contract_violations(new_graph, new_contract),
            )
        )
    return checks


# ---------------------------------------------------------------- 测试镜像


def _mentioned_modules(path: Path, candidates: Iterable[str]) -> Counter[str]:
    text = path.read_text(encoding="utf-8")
    counts: Counter[str] = Counter()
    tree = ast.parse(text)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.append(node.value)
    candidate_list = sorted(candidates, key=len, reverse=True)
    for name in names:
        for candidate in candidate_list:
            if name == candidate or name.startswith(candidate + "."):
                counts[candidate] += 1
                break
    return counts


@dataclass(frozen=True)
class TestMove:
    old: str
    new: str
    rule: str


def _imported_test_helpers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module.rsplit(".", 1)[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.")
    }


def derive_test_moves(mapping: ModuleMap) -> tuple[list[TestMove], list[str]]:
    """由映射表推导镜像测试的去向，不另行维护测试清单。

    - 被整体移动的源码目录（如 ``lib.pricing``）对应的测试目录整体跟随；
    - 与被移动模块同处一个源码目录镜像下的散文件，依次按：
      1. 文件名：去掉 ``test_`` 后等于某个被移动模块名、或以「模块名_」开头（取最长匹配）；
      2. 提及：import 与字符串模块路径提及的同目录被移动模块，按其新父包汇总计数取最多者，并列取字典序
         并注明；
      3. 辅助文件：import 了同目录里已推导出去向的辅助模块（如 ``*_support.py``），随辅助模块走；
      4. 全局提及：同 2，但计数范围扩大到全部映射条目——测的是别处源码的测试随被测源码走。
    """
    moves: list[TestMove] = []
    unresolved: list[str] = []
    moved_dirs = {old: new for old, new in mapping.moves.items() if (REPO_ROOT / old.replace(".", "/")).is_dir()}
    for test_root in TEST_ROOTS:
        base = REPO_ROOT / test_root
        for old_dir, new_dir in sorted(moved_dirs.items()):
            src = base / old_dir.replace(".", "/")
            if src.is_dir():
                dst = base / new_dir.replace(".", "/")
                moves.append(
                    TestMove(str(src.relative_to(REPO_ROOT)) + "/", str(dst.relative_to(REPO_ROOT)) + "/", "目录跟随")
                )
        for source_dir in ("lib", "server/services"):
            directory = base / source_dir
            if not directory.is_dir():
                continue
            package = source_dir.replace("/", ".")
            siblings = {
                old: new
                for old, new in mapping.moves.items()
                if old.rsplit(".", 1)[0] == package and old not in moved_dirs
            }
            placed: dict[str, str] = {}
            pending: list[Path] = []
            for path in sorted(directory.glob("*.py")):
                if path.name in ("__init__.py", "conftest.py"):
                    continue
                stem = path.stem.removeprefix("test_")
                by_name = sorted(
                    (
                        old
                        for old in siblings
                        if stem == old.rsplit(".", 1)[1] or stem.startswith(old.rsplit(".", 1)[1] + "_")
                    ),
                    key=len,
                    reverse=True,
                )
                if by_name:
                    placed[path.stem] = siblings[by_name[0]].rsplit(".", 1)[0]
                    moves.append(_test_move(base, path, placed[path.stem], "文件名"))
                    continue
                if not _place_by_mentions(mapping, base, path, siblings, placed, moves):
                    pending.append(path)
            for path in pending:
                helpers = sorted(h for h in _imported_test_helpers(path) if h in placed)
                if helpers:
                    placed[path.stem] = placed[helpers[0]]
                    moves.append(_test_move(base, path, placed[helpers[0]], f"随辅助模块 {helpers[0]}"))
                elif not _place_by_mentions(mapping, base, path, mapping.moves, placed, moves):
                    unresolved.append(str(path.relative_to(REPO_ROOT)))
    return moves, unresolved


def _place_by_mentions(
    mapping: ModuleMap,
    base: Path,
    path: Path,
    scope: Mapping[str, str],
    placed: dict[str, str],
    moves: list[TestMove],
) -> bool:
    per_package: Counter[str] = Counter()
    for old, count in _mentioned_modules(path, scope).items():
        per_package[mapping.moves[old].rsplit(".", 1)[0]] += count
    if not per_package:
        return False
    best = max(per_package.values())
    tied = sorted(p for p, c in per_package.items() if c == best)
    placed[path.stem] = tied[0]
    moves.append(_test_move(base, path, tied[0], f"提及 {best} 次" + ("（并列取字典序）" if len(tied) > 1 else "")))
    return True


def _test_move(base: Path, path: Path, new_package: str, rule: str) -> TestMove:
    dst = base / new_package.replace(".", "/") / path.name
    return TestMove(str(path.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT)), rule)


# ---------------------------------------------------------------- 报告


def _original(mapping: ModuleMap, module: str) -> str:
    inverse = {new: old for old, new in mapping.moves.items()}
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        old = inverse.get(".".join(parts[:i]))
        if old is not None:
            return ".".join([old, *parts[i:]])
    return module


def render_report(mapping: ModuleMap) -> tuple[str, bool]:
    modules = discover_modules()
    graph = build_import_graph(modules)
    new_graph = rename_graph(graph, mapping)
    problems = coverage_problems(modules, mapping)
    cycles, stale = find_cycles(new_graph, mapping)
    cycle_ids = module_cycle_ids(graph)
    contracts = check_contracts(graph, new_graph, mapping)
    violations = route_neutral_violations(new_graph, mapping)
    server_edges = lib_to_server_edges(new_graph)
    test_moves, unresolved_tests = derive_test_moves(mapping)
    for target, count in sorted(Counter(m.new for m in test_moves).items()):
        if count > 1 or (REPO_ROOT / target).exists():
            problems.append(f"测试落点 {target} 冲突")

    def inherited(edge: Edge) -> bool:
        a, b = (_original(mapping, m) for m in edge)
        return a in cycle_ids and cycle_ids[a] == cycle_ids.get(b)

    contract_drift = [c for c in contracts if c.old_violations != c.new_violations]
    ok = (
        not problems
        and not stale
        and not violations
        and not contract_drift
        and not any(c.unwaived for c in cycles)
        and not unresolved_tests
    )
    breaker_imports = [e for c in cycles for pair in c.breakers for e in c.evidence[pair]]
    out: list[str] = []
    w = out.append
    w("# 目录归组预演报告")
    w("")
    w("> 本文件由 `uv run python scripts/regroup/preview.py --write` 生成，勿手工编辑。")
    w("> 输入：`scripts/regroup/module_map.toml` 与当前 `lib/`、`server/` 源码的静态导入图。")
    w("> 各项裁定的理由见 `scripts/regroup/module_map.toml` 的 `[rulings]` 与 `[accepted_edges]`。")
    w("")
    w("## 结论")
    w("")
    w(f"- 映射条目 {len(mapping.moves)} 条；覆盖问题 {len(problems)} 个")
    w(f"- 有包间环的强连通分量 {len(cycles)} 个，分布在 {len({c.ancestor for c in cycles})} 个父包")
    w(
        f"- 断环需豁免的包间依赖 {sum(len(c.breakers) for c in cycles)} 条（模块级 import {len(breaker_imports)} 条，"
        f"其中 {sum(inherited(e) for e in breaker_imports)} 条本身就在现有的模块级环上）；"
        f"未登记 {sum(len(c.unwaived) for c in cycles)} 条；登记了但不存在 {len(stale)} 条"
    )
    w(f"- 既有 import-linter 契约改写路径后判定变化 {len(contract_drift)} 条")
    w(f"- 路线中立违规 {len(violations)} 处；核心库 → 服务端依赖 {len(server_edges)} 条")
    w(f"- 镜像测试移动 {len(test_moves)} 项；无法推导去向 {len(unresolved_tests)} 个")
    w(f"- 预演判定：{'通过' if ok else '不通过'}")
    w("")
    if problems:
        w("## 覆盖问题")
        w("")
        out.extend(f"- {p}" for p in problems)
        w("")
    w("## 新布局")
    w("")
    by_package: dict[str, list[str]] = defaultdict(list)
    for old, new in sorted(mapping.moves.items(), key=lambda kv: kv[1]):
        by_package[new.rsplit(".", 1)[0]].append(f"`{old}` → `{new}`")
    for package in sorted(by_package):
        section = mapping.packages.get(package, "")
        w(f"### `{package}`" + (f"（{section}）" if section else ""))
        w("")
        out.extend(f"- {line}" for line in by_package[package])
        w("")
    w("## 包间环")
    w("")
    w("口径同 import-linter `acyclic_siblings`：在 `lib`、`server.services` 与每个新建包内，把直接子项（子包或")
    w("模块）之间的模块级 import 聚合成包间依赖，求强连通分量。每个分量给出一个自底向上的顺序，使「下层")
    w("import 上层」的逆向依赖所含的模块级 import 总数最小；逆向依赖就是断环所需的豁免。模块级 import 标")
    w("「既有环」的，两端在当前代码里已经处于同一个模块级环上——这一段环与目录无关，任何归属都消除不了。")
    w("")
    for finding in cycles:
        w(f"### `{finding.ancestor}` 内的环（{len(finding.component)} 个子项）")
        w("")
        w("自底向上：" + " < ".join(f"`{m.removeprefix(finding.ancestor + '.')}`" for m in finding.order))
        w("")
        w("| 逆向依赖 | 模块级 import |")
        w("| --- | --- |")
        for edge in finding.breakers:
            evidence = "<br>".join(
                f"`{a} -> {b}`" + ("（既有环）" if inherited((a, b)) else "") for a, b in finding.evidence[edge]
            )
            w(f"| `{edge[0]}` → `{edge[1]}` | {evidence} |")
        w("")
        if finding.unwaived:
            w("以下逆向依赖未在 `[accepted_edges]` 登记：")
            w("")
            out.extend(f"- `{a} -> {b}`" for a, b in finding.unwaived)
            w("")
    if stale:
        w("### 登记了但不存在的豁免")
        w("")
        out.extend(f"- `{e}`" for e in stale)
        w("")
    w("## 既有契约改写")
    w("")
    w("按映射表改写 `pyproject.toml` 中 import-linter 契约的模块路径，在新导入图上按 import 链重新判定，结果须")
    w("与旧图一致；存量豁免条数按构造不变。")
    w("")
    w("| 契约 | 存量豁免 | 旧图违规 | 新图违规 |")
    w("| --- | --- | --- | --- |")
    for c in contracts:
        w(f"| {c.name} | {c.ignore_count} | {len(c.old_violations)} | {len(c.new_violations)} |")
    w("")
    for c in contracts:
        if c.renamed:
            w(f"- {c.name}：" + "；".join(f"`{a}` → `{b}`" for a, b in c.renamed if a.count(".") <= 2))
    w("")
    w("## 路线中立")
    w("")
    w(
        f"源：{', '.join(f'`{s}`' for s in mapping.route_neutral_sources)}；禁止（含间接）依赖 `{mapping.route_specific}`。"
    )
    w("")
    if violations:
        out.extend(f"- {' → '.join(f'`{m}`' for m in chain)}" for chain in violations)
    else:
        w("成立：以上模块及其传递依赖均不触达参考生视频子包。")
    w("")
    w("## 核心库 → 服务端")
    w("")
    out.extend(f"- `{a}` → `{b}`" for a, b in server_edges)
    if not server_edges:
        w("无。")
    w("")
    w("## 镜像测试移动")
    w("")
    w("| 旧路径 | 新路径 | 推导依据 |")
    w("| --- | --- | --- |")
    out.extend(f"| `{m.old}` | `{m.new}` | {m.rule} |" for m in test_moves)
    w("")
    if unresolved_tests:
        w("无法从映射表推导去向的测试：")
        w("")
        out.extend(f"- `{p}`" for p in unresolved_tests)
        w("")
    return "\n".join(out), ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="把报告写入 preview-report.md")
    group.add_argument("--check", action="store_true", help="报告过期或预演不通过时退出码 1")
    args = parser.parse_args(argv)
    report, ok = render_report(load_map())
    if args.write:
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"已写入 {REPORT_PATH.relative_to(REPO_ROOT)}；预演{'通过' if ok else '不通过'}")
        return 0 if ok else 1
    if args.check:
        current = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
        if current != report:
            print("preview-report.md 与映射表 / 源码不一致，请运行 --write 重新生成", file=sys.stderr)
            return 1
        return 0 if ok else 1
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
