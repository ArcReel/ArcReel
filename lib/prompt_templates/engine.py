"""以目录为依赖的整段提示词模版。

换行由引用处决定：片段文件只写措辞本身，不带前导空行，也不带尾换行（引擎按 ``rstrip``
去尾）。块与块之间的行距写在模版正文的引用处。块级引用（表达式独占一行）渲染为空或判重后无剩余
内容时，这一行连同行尾换行一起消失，连续列表里的空变体因此不留空行；整段渲染完成后，模版自身产生的连续三个
及以上换行塌缩为两个、首尾换行去掉。槽位值不参与塌缩，其中的空行与首尾换行逐字保留。

幂等去重由 frontmatter ``idempotent: true`` 显式开启，只给存在纯文本回贴形态的模版用；未开启
的模版从不跳过任何片段。开启后只有块级引用参与判重，且逐行判定：片段渲染结果中已在正文或
先前注入的片段里以完整行出现的行跳过，其余行照常注入，风格块里一行取值变化不会让另一行重复。
行内引用一律注入，短措辞不会被正文里偶然同形的字串吞掉；``lists/`` 目录下的列表片段承载数据，
在任何模版里都不判重。
"""

import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from jinja2 import StrictUndefined, Template, meta, nodes, pass_context
from jinja2.exceptions import TemplateError as JinjaError
from jinja2.runtime import Context
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TemplateError(ValueError):
    """模版加载或渲染失败。"""


#: 模版可声明的全部轴；设置页按轴名取显示文案，未列出的键在加载期 fail loud。
TemplateAxis = Literal["content_mode", "generation_mode", "source_kind", "asset_type", "ad_duration_tier"]


class TemplateMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    category: str
    title: str
    description: str
    applies_to: dict[TemplateAxis, list[str]]
    slots: dict[str, str]
    protected: bool
    output_schema: str | None = None
    idempotent: bool = Field(default=False, exclude=True)


