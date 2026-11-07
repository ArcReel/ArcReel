"""Whole-prompt templates backed by a directory.

References control line spacing: partials contain wording only, without leading blank lines or trailing newlines
(the engine applies ``rstrip``). Templates specify spacing between blocks at reference sites. When a block reference
(an expression alone on its line) renders empty or has no content after deduplication, its entire line disappears.
Empty variants therefore leave no gaps in consecutive lists. After rendering, template-origin runs of three or more
newlines collapse to two, and outer newlines are trimmed. Slot newlines, including outer ones, remain verbatim.

Frontmatter ``idempotent: true`` explicitly enables deduplication only for templates with plain-text round trips.
Other templates never skip partials. When enabled, only block references deduplicate, line by line: skip complete
lines already present in the body or earlier partials, injecting the rest. Changing one style line cannot duplicate another.
Inline references always inject, so coincidentally matching body substrings never swallow short phrases. List partials
under ``lists/`` carry data and never deduplicate in any template.
"""

import re
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import yaml
from jinja2 import StrictUndefined, Template, meta, nodes, pass_context
from jinja2.exceptions import TemplateError as JinjaError
from jinja2.runtime import Context
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class TemplateError(ValueError):
    """Template loading or rendering failed."""


#: All declarable template axes; settings resolve labels by axis name. Unlisted keys fail loudly at load time.
TemplateAxis = Literal["content_mode", "generation_mode", "source_kind", "asset_type", "ad_duration_tier"]


#: Production steps for templates; settings label these values. Unlisted values fail loudly at load time.
TemplateStage = Literal[
    "source_overview",
    "episode_plan",
    "script_plan",
    "prompt_authoring",
    "asset_sheet",
    "storyboard_image",
    "grid",
    "storyboard_video",
    "reference_video",
    "style_analysis",
    "style",
    "agent_session",
]


class _Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AgentToolTrigger(_Trigger):
    """Render on Agent MCP calls; ``name`` is the tool name."""

    kind: Literal["agent_tool"]
    name: Literal[
        "plan_episodes",
        "generate_script_plan",
        "generate_episode_script",
    ]


class UserActionTrigger(_Trigger):
    """Render directly from a UI operation; ``name`` is its name, and ``agent_session`` means starting/resuming a session."""

    kind: Literal["user_action"]
    name: Literal["source_overview", "style_analysis", "style_selection", "agent_session", "draft_repair"]


class GenerationTaskTrigger(_Trigger):
    """Render during generation execution; ``name`` is task type, and ``asset`` covers character, scene, prop, product, and derivative images."""

    kind: Literal["generation_task"]
    name: Literal["asset", "storyboard", "video", "reference_video", "grid"]


#: Template triggers: three closed categories, each with a concrete name.
TemplateTrigger = Annotated[
    AgentToolTrigger | UserActionTrigger | GenerationTaskTrigger,
    Field(discriminator="kind"),
]


class TemplateMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    category: str
    title: str
    description: str
    stage: TemplateStage
    invoked_by: TemplateTrigger
    applies_to: dict[TemplateAxis, list[str]]
    slots: dict[str, str]
    protected: bool
    output_schema: str | None = None
    idempotent: bool = Field(default=False, exclude=True)


class _PartialHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    protected: bool = False


class PartialEntry(BaseModel):
    """Partial inventory entry: body without frontmatter, protection flag, and referencing template IDs in registration order."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str
    source: str
    protected: bool
    referenced_by: list[str]


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
        self._partial_sources: dict[str, tuple[str, bool]] = {}
        self._sources: dict[str, list[str]] = {}
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
        if metadata.id.startswith("partials/"):
            raise TemplateError(f"{path}: 模版 id 使用了保留前缀 partials/: {metadata.id}")
        if metadata.id in self._registry:
            raise TemplateError(f"{path}: 重复模版 id {metadata.id}")
        partials: dict[str, str] = {}
        referenced = self._collect(metadata, body, partials, set(), ())
        self._sources[metadata.id] = list(partials)
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
            # Expand nested partials in place; only body references carry markers for block-level decisions.
            if depth:
                return _Fragment(text)
            dedupe = _DEDUPE if metadata.idempotent and "lists" not in name.split("/") else _INJECT
            return _Fragment(f"{opening}{dedupe}{text}{closing}")

        @pass_context
        def variant(context: Context, family: str, axis_value: str, **kwargs: Any) -> str:
            return partial(context, f"{family}/{axis_value}", **kwargs)

        try:
            # First project body data only; second use it to determine whether block partials already occur in the body.
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

    def read_source(self, template_id: str) -> tuple[str, list[PartialEntry]]:
        """Template body and referenced partial inventory in first-reference order; expand variant families across all axis values."""
        _, body, _ = self._get(template_id)
        return body, [self._partial_entry(name) for name in self._sources[template_id]]

    def read_partial(self, name: str) -> PartialEntry:
        """Get one inventory entry by partial name; only partials referenced by at least one template are known."""
        if name not in self._partial_sources:
            raise TemplateError(f"未知片段: {name}")
        return self._partial_entry(name)

    def _partial_entry(self, name: str) -> PartialEntry:
        source, protected = self._partial_sources[name]
        referenced_by = [template_id for template_id, names in self._sources.items() if name in names]
        return PartialEntry(name=name, source=source, protected=protected, referenced_by=referenced_by)

    def _protect_newlines(self, value: Any) -> Any:
        """Replace slot newlines with placeholders so collapsing/block detection sees only template newlines; restore after rendering."""
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

    def _read_partial(self, name: str) -> str:
        """Read partial bodies; if frontmatter exists, read only its ``protected`` flag."""
        if name not in self._partial_sources:
            raw = self._partial_path(name).read_text(encoding="utf-8")
            protected = False
            if raw.startswith("---\n") and "\n---\n" in raw[4:]:
                header, raw = raw[4:].split("\n---\n", 1)
                try:
                    protected = _PartialHeader.model_validate(yaml.safe_load(header) or {}).protected
                except ValidationError as exc:
                    raise TemplateError(f"片段 {name}: frontmatter 无效: {exc}") from exc
            self._partial_sources[name] = raw, protected
        return self._partial_sources[name][0]

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
                text = self._read_partial(partial_name)
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
    """Rendered partial with internally protected slot newlines; do not process again on output."""


_DEDUPE = "+"
_INJECT = "-"


def _blank(*_args: Any, **_kwargs: Any) -> str:
    """Partials are empty in the first pass, retaining only slot-projected body text."""
    return ""


def _inject_fragments(rendered: str, opening: str, closing: str, body: str, newline_marker: str) -> str:
    """Remove partial markers, deduplicate block references linewise as needed, and remove whole empty block-reference lines.

    ``seen`` contains body text with restored slot newlines and accumulated injected partials. Deduplicate only
    against content preceding this partial; identical lines within the partial do not swallow each other.
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
    """Remove *text* lines already appearing as whole lines in *seen*; preserve spacing blank lines, trimming outer blanks."""
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
    """Whether *needle* appears as a complete line in *haystack*."""
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        if (start == 0 or haystack[start - 1] == "\n") and (end == len(haystack) or haystack[end] == "\n"):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _collapse_blank_lines(rendered: str) -> str:
    """Remove excess template spacing here; slot newlines remain placeholders and are unaffected."""
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
