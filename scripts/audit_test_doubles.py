#!/usr/bin/env python3
"""静态审计 tests/ 中两类可机械识别的负价值测试形态。

类 1「测 mock 本身」：一个 test 函数内所有断言都作用于替身对象的调用记录
（`assert_called*` / `assert_awaited*` / `call_args*` / `call_count` / `await_count`，
或 monkeypatch 注入的调用记录容器），没有任何针对返回值、状态、副作用的断言。

类 2「patch 被测公共入口或私有符号」：`patch(...)` / `patch.object(...)` /
`monkeypatch.setattr(...)` 的目标以 `lib.` / `server.` 开头且命中 `_` 前缀私有符号；
以及 integration 标记用例 patch 了被测 module 自身的公共入口。

零第三方依赖，只用 `ast`。用法见 `--help`。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

# ---------------------------------------------------------------- 常量表

MOCK_FACTORIES = {
    "MagicMock",
    "AsyncMock",
    "Mock",
    "NonCallableMock",
    "NonCallableMagicMock",
    "PropertyMock",
    "create_autospec",
    "mock_open",
}

PATCH_FUNCS = {"patch", "mock.patch", "unittest.mock.patch", "mocker.patch"}

PATCH_OBJECT_FUNCS = {f"{prefix}.object" for prefix in PATCH_FUNCS}

DOUBLE_ASSERT_METHODS = {
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_has_calls",
    "assert_not_called",
    "assert_awaited",
    "assert_awaited_once",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "assert_any_await",
    "assert_has_awaits",
    "assert_not_awaited",
}

DOUBLE_ATTRS = {
    "call_args",
    "call_args_list",
    "call_count",
    "await_args",
    "await_args_list",
    "await_count",
    "mock_calls",
    "method_calls",
    "called",
    "awaited",
    "call_list",
}

# 断言辅助函数：调用即视为实质断言（保守，倾向压低类 1 误报）
ASSERT_HELPER_PREFIXES = ("assert", "_assert", "check_", "_check", "verify_", "_verify", "expect_")

BUILTIN_NAMES = {
    "len",
    "any",
    "all",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "sorted",
    "isinstance",
    "type",
    "abs",
    "min",
    "max",
    "sum",
    "range",
    "enumerate",
    "zip",
    "repr",
    "getattr",
    "hasattr",
    "pytest",
    "math",
    "json",
    "re",
}

# 类 2 动机分档。按顺序匹配：轮询/重试 > 外部 I/O > 被测逻辑本身。
TIMING_RE = re.compile(
    r"(?i)(sleep|poll|interval|delay|timeout|backoff|retry|retries|max_attempts|attempts"
    r"|wait|deadline|_seconds$|_secs$|_ms$|tick|schedule|monotonic|perf_counter)"
)

IO_RE = re.compile(
    r"(?i)(http|requests?|aiohttp|client|session|urlopen|download|upload|fetch|subprocess"
    r"|popen|ffmpeg|ffprobe|probe|socket|smtp|s3|oss|bucket|storage|blob|boto|openai|anthropic"
    r"|dashscope|volc|ark|gemini|vertex|minimax|kling|veo|sdk|api|invoke|submit|dispatch"
    r"|_post|_get|_put|_delete|_request|_query|_execute|engine|connect|conn|cursor"
    r"|read_bytes|write_bytes|read_text|write_text|copy2|rmtree|unlink|which|check_output"
    r"|getenv|environ|utcnow|now$|uuid|token|credential|env$|load_env|shell|command|exec)"
)

IO_MODULE_HINTS = (
    "_backends",
    ".storage",
    ".db",
    ".database",
    ".http",
    ".client",
    "lib.media",
    "lib.ffmpeg",
    "server.db",
)

MOTIVE_TIMING = "绕轮询/重试等待常量"
MOTIVE_IO = "绕外部 I/O"
MOTIVE_LOGIC = "绕被测逻辑本身"

# 正则只看符号名，命中率有限。下表是逐符号读过生产代码后的人工判定，覆盖正则判错的目标；
# 其余目标按正则归档。改动这张表就改动了报告里的三档数字，增删条目请附判定依据。
MOTIVE_OVERRIDES = {
    # 名字不像 I/O，实际经 ConfigResolver / 文件系统 / 远端探测
    "server.agent_runtime.sdk_tools._context.resolve_video_caps": MOTIVE_IO,
    "server.agent_runtime.sdk_tools.enqueue_image_edits._i2i_provider_available": MOTIVE_IO,
    "server.agent_runtime.sdk_tools.text_generation._resolve_video_capabilities": MOTIVE_IO,
    "lib.artifact_manifest._O_NOFOLLOW": MOTIVE_IO,
    "server.routers.system_config._read_app_version": MOTIVE_IO,
    "server.services.diagnostics._app_version": MOTIVE_IO,
    "server.app._DOCKERENV_PATH": MOTIVE_IO,
    "server.app._CGROUP_PATH": MOTIVE_IO,
    "server.app._migrate_source_encoding_on_startup": MOTIVE_IO,
    "server.routers.custom_providers._run_discover": MOTIVE_IO,
    "server.routers.custom_providers._test_google": MOTIVE_IO,
    "lib.project_manager.ProjectManager._write_script_unlocked": MOTIVE_IO,
    "lib.project_manager.ProjectManager._read_script_unlocked": MOTIVE_IO,
    "lib.artifact_activation._ensure_activation_backup": MOTIVE_IO,
    "server.services.reference_video_tasks._stage_provider_media_for_task": MOTIVE_IO,
    "server.services.grid_split._register_split_entries_atomically": MOTIVE_IO,
    # 名字像 I/O，实际是纯判断 / 阈值常量 / 内部编排
    "server.agent_runtime.event_log._is_client_key_violation": MOTIVE_LOGIC,
    "lib.video_backends.v2_video_generations._LARGE_IMAGE_WARN_BYTES": MOTIVE_LOGIC,
    "server.services.cost_estimation.quote_video_request_from_price": MOTIVE_LOGIC,
    "server.services.generation_tasks._execute_reference_video_task_proxy": MOTIVE_LOGIC,
    "lib.generation_worker.GenerationWorker._dispatch_resume_orphans_background": MOTIVE_LOGIC,
}

MONKEYPATCH_SETTERS = {"setattr", "delattr"}


# ---------------------------------------------------------------- 数据结构


@dataclass
class DoubleOnlyTest:
    path: str
    line: int
    func: str
    marks: list[str]
    double_assertions: int
    evidence: list[str]
    subjects: list[str]


@dataclass
class PatchSite:
    path: str
    line: int
    func: str
    marks: list[str]
    target: str
    kind: str
    private: bool
    module: str | None
    symbol_origin: str
    module_under_test: str | None
    hits_module_under_test: bool
    motive: str


@dataclass
class FileStat:
    path: str
    test_funcs: int = 0
    double_only: int = 0
    no_assertion: int = 0
    private_patches: int = 0
    integration_self_patches: int = 0


# ---------------------------------------------------------------- 工具函数


def dotted(node: ast.expr | None) -> str | None:
    """把 Name / Attribute 链还原成点分字符串。"""
    if node is None:
        return None
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def call_name(node: ast.Call) -> str | None:
    return dotted(node.func)


def is_patch_object_call(node: ast.Call) -> bool:
    name = call_name(node)
    return name in PATCH_OBJECT_FUNCS


def is_patch_call(node: ast.Call) -> bool:
    """严格白名单。不能按 `.patch` 后缀判定：`client.patch(...)` 是 HTTP 方法，
    误判会把真实 HTTP 响应当成替身，进而把大量有效用例错划进类 1。"""
    name = call_name(node)
    return name in PATCH_FUNCS or name in PATCH_OBJECT_FUNCS


def is_mock_factory_call(node: ast.Call) -> bool:
    name = call_name(node)
    if name is None:
        return False
    return name.split(".")[-1] in MOCK_FACTORIES


def marks_of(decorators: list[ast.expr]) -> list[str]:
    out: list[str] = []
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = dotted(target)
        if not name:
            continue
        parts = name.split(".")
        if "mark" in parts:
            idx = parts.index("mark")
            if idx + 1 < len(parts):
                out.append(parts[idx + 1])
    return out


def _is_empty_container(node: ast.expr) -> bool:
    if isinstance(node, ast.List) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if isinstance(node, ast.Call):
        name = call_name(node)
        return name in {"list", "dict", "set"} and not node.args
    return False


# ---------------------------------------------------------------- 模块别名解析


def alias_map_from(node: ast.AST) -> dict[str, str]:
    """收集一个 AST 子树里的 import 别名 -> 点分模块映射。"""
    out: dict[str, str] = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    out[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            for alias in sub.names:
                out[alias.asname or alias.name] = f"{sub.module}.{alias.name}"
    return out


class AliasIndex:
    """把测试文件里的 import 还原成 别名 -> 点分模块 的映射（模块级视角）。"""

    def __init__(self, tree: ast.Module) -> None:
        self.alias_to_module: dict[str, str] = alias_map_from(tree)
        self.imported_modules: set[str] = set()
        self.import_counter: Counter[str] = Counter()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imported_modules.add(alias.name)
                    if alias.name.startswith(("lib.", "server.")):
                        self.import_counter[alias.name] += 1
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.imported_modules.add(node.module)
                if node.module.startswith(("lib", "server")):
                    for alias in node.names:
                        # `from lib.x import y`：y 可能是子模块也可能是符号，两种都登记
                        self.import_counter[f"{node.module}.{alias.name}"] += 1

    def resolve(self, name: str, overrides: dict[str, str] | None = None) -> str:
        head, _, rest = name.partition(".")
        base = (overrides or {}).get(head) or self.alias_to_module.get(head)
        if base is None:
            return name
        return f"{base}.{rest}" if rest else base

    def module_under_test(self, path: Path, is_module: Callable[[str], bool]) -> str | None:
        """被测 module 启发式：测试文件名去掉 `test_` 前缀后与文件内 `lib.` / `server.`
        import 的模块末段做前缀匹配，取匹配最长者；无匹配时取被 import 次数最多的
        `lib.` / `server.` 模块。候选先用磁盘上是否存在同名模块文件过滤掉符号 import。"""
        stem = path.stem
        if stem.startswith("test_"):
            stem = stem[5:]
        pool = {m for m in self.imported_modules if m.startswith(("lib.", "server."))}
        pool |= {m for m in self.import_counter if m.startswith(("lib.", "server."))}
        # 必须排序后遍历：并列长度下不定的迭代顺序会让「被测 module」在多次运行间抖动。
        candidates = sorted(m for m in pool if is_module(m))
        best: str | None = None
        best_len = 0
        for mod in candidates:
            last = mod.split(".")[-1]
            if not last:
                continue
            if stem == last or stem.startswith(last + "_") or last.startswith(stem):
                if len(last) > best_len:
                    best, best_len = mod, len(last)
        if best:
            return best
        for module, _count in self.import_counter.most_common():
            if is_module(module):
                return module
        return None


# ---------------------------------------------------------------- 类 1 识别


class TestFunctionAnalyzer:
    """在单个 test 函数体内识别替身绑定并给断言分类。"""

    def __init__(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.func = func
        self.doubles: set[str] = set()
        self.recorders: set[str] = set()
        self._collect_doubles()

    # -- 替身绑定 ------------------------------------------------

    def _collect_doubles(self) -> None:
        injected = 0
        for dec in self.func.decorator_list:
            if isinstance(dec, ast.Call) and is_patch_call(dec):
                if not any(kw.arg == "new" for kw in dec.keywords):
                    injected += 1
        args = [a.arg for a in self.func.args.args if a.arg not in {"self", "cls"}]
        # patch 装饰器自下而上注入到 self 之后的位置参数
        self.doubles.update(args[:injected])

        for node in ast.walk(self.func):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if (
                        isinstance(item.context_expr, ast.Call)
                        and is_patch_call(item.context_expr)
                        and isinstance(item.optional_vars, ast.Name)
                    ):
                        self.doubles.add(item.optional_vars.id)
            elif isinstance(node, ast.Assign):
                self._collect_from_assign(node)

        self._collect_recorders()

    def _collect_from_assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            return
        value = node.value
        if isinstance(value, ast.Call):
            if is_mock_factory_call(value) or is_patch_call(value):
                self.doubles.update(targets)
                return
            fname = call_name(value)
            if fname and fname.endswith(".start") and fname.split(".")[0] in self.doubles:
                self.doubles.update(targets)
                return
        if isinstance(value, ast.Attribute):
            base = dotted(value)
            if base and base.split(".")[0] in self.doubles:
                self.doubles.update(targets)

    def _collect_recorders(self) -> None:
        """monkeypatch / patch 注入的调用记录容器：函数体内 `x = []`，且在嵌套
        lambda / def 中被 append / add / 下标赋值，同时该函数确实做过替身注入。"""
        containers = {
            t.id
            for node in ast.walk(self.func)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name) and _is_empty_container(node.value)
        }
        if not containers:
            return
        has_injection = any(
            isinstance(node, ast.Call) and (is_patch_call(node) or (call_name(node) or "").endswith(".setattr"))
            for node in ast.walk(self.func)
        )
        if not has_injection:
            return
        for node in ast.walk(self.func):
            if node is self.func or not isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    recv = dotted(inner.func)
                    if recv and "." in recv:
                        root, attr = recv.split(".")[0], recv.split(".")[-1]
                        if root in containers and attr in {"append", "add", "extend", "update", "setdefault"}:
                            self.recorders.add(root)
                elif isinstance(inner, ast.Subscript) and isinstance(inner.ctx, ast.Store):
                    if isinstance(inner.value, ast.Name) and inner.value.id in containers:
                        self.recorders.add(inner.value.id)
                elif isinstance(inner, ast.AugAssign) and isinstance(inner.target, ast.Name):
                    if inner.target.id in containers:
                        self.recorders.add(inner.target.id)

    # -- 断言分类 ------------------------------------------------

    def classify(self) -> tuple[int, int, list[str], list[str]]:
        """返回 (替身断言数, 实质断言数, 替身断言证据, 被断言的替身主体)。"""
        double_hits = 0
        real_hits = 0
        evidence: list[str] = []
        subjects: set[str] = set()
        double_names = self.doubles | self.recorders

        for node in ast.walk(self.func):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                fname = dotted(node.value.func)
                if fname:
                    last = fname.split(".")[-1]
                    if last in DOUBLE_ASSERT_METHODS:
                        double_hits += 1
                        subjects.add(fname.rsplit(".", 1)[0])
                        if len(evidence) < 3:
                            evidence.append(f"L{node.lineno} {fname}(...)")
                        continue
                    if last.startswith(ASSERT_HELPER_PREFIXES):
                        real_hits += 1
                        continue
            elif isinstance(node, ast.Assert):
                kind = self._classify_assert(node, double_names, subjects)
                if kind == "double":
                    double_hits += 1
                    if len(evidence) < 3:
                        evidence.append(f"L{node.lineno} assert")
                elif kind == "real":
                    real_hits += 1
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call):
                        cname = dotted(ctx.func) or ""
                        if cname.endswith(("raises", "warns", "deprecated_call")):
                            real_hits += 1
        return double_hits, real_hits, evidence, sorted(subjects)

    def _classify_assert(self, node: ast.Assert, double_names: set[str], subjects: set[str]) -> str:
        attrs = {n.attr for n in ast.walk(node.test) if isinstance(n, ast.Attribute)}
        called = {(dotted(n.func) or "").split(".")[-1] for n in ast.walk(node.test) if isinstance(n, ast.Call)}
        if attrs & DOUBLE_ATTRS or called & DOUBLE_ASSERT_METHODS:
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Attribute) and (sub.attr in DOUBLE_ATTRS or sub.attr in DOUBLE_ASSERT_METHODS):
                    owner = dotted(sub.value)
                    if owner:
                        subjects.add(owner)
            return "double"
        roots = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)} - BUILTIN_NAMES
        if not roots:
            return "trivial"
        if roots <= double_names:
            subjects.update(roots)
            return "double"
        return "real"


# ---------------------------------------------------------------- 类 2 识别


def classify_motive(target: str) -> str:
    override = MOTIVE_OVERRIDES.get(target)
    if override:
        return override
    symbol = target.split(".")[-1]
    if TIMING_RE.search(symbol):
        return MOTIVE_TIMING
    if IO_RE.search(symbol):
        return MOTIVE_IO
    if any(hint in target for hint in IO_MODULE_HINTS):
        return MOTIVE_IO
    return MOTIVE_LOGIC


def is_private_target(target: str) -> bool:
    if not target.startswith(("lib.", "server.")):
        return False
    return any(seg.startswith("_") and not seg.startswith("__") for seg in target.split(".")[1:])


def target_module_prefix(target: str, known_modules: set[str]) -> str:
    """取点分目标里最长的、已知是模块的前缀；否则退化为「去掉最后一段」。"""
    parts = target.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:cut])
        if prefix in known_modules:
            return prefix
    return ".".join(parts[:-1])


class ProductionIndex:
    """按 `lib/` `server/` 源码判定 patch 目标落在哪个模块、符号是模块自身定义还是
    从别处 import 进来的引用。区分二者是关键：`patch("被测module.自身符号")` 是把被测
    实现挖空，`patch("被测module.协作者")` 只是在既有 import 边界上换实现。"""

    ORIGIN_DEFINED = "defined"
    ORIGIN_IMPORTED = "imported"
    ORIGIN_UNKNOWN = "unknown"

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, tuple[dict[str, str], dict[str, set[str]]] | None] = {}

    def is_module(self, dotted_name: str) -> bool:
        return self._module_file(dotted_name) is not None

    def _module_file(self, module: str) -> Path | None:
        rel = module.replace(".", "/")
        for candidate in (self.root / f"{rel}.py", self.root / rel / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def _load(self, module: str) -> tuple[dict[str, str], dict[str, set[str]]] | None:
        """返回 (顶层名 -> defined/imported, 类名 -> 成员集合)。"""
        if module in self._cache:
            return self._cache[module]
        path = self._module_file(module)
        result: tuple[dict[str, str], dict[str, set[str]]] | None = None
        if path is not None:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
                tree = None
            if tree is not None:
                names: dict[str, str] = {}
                classes: dict[str, set[str]] = {}
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names[node.name] = self.ORIGIN_DEFINED
                    elif isinstance(node, ast.ClassDef):
                        names[node.name] = self.ORIGIN_DEFINED
                        members: set[str] = set()
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                members.add(item.name)
                            elif isinstance(item, ast.Assign):
                                members.update(t.id for t in item.targets if isinstance(t, ast.Name))
                            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                members.add(item.target.id)
                        classes[node.name] = members
                    elif isinstance(node, ast.Assign):
                        names.update({t.id: self.ORIGIN_DEFINED for t in node.targets if isinstance(t, ast.Name)})
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        names[node.target.id] = self.ORIGIN_DEFINED
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            names[alias.asname or alias.name.split(".")[0]] = self.ORIGIN_IMPORTED
                    elif isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            names[alias.asname or alias.name] = self.ORIGIN_IMPORTED
                result = (names, classes)
        self._cache[module] = result
        return result

    def split(self, target: str) -> tuple[str | None, str]:
        """把点分目标切成 (模块, 模块内符号路径)。取最长的、磁盘上存在的模块前缀。"""
        parts = target.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:cut])
            if self._module_file(module) is not None:
                return module, ".".join(parts[cut:])
        return None, target

    def origin(self, target: str) -> str:
        module, symbol = self.split(target)
        if module is None or not symbol:
            return self.ORIGIN_UNKNOWN
        loaded = self._load(module)
        if loaded is None:
            return self.ORIGIN_UNKNOWN
        names, classes = loaded
        head, _, rest = symbol.partition(".")
        kind = names.get(head)
        if kind is None:
            return self.ORIGIN_UNKNOWN
        if kind == self.ORIGIN_IMPORTED:
            return self.ORIGIN_IMPORTED
        if not rest:
            return self.ORIGIN_DEFINED
        members = classes.get(head)
        if members is None:
            return self.ORIGIN_UNKNOWN
        return self.ORIGIN_DEFINED if rest.split(".")[0] in members else self.ORIGIN_UNKNOWN


# ---------------------------------------------------------------- 主扫描


class FileScanner:
    def __init__(self, path: Path, root: Path, prod: ProductionIndex) -> None:
        self.path = path
        self.prod = prod
        self.rel = str(path.relative_to(root))
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.aliases = AliasIndex(self.tree)
        self.mut = self.aliases.module_under_test(path, prod.is_module)
        self.module_marks = self._module_marks()
        self.stat = FileStat(path=self.rel)
        self.double_only: list[DoubleOnlyTest] = []
        self.patches: list[PatchSite] = []

    def _module_marks(self) -> list[str]:
        out: list[str] = []
        for node in self.tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
                continue
            values = list(node.value.elts) if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            for v in values:
                name = dotted(v.func) if isinstance(v, ast.Call) else dotted(v)
                if not name:
                    continue
                parts = name.split(".")
                if "mark" in parts:
                    idx = parts.index("mark")
                    if idx + 1 < len(parts):
                        out.append(parts[idx + 1])
        return out

    def scan(self) -> None:
        self._scan_scope(self.tree.body, [], None)
        self._scan_patch_sites()

    def _scan_scope(self, body: list[ast.stmt], class_marks: list[str], class_name: str | None) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._scan_scope(node.body, class_marks + marks_of(node.decorator_list), node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                self._analyze_test(node, class_marks, class_name)

    def _analyze_test(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        class_marks: list[str],
        class_name: str | None,
    ) -> None:
        self.stat.test_funcs += 1
        marks = sorted(set(self.module_marks + class_marks + marks_of(node.decorator_list)))
        double_hits, real_hits, evidence, subjects = TestFunctionAnalyzer(node).classify()
        qual = f"{class_name}::{node.name}" if class_name else node.name
        if double_hits == 0 and real_hits == 0:
            self.stat.no_assertion += 1
        elif real_hits == 0:
            self.stat.double_only += 1
            self.double_only.append(
                DoubleOnlyTest(
                    path=self.rel,
                    line=node.lineno,
                    func=qual,
                    marks=marks,
                    double_assertions=double_hits,
                    evidence=evidence,
                    subjects=subjects,
                )
            )

    # -- patch 站点 ----------------------------------------------

    def _enclosing_map(self) -> dict[int, tuple[str, list[str], dict[str, str]]]:
        """行号 -> (所属函数限定名, 生效的 marks, 该函数内的 import 别名覆盖)。
        函数内 `from x import y as mod` 这类局部 import 必须覆盖模块级同名别名，
        否则同一文件里反复重绑定的 `mod` 会被解析成最后一次 import 的模块。"""
        out: dict[int, tuple[str, list[str], dict[str, str]]] = {}

        def walk(body: list[ast.stmt], class_marks: list[str], class_name: str | None) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    walk(node.body, class_marks + marks_of(node.decorator_list), node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    marks = sorted(set(self.module_marks + class_marks + marks_of(node.decorator_list)))
                    qual = f"{class_name}::{node.name}" if class_name else node.name
                    local = alias_map_from(node)
                    end = node.end_lineno or node.lineno
                    for line in range(node.lineno, end + 1):
                        out.setdefault(line, (qual, marks, local))
                    walk(node.body, class_marks, class_name)

        walk(self.tree.body, [], None)
        return out

    def _scan_patch_sites(self) -> None:
        enclosing = self._enclosing_map()
        known = self.aliases.imported_modules
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            target, kind = self._patch_target(node)
            if target is None or kind is None:
                continue
            func, marks, local_aliases = enclosing.get(node.lineno, ("<module>", self.module_marks, {}))
            resolved = self.aliases.resolve(target, local_aliases)
            private = is_private_target(resolved)
            module, _symbol = self.prod.split(resolved)
            if module is None:
                module = target_module_prefix(resolved, known) or None
            hits_mut = bool(self.mut) and (module == self.mut or resolved.startswith(f"{self.mut}."))
            site = PatchSite(
                path=self.rel,
                line=node.lineno,
                func=func,
                marks=marks,
                target=resolved,
                kind=kind,
                private=private,
                module=module,
                symbol_origin=self.prod.origin(resolved),
                module_under_test=self.mut,
                hits_module_under_test=hits_mut,
                motive=classify_motive(resolved),
            )
            self.patches.append(site)
            if private:
                self.stat.private_patches += 1
            elif "integration" in marks and hits_mut and site.symbol_origin == ProductionIndex.ORIGIN_DEFINED:
                self.stat.integration_self_patches += 1

    def _patch_target(self, node: ast.Call) -> tuple[str | None, str | None]:
        name = call_name(node)
        if name is None:
            return None, None
        if is_patch_object_call(node):
            if len(node.args) >= 2:
                base = dotted(node.args[0])
                attr = const_str(node.args[1])
                if base and attr:
                    return f"{base}.{attr}", "patch.object"
            return None, None
        if is_patch_call(node):
            literal = const_str(node.args[0]) if node.args else None
            return (literal, "patch") if literal else (None, None)
        if "monkeypatch" in name and name.split(".")[-1] in MONKEYPATCH_SETTERS:
            if not node.args:
                return None, None
            literal = const_str(node.args[0])
            if literal:
                return literal, "monkeypatch.setattr"
            base = dotted(node.args[0])
            attr = const_str(node.args[1]) if len(node.args) >= 2 else None
            if base and attr:
                return f"{base}.{attr}", "monkeypatch.setattr"
        return None, None


# ---------------------------------------------------------------- 汇总输出


def run(root: Path, tests_dir: Path, top: int) -> dict[str, object]:
    files = sorted(p for p in tests_dir.rglob("*.py") if p.name != "__init__.py")
    stats: list[FileStat] = []
    double_only: list[DoubleOnlyTest] = []
    patches: list[PatchSite] = []
    failures: list[str] = []
    prod = ProductionIndex(root)

    for path in files:
        try:
            scanner = FileScanner(path, root, prod)
            scanner.scan()
        except SyntaxError as exc:  # pragma: no cover
            failures.append(f"{path}: {exc}")
            continue
        stats.append(scanner.stat)
        double_only.extend(scanner.double_only)
        patches.extend(scanner.patches)

    private = [p for p in patches if p.private]
    private_own = [p for p in private if p.symbol_origin == ProductionIndex.ORIGIN_DEFINED]
    integ_self = [
        p
        for p in patches
        if not p.private
        and "integration" in p.marks
        and p.hits_module_under_test
        and p.symbol_origin == ProductionIndex.ORIGIN_DEFINED
    ]
    integ_collaborator = [
        p
        for p in patches
        if not p.private
        and "integration" in p.marks
        and p.hits_module_under_test
        and p.symbol_origin == ProductionIndex.ORIGIN_IMPORTED
    ]
    flagged = private + integ_self

    by_symbol = Counter(p.target for p in private)
    by_symbol_integ = Counter(p.target for p in integ_self)
    motive_counter = Counter(p.motive for p in flagged)
    motive_private = Counter(p.motive for p in private)
    motive_integ = Counter(p.motive for p in integ_self)
    module_counter = Counter(p.module or "<未解析>" for p in private)
    kind_counter = Counter(p.kind for p in flagged)
    origin_counter = Counter(p.symbol_origin for p in private)

    samples_by_motive: dict[str, list[dict[str, object]]] = defaultdict(list)
    for p in flagged:
        if len(samples_by_motive[p.motive]) < 10:
            samples_by_motive[p.motive].append(asdict(p))

    double_by_file = Counter(d.path for d in double_only)

    return {
        "totals": {
            "test_files": len(stats),
            "test_functions": sum(s.test_funcs for s in stats),
            "double_only_tests": len(double_only),
            "no_assertion_tests": sum(s.no_assertion for s in stats),
            "files_with_double_only": len(double_by_file),
            "patch_sites_total": len(patches),
            "private_patch_sites": len(private),
            "private_patch_sites_defined_in_module": len(private_own),
            "integration_self_public_patch_sites": len(integ_self),
            "integration_collaborator_patch_sites": len(integ_collaborator),
            "files_with_private_patch": len({p.path for p in private}),
            "distinct_private_symbols": len(by_symbol),
        },
        "double_only_by_file": [
            {"path": s.path, "double_only": s.double_only, "test_funcs": s.test_funcs}
            for s in sorted(stats, key=lambda s: (-s.double_only, s.path))
            if s.double_only
        ][:top],
        "double_only_cases": [asdict(d) for d in double_only],
        "double_only_subjects_top": Counter(s for d in double_only for s in d.subjects).most_common(top),
        "private_targets_top": by_symbol.most_common(top),
        "integration_self_targets_top": by_symbol_integ.most_common(top),
        "motive_counts": dict(motive_counter),
        "motive_counts_private_only": dict(motive_private),
        "motive_counts_integration_self": dict(motive_integ),
        "motive_samples": dict(samples_by_motive),
        "private_modules_top": module_counter.most_common(15),
        "patch_kind_counts": dict(kind_counter),
        "private_symbol_origin_counts": dict(origin_counter),
        "private_patch_sites": [asdict(p) for p in private],
        "integration_self_patch_sites": [asdict(p) for p in integ_self],
        "integration_collaborator_patch_sites": [asdict(p) for p in integ_collaborator],
        "parse_failures": failures,
    }


def _print_ranking(title: str, rows: object) -> None:
    print(title)
    assert isinstance(rows, list)
    for row in rows:
        name, count = row
        print(f"{count:4d}  {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="审计 tests/ 中「测 mock 本身」与「patch 私有符号/被测公共入口」的用例",
    )
    parser.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--tests", default="tests", help="测试目录，相对 root（默认 tests）")
    parser.add_argument("--top", type=int, default=30, help="Top N 榜单长度（默认 30）")
    parser.add_argument("--json", dest="json_out", help="把完整明细写入该 JSON 文件")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    tests_dir = root / args.tests
    if not tests_dir.is_dir():
        print(f"测试目录不存在：{tests_dir}", file=sys.stderr)
        return 2

    result = run(root, tests_dir, args.top)
    totals = result["totals"]
    assert isinstance(totals, dict)

    print("== 总量 ==")
    for key, value in totals.items():
        print(f"{key:38s} {value}")

    print("\n== 类 1：仅断言替身的用例（按文件 Top） ==")
    by_file = result["double_only_by_file"]
    assert isinstance(by_file, list)
    for row in by_file:
        print(f"{row['double_only']:4d}/{row['test_funcs']:<4d} {row['path']}")

    _print_ranking("\n== 类 1：被断言的替身主体 Top ==", result["double_only_subjects_top"])
    _print_ranking("\n== 类 2：patch 私有符号 Top ==", result["private_targets_top"])
    _print_ranking(
        "\n== 类 2：integration 用例 patch 被测 module 公共入口 Top ==", result["integration_self_targets_top"]
    )

    print("\n== 类 2：动机分档 ==")
    motives = result["motive_counts"]
    assert isinstance(motives, dict)
    for motive, count in sorted(motives.items(), key=lambda kv: -kv[1]):
        print(f"{count:4d}  {motive}")

    _print_ranking("\n== 类 2：私有符号最集中的生产模块 ==", result["private_modules_top"])

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