class PromptTemplates:
    def __init__(self, directory: Path) -> None:
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
            finalize=self._protect_newlines,
        )
        self._env.filters = {name: self._env.filters[name] for name in ("join", "indent", "trim")}
        self._env.globals = {"partial": None, "variant": None}
        self._newline_marker = f"\x00{uuid4().hex}\x00"
        self._partials_dir = (directory / "partials").resolve()
        self._partials: dict[str, Template] = {}
        self._sources: dict[str, dict[str, str]] = {}
        self._registry: dict[str, tuple[TemplateMeta, str, Template]] = {}
        if not directory.is_dir():
            raise TemplateError(f"模版目录不存在: {directory}")
        for path in sorted(directory.rglob("*.md")):
            try:
                self._load(path, directory)
            except (JinjaError, ValidationError, yaml.YAMLError) as exc:
                raise TemplateError(f"{path}: 加载失败: {exc}") from exc

    def _load(self, path: Path, directory: Path) -> None:
        if directory / "partials" in path.parents:
            return
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n") or "\n---\n" not in raw[4:]:
            raise TemplateError(f"{path}: 缺少 YAML frontmatter")
        header, body = raw[4:].split("\n---\n", 1)
        metadata = TemplateMeta.model_validate(yaml.safe_load(header))
        if metadata.id in self._registry:
            raise TemplateError(f"{path}: 重复模版 id {metadata.id}")
        partials: dict[str, str] = {}
        referenced = self._collect(metadata, body, partials, set(), ())
        self._sources[metadata.id] = partials
        if undeclared := referenced - metadata.slots.keys():
            raise TemplateError(f"{metadata.id}: 未声明槽位 {sorted(undeclared)}")
        if unused := metadata.slots.keys() - referenced:
            raise TemplateError(f"{metadata.id}: 未使用槽位 {sorted(unused)}")
        self._registry[metadata.id] = metadata, body, self._env.from_string(body)

    def render(self, template_id: str, **slots: Any) -> str:
        metadata, _, template = self._get(template_id)
        if missing := metadata.slots.keys() - slots.keys():
            raise TemplateError(f"{template_id}: 缺少槽位 {sorted(missing)}（缺值须显式传 None）")
        if extra := slots.keys() - metadata.slots.keys():
            raise TemplateError(f"{template_id}: 多余槽位 {sorted(extra)}")
        for axis, values in metadata.applies_to.items():
            if axis in slots and slots[axis] not in values:
                raise TemplateError(f"{template_id}: {axis} 的轴值无效: {slots[axis]!r}")
        depth = 0
        opening = f"\x00{uuid4().hex}\x00"
        closing = f"\x00{uuid4().hex}\x00"

        @pass_context
        def partial(context: Context, name: str, **kwargs: Any) -> str:
            nonlocal depth
            depth += 1
            try:
                text = self._partials[name].render({**context.get_all(), **kwargs}).rstrip("\n")
            finally:
                depth -= 1
            # 嵌套片段就地展开，只有正文里的引用带标记参与块级判定。
            if depth:
                return _Fragment(text)
            dedupe = _DEDUPE if metadata.idempotent and "lists" not in name.split("/") else _INJECT
            return _Fragment(f"{opening}{dedupe}{text}{closing}")

        @pass_context
        def variant(context: Context, family: str, axis_value: str, **kwargs: Any) -> str:
            return partial(context, f"{family}/{axis_value}", **kwargs)

        try:
            # 第一遍只投影正文数据，第二遍据此判断块级片段是否已经在正文里。
            body = template.render({**slots, "partial": _blank, "variant": _blank})
            rendered = template.render({**slots, "partial": partial, "variant": variant})
        except JinjaError as exc:
            raise TemplateError(f"{template_id}: 渲染失败: {exc}") from exc
        injected = _inject_fragments(
            rendered,
            opening,
            closing,
            body.replace(self._newline_marker, "\n"),
            self._newline_marker,
        )
        return _collapse_blank_lines(injected).replace(self._newline_marker, "\n")

    def list_templates(self) -> list[TemplateMeta]:
        return [metadata.model_copy(deep=True) for metadata, _, _ in self._registry.values()]

    def read_source(self, template_id: str) -> tuple[str, dict[str, str]]:
        _, body, _ = self._get(template_id)
        return body, dict(self._sources[template_id])

    def _protect_newlines(self, value: Any) -> Any:
        """槽位值里的换行换成占位符，塌缩与块级判定只看模版自身的换行，渲染末尾再还原。"""
        if isinstance(value, str) and not isinstance(value, _Fragment):
            return value.replace("\n", self._newline_marker)
        return value

    def _get(self, template_id: str) -> tuple[TemplateMeta, str, Template]:
        try:
            return self._registry[template_id]
        except KeyError:
            raise TemplateError(f"未知模版: {template_id}") from None

    def _partial_path(self, name: str) -> Path:
        path = (self._partials_dir / f"{name}.md").resolve()
        if not path.is_relative_to(self._partials_dir):
            raise TemplateError(f"片段路径越界: {name}")
        if not path.is_file():
            raise TemplateError(f"缺少片段: {name}")
        return path

    def _collect(
        self,
        metadata: TemplateMeta,
        source: str,
        partials: dict[str, str],
        provided: set[str],
        stack: tuple[str, ...],
    ) -> set[str]:
        ast = self._env.parse(source)
        self._validate_syntax(metadata, ast, stack)
        variables = meta.find_undeclared_variables(ast) - provided
        for call in ast.find_all(nodes.Call):
            if not isinstance(call.node, nodes.Name) or call.node.name not in {"partial", "variant"}:
                raise TemplateError(f"{metadata.id}: 只允许 partial / variant 调用")
            kind = call.node.name
            count = 1 if kind == "partial" else 2
            if (
                len(call.args) != count
                or call.dyn_args is not None
                or call.dyn_kwargs is not None
                or not isinstance(call.args[0], nodes.Const)
                or not isinstance(call.args[0].value, str)
            ):
                raise TemplateError(f"{metadata.id}: 片段名必须是字面量，参数须显式传入")
            name = call.args[0].value
            if not name.startswith(("shared/", f"{metadata.id}/")) or ".." in name.split("/"):
                raise TemplateError(f"{metadata.id}: 片段必须位于 shared/ 或模版 id 目录: {name}")
            names = [name]
            if kind == "variant":
                axis = call.args[1]
                if not isinstance(axis, nodes.Name) or axis.name not in metadata.applies_to:
                    raise TemplateError(f"{metadata.id}: 变体轴须声明于 applies_to")
                names = [f"{name}/{value}" for value in metadata.applies_to[axis.name]]
                if not names:
                    raise TemplateError(f"{metadata.id}: 变体轴不可为空")
            for partial_name in names:
                if partial_name in stack:
                    raise TemplateError(f"{metadata.id}: 片段循环引用: {partial_name}")
                path = self._partial_path(partial_name)
                text = path.read_text(encoding="utf-8")
                partials[partial_name] = text
                variables |= self._collect(
                    metadata,
                    text,
                    partials,
                    provided | {kw.key for kw in call.kwargs},
                    (*stack, partial_name),
                )
                self._partials[partial_name] = self._env.from_string(text)
        return variables

    def _validate_syntax(self, metadata: TemplateMeta, ast: nodes.Template, stack: tuple[str, ...]) -> None:
        for statement in ast.find_all(nodes.Stmt):
            if not isinstance(statement, (nodes.Output, nodes.If, nodes.For)):
                raise TemplateError(f"{metadata.id}: 不支持的模版语法 {type(statement).__name__}")
        if next(ast.find_all(nodes.Compare), None) is not None:
            raise TemplateError(f"{metadata.id}: 条件仅允许槽位真值判断，不允许比较")
        for conditional in ast.find_all((nodes.If, nodes.CondExpr)):
            if not _truthiness(conditional.test):
                raise TemplateError(f"{metadata.id}: 条件仅允许槽位真值判断")
            names = list(conditional.test.find_all(nodes.Name))
            if isinstance(conditional.test, nodes.Name):
                names.append(conditional.test)
            if any(name.name in metadata.applies_to for name in names):
                raise TemplateError(f"{metadata.id}: 条件不得判断轴值")
        for loop in ast.find_all(nodes.For):
            if (
                not stack
                or "lists" not in stack[-1].split("/")
                or not _slot_path(loop.iter)
                or loop.test is not None
                or loop.recursive
            ):
                raise TemplateError(f"{metadata.id}: 循环只允许在 lists/ 列表片段中展开列表槽位")


class _Fragment(str):
    """片段的渲染结果，已由片段内部保护过槽位换行，输出时不再处理。"""


_DEDUPE = "+"
_INJECT = "-"


def _blank(*_args: Any, **_kwargs: Any) -> str:
    """第一遍渲染里片段一律为空，只留槽位投影出的正文。"""
    return ""


def _inject_fragments(rendered: str, opening: str, closing: str, body: str, newline_marker: str) -> str:
    """去掉片段标记，块级引用按需逐行判重，渲染为空的块级引用整行删去。

    ``seen`` 是还原了槽位换行的正文，并累积已注入的片段；判重只对照本片段之前的内容，
    片段内部的同形行不互相吞掉。
    """
    parts: list[str] = []
    seen = body
    cursor = 0
    while (start := rendered.find(opening, cursor)) != -1:
        end = rendered.index(closing, start)
        dedupe = rendered[start + len(opening)] == _DEDUPE
        text = rendered[start + len(opening) + 1 : end]
        after = end + len(closing)
        parts.append(rendered[cursor:start])
        block = (start == 0 or rendered[start - 1] == "\n") and (after == len(rendered) or rendered[after] == "\n")
        if block and dedupe:
            text = _unseen_lines(seen, text, newline_marker)
        if text:
            parts.append(text)
            restored = text.replace(newline_marker, "\n")
            seen = f"{seen}\n{restored}"
        elif block and after < len(rendered):
            after += 1
        cursor = after
    parts.append(rendered[cursor:])
    return "".join(parts)


def _unseen_lines(seen: str, text: str, newline_marker: str) -> str:
    """去掉 *text* 中已以完整行出现在 *seen* 里的行；空白行保留作行距，首尾空行去掉。"""
    kept = [
        line
        for line in text.split("\n")
        if not (restored := line.replace(newline_marker, "\n")).strip() or not _holds_full_lines(seen, restored)
    ]
    while kept and not kept[0]:
        kept.pop(0)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept)


def _holds_full_lines(haystack: str, needle: str) -> bool:
    """*needle* 是否以完整行的形式出现在 *haystack* 里。"""
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        if (start == 0 or haystack[start - 1] == "\n") and (end == len(haystack) or haystack[end] == "\n"):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _collapse_blank_lines(rendered: str) -> str:
    """模版行距里多余的空行在这里消失；槽位换行此时仍是占位符，不受影响。"""
    return re.sub(r"\n{3,}", "\n\n", rendered).strip("\n")


def _slot_path(expression: nodes.Node) -> bool:
    if isinstance(expression, nodes.Name):
        return True
    if isinstance(expression, nodes.Getattr):
        return _slot_path(expression.node)
    if isinstance(expression, nodes.Getitem):
        return _slot_path(expression.node) and isinstance(expression.arg, nodes.Const)
    return False


def _truthiness(expression: nodes.Node) -> bool:
    if isinstance(expression, (nodes.And, nodes.Or)):
        return _truthiness(expression.left) and _truthiness(expression.right)
    if isinstance(expression, nodes.Not):
        return _truthiness(expression.node)
    return _slot_path(expression)
