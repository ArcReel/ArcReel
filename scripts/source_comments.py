"""Locate source comments without treating string literals and data as comments."""

from __future__ import annotations

import bisect
import io
import re
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Literal

from tree_sitter import Node, Parser  # deptry: ignore[DEP004]  # development-only source lint
from tree_sitter_language_pack import get_parser  # deptry: ignore[DEP004]  # development-only source lint

Grammar = Literal[
    "bash", "css", "dockerfile", "html", "ini", "javascript", "json", "toml", "tsx", "typescript", "xml", "yaml"
]
Language = Grammar | Literal["dotenv", "ignore", "jq", "mako", "python"]

_SUFFIX_LANGUAGES: dict[str, Language] = {
    ".bash": "bash",
    ".cjs": "javascript",
    ".css": "css",
    ".cts": "typescript",
    ".html": "html",
    ".ini": "ini",
    ".jq": "jq",
    ".js": "javascript",
    ".jsonc": "json",
    ".jsx": "javascript",
    ".mako": "mako",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".py": "python",
    ".pyi": "python",
    ".sh": "bash",
    ".svg": "xml",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_IGNORE_NAMES = {".dockerignore", ".gitattributes", ".gitignore", ".prettierignore", ".worktreeinclude", "CODEOWNERS"}
_COMMENT_NODES = {"Comment", "comment", "hash_bang_line"}
_GITHUB_EXPRESSION = re.compile(rb"\$\{\{(?:'(?:[^']|'')*+'|(?!\}\})[^'])*+\}\}")
_ENCODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+", re.ASCII)
_IGNORE_COMMENT = re.compile(rb"^[ \t]*(?P<comment>#[^\r\n]*)", re.MULTILINE)
_GITIGNORE_COMMENT = re.compile(rb"^(?P<comment>#[^\r\n]*)", re.MULTILINE)
_MAKO_COMMENT = re.compile(rb"(?m)^[ \t]*(?P<line>##[^\r\n]*)|(?P<block><%doc>.*?</%doc>)", re.DOTALL)
_DOTENV_TOKEN = re.compile(
    rb"""(?m)^[ \t]*(?:(\#[^\r\n]*)|(?:export[ \t]+)?(?:'[^'\r\n]+'|[^#=\s]+)[ \t]*="""
    rb"""(?:[ \t]*(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")[ \t]*(\#[^\r\n]*)?"""
    rb"""|[^\r\n]*?(?:[ \t]+(\#[^\r\n]*))?)[ \t]*(?=\r?$))"""
)
_JQ_EXPRESSION_TOKEN = re.compile(rb'#[^\r\n]*|["()]')
_JQ_STRING_TOKEN = re.compile(rb'\\.|"', re.DOTALL)
_LICENSE = re.compile(
    r"^(?:spdx-(?:license-identifier|filecopyrighttext)\s*:|"
    r"(?:copyright\s+(?:\(c\)\s*)?(?:©\s*)?\d{4}|©\s*\d{4}))",
    re.IGNORECASE,
)
_PYTHON_DIRECTIVE = re.compile(
    r"^(?:noqa\b|ruff:\s*noqa\b|(?:pyright|mypy|deptry|type):\s*ignore(?=$|[\s\[])|"
    r"(?:pyright|mypy):\s*[\w-]+\s*=|fmt:\s*(?:on|off|skip)\b|"
    r"isort:\s*(?:skip|skip_file|off|on|split)\b|pragma:\s*no\s+(?:cover|branch)\b)"
)
_JS_DIRECTIVE = re.compile(
    r"^(?:(?:eslint-(?:disable(?:-next-line|-line)?|enable|env)|"
    r"prettier-ignore(?:-start|-end)?|(?:istanbul|c8|v8) ignore|"
    r"@(?:license|preserve|ts-nocheck|ts-check|ts-expect-error|ts-ignore)|"
    r"@vite-ignore|[@#]__PURE__|[@#]__NO_SIDE_EFFECTS__)\b)"
)
_JSDOC_MARKER = re.compile(r"(?m)^[ \t]*@(?:enqueue|public)\b")
_KNIP_PUBLIC_REASON = re.compile(r"(?m)^[ \t]*@public\b[^\r\n]*")
_SHELL_DIRECTIVE = re.compile(r"^shellcheck\s+(?:disable|enable|source|source-path|shell)\s*=")
_YAML_DIRECTIVE = re.compile(r"^zizmor:\s*ignore(?:\[[^\]\n]*\])?(?=$|[\s:])")
_JS_LANGUAGES = {"javascript", "typescript", "tsx"}


@dataclass(frozen=True)
class Comment:
    """A comment at one-based lines and zero-based Unicode columns."""

    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    raw: str
    directive: bool
    prose: str


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    language: Language


def language_for(path: str | Path) -> Language | None:
    """Return the source language for a supported path."""
    name = Path(path).name
    if name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile"):
        return "dockerfile"
    if name == ".env" or name.startswith(".env."):
        return "dotenv"
    if name in _IGNORE_NAMES:
        return "ignore"
    if name.startswith("tsconfig.") and name.endswith(".json"):
        return "json"
    return _SUFFIX_LANGUAGES.get(Path(path).suffix.lower())


def extract_comments(path: str | Path, source: str) -> list[Comment]:
    """Return the physical spans of comments in a source file."""
    language = language_for(path)
    if language is None:
        return []
    path = Path(path).as_posix()
    data = source.encode("utf-8")
    line_starts = _line_starts(data)
    comments = []
    spans = sorted(set(_spans(language, data, path)), key=lambda span: (span.start, span.end))
    for span in spans:
        raw_bytes = data[span.start : span.end].rstrip(b"\r\n")
        raw = raw_bytes.decode("utf-8")
        start_line, start_col = _position(data, line_starts, span.start)
        end_line, end_col = _position(data, line_starts, span.start + len(raw_bytes))
        comment_text = _comment_text(raw)
        line_start = line_starts[start_line - 1]
        before = data[line_start : span.start].decode("utf-8")
        directive, prose = _comment_prose(raw, comment_text, before, start_line, span.language, path)
        comments.append(Comment(path, start_line, start_col, end_line, end_col, raw, directive, prose))
    return comments


def _line_starts(data: bytes) -> list[int]:
    return [0, *(match.end() for match in re.finditer(rb"\n", data))]


def _position(data: bytes, line_starts: list[int], offset: int) -> tuple[int, int]:
    index = bisect.bisect_right(line_starts, offset) - 1
    return index + 1, len(data[line_starts[index] : offset].decode("utf-8"))


def _comment_text(raw: str) -> str:
    for opening, closing in (("<!--", "-->"), ("<%doc>", "</%doc>"), ("/*", "*/")):
        if raw.startswith(opening):
            body = raw.removeprefix(opening).removesuffix(closing)
            if opening == "/*":
                body = re.sub(r"(?m)^[ \t]*\*(?:[ \t]|$)", "", body)
            return body.strip()
    return re.sub(r"^(?:/{2,3}|#{1,2}|;)[ \t]*", "", raw).strip()


def _comment_prose(raw: str, body: str, before: str, line: int, language: Language, path: str) -> tuple[bool, str]:
    if (raw.startswith("#!") and line == 1 and language in _JS_LANGUAGES | {"bash", "python"}) or (
        language == "python" and line <= 2 and not before.strip() and _ENCODING.match(raw)
    ):
        return True, ""
    if language == "dockerfile" and re.fullmatch(r"(?:syntax|escape|check)\s*=.*", body, re.IGNORECASE):
        return True, ""
    if (
        language == "python"
        and (path.startswith(("lib/", "server/")) or (path.startswith("packages/") and "/src/" in path))
        and body.startswith("hard-delete:")
    ):
        return True, ""
    if language in {"html", "xml"} and (body.startswith("[if ") or raw.startswith("<!--#")):
        return True, ""
    if (
        language == "yaml"
        and _is_github_path(path)
        and re.fullmatch(
            r"\s*(?:-\s*)?uses:\s*['\"]?(?:[\w./-]+@[0-9a-f]{40}|docker://[^@\s]+@sha256:[0-9a-f]{64})['\"]?\s*", before
        )
    ):
        version = re.match(r"v?\d+(?:\.\d+)*(?:[-+][\w.-]+)?(?=$|\s)", body)
        if version:
            return True, body[version.end() :].strip(" -–—")
    if _LICENSE.match(body):
        return True, ""
    if language in _JS_LANGUAGES:
        if raw.startswith("///") and body.startswith("<reference "):
            return True, ""
        if raw.startswith("/**") and _JSDOC_MARKER.search(body):
            prose = _JSDOC_MARKER.sub("", _KNIP_PUBLIC_REASON.sub("", body)).strip()
            return True, re.sub(r"(?m)^\s*(?:--|#|:)\s*", "", prose)
    if (
        (language == "python" and _PYTHON_DIRECTIVE.match(body))
        or (language in _JS_LANGUAGES and _JS_DIRECTIVE.match(body))
        or (language == "bash" and _SHELL_DIRECTIVE.match(body))
        or (language == "yaml" and _is_github_path(path) and _YAML_DIRECTIVE.match(body))
    ):
        return True, ""
    return False, body


def _is_github_path(path: str) -> bool:
    return ".github" in Path(path).parts


@cache
def _parser(language: Grammar) -> Parser:
    return get_parser(language)


def _spans(language: Language, data: bytes, path: str) -> Iterator[_Span]:
    if language == "python":
        yield from _python_spans(data)
    elif language == "mako":
        yield from _mako_spans(data, path)
    elif language == "jq":
        yield from _jq_spans(data)
    elif language in {"dotenv", "ignore"}:
        yield from _simple_spans(language, data, path)
    else:
        yield from _tree_spans(language, data, path)


def _python_spans(data: bytes) -> Iterator[_Span]:
    source = data.decode("utf-8")
    lines = source.split("\n")
    starts = _line_starts(data)
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                row, column = token.start
                start = starts[row - 1] + len(lines[row - 1][:column].encode("utf-8"))
                yield _Span(start, start + len(token.string.encode("utf-8")), "python")
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return


def _mako_spans(data: bytes, path: str) -> Iterator[_Span]:
    masked = bytearray(data)
    for match in _MAKO_COMMENT.finditer(data):
        group = match.lastgroup
        assert group is not None
        start, end = match.span(group)
        yield _Span(start, end, "mako")
        masked[start:end] = re.sub(rb"[^\r\n]", b" ", data[start:end])
    if path.endswith(".py.mako"):
        yield from _python_spans(bytes(masked))


def _jq_spans(data: bytes) -> Iterator[_Span]:
    closing_tokens: list[bytes] = []
    offset = 0
    while offset < len(data):
        in_string = bool(closing_tokens) and closing_tokens[-1] == b'"'
        pattern = _JQ_STRING_TOKEN if in_string else _JQ_EXPRESSION_TOKEN
        match = pattern.search(data, offset)
        if match is None:
            return
        token = match[0]
        offset = match.end()
        if closing_tokens and token == closing_tokens[-1]:
            closing_tokens.pop()
        elif token == b'"':
            closing_tokens.append(b'"')
        elif token == rb"\(" or (token == b"(" and closing_tokens):
            closing_tokens.append(b")")
        elif token.startswith(b"#"):
            yield _Span(match.start(), offset, "jq")


def _simple_spans(language: Literal["dotenv", "ignore"], data: bytes, path: str) -> Iterator[_Span]:
    ignore_comment = _GITIGNORE_COMMENT if Path(path).name in {".gitignore", ".prettierignore"} else _IGNORE_COMMENT
    pattern = _DOTENV_TOKEN if language == "dotenv" else ignore_comment
    for match in pattern.finditer(data):
        if match.lastindex is not None:
            start, end = match.span(match.lastindex)
            yield _Span(start, end, language)


def _tree_spans(language: Grammar, data: bytes, path: str) -> Iterator[_Span]:
    tree = _parser(language).parse(data)
    cursor = tree.walk()
    while True:
        node = cursor.node
        if node.type in _COMMENT_NODES:
            if _is_source_comment(node, language, data):
                yield _Span(node.start_byte, node.end_byte, language)
        elif language == "yaml" and node.type in {"block_mapping_pair", "flow_pair"} and _is_github_path(path):
            yield from _yaml_embedding(node, data, path)
        elif language == "html" and node.type == "raw_text":
            yield from _html_embedding(node, data, path)
        elif language == "dockerfile" and node.type == "heredoc_block":
            yield from _docker_embedding(node, data, path)
        elif language == "dockerfile" and node.type == "shell_command":
            if node.parent is not None and node.parent.type == "run_instruction":
                yield from _shifted_spans("bash", data[node.start_byte : node.end_byte], node.start_byte, path)
        elif language == "bash" and node.type == "heredoc_body":
            yield from _bash_embedding(node, data, path)
        if node.type not in _COMMENT_NODES and cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return


def _is_source_comment(node: Node, language: Grammar, data: bytes) -> bool:
    if language == "dockerfile":
        line_start = node.start_byte - node.start_point.column
        return not data[line_start : node.start_byte].strip()
    return language != "html" or node.parent is None or node.parent.type != "quoted_attribute_value"


def _yaml_embedding(node: Node, data: bytes, path: str) -> Iterator[_Span]:
    key = node.child_by_field_name("key")
    value = node.child_by_field_name("value")
    if key is None or value is None or value.named_child_count != 1:
        return
    key_text = data[key.start_byte : key.end_byte].strip(b"'\x22")
    if key_text not in {b"run", b"filters", b"script"}:
        return
    step = node.parent
    if step is None:
        return
    if key_text in {b"filters", b"script"}:
        with_pair = _yaml_parent(step)
        if with_pair is None or _yaml_pair(with_pair, data)[0] != b"with" or with_pair.parent is None:
            return
        step = with_pair.parent
    sequence = _yaml_parent(step)
    if sequence is None or sequence.type not in {"block_sequence", "flow_sequence"}:
        return
    steps_pair = _yaml_parent(sequence)
    steps_key = steps_pair.child_by_field_name("key") if steps_pair is not None else None
    if steps_key is None or data[steps_key.start_byte : steps_key.end_byte].strip(b"'\x22") != b"steps":
        return
    pairs = dict(_yaml_pair(pair, data) for pair in step.named_children)
    uses = pairs.get(b"uses", b"")
    language: Language
    if key_text == b"run":
        shell = pairs.get(b"shell", b"bash").split(maxsplit=1)
        if not shell or shell[0] not in {b"bash", b"sh"}:
            return
        language = "bash"
    elif key_text == b"filters" and uses.startswith(b"dorny/paths-filter@"):
        language = "yaml"
    elif key_text == b"script" and uses.startswith(b"actions/github-script@"):
        language = "javascript"
    else:
        return
    scalar = value.named_children[0]
    scalar_text = data[scalar.start_byte : scalar.end_byte]
    if scalar.type == "block_scalar":
        header, newline, content = scalar_text.partition(b"\n")
        if newline:
            indicator = re.match(rb"[|>][+-]?([1-9])", header)
            indent = key.start_point.column + int(indicator[1]) if indicator else None
            yield from _dedented_spans(
                language,
                _mask_github_expressions(content),
                scalar.start_byte + len(header) + 1,
                path,
                indent=indent,
                folded=header.startswith(b">"),
            )
    elif scalar.type == "plain_scalar" and b"\n" not in scalar_text:
        yield from _shifted_spans(language, _mask_github_expressions(scalar_text), scalar.start_byte, path)
    elif scalar.type in {"single_quote_scalar", "double_quote_scalar"} and b"\n" not in scalar_text:
        content = scalar_text[1:-1]
        if (scalar.type == "single_quote_scalar" and b"''" in content) or (
            scalar.type == "double_quote_scalar" and b"\\" in content
        ):
            return
        yield from _shifted_spans(language, _mask_github_expressions(content), scalar.start_byte + 1, path)


def _yaml_parent(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None and parent.type in {"block_node", "flow_node", "block_sequence_item"}:
        parent = parent.parent
    return parent


def _mask_github_expressions(data: bytes) -> bytes:
    """Replace workflow expressions while retaining physical byte offsets."""
    return _GITHUB_EXPRESSION.sub(lambda match: re.sub(rb"[^\r\n\t ]", b"_", match[0]), data)


def _yaml_pair(node: Node, data: bytes) -> tuple[bytes, bytes]:
    key = node.child_by_field_name("key")
    value = node.child_by_field_name("value")
    if key is None or value is None:
        return b"", b""
    return (
        data[key.start_byte : key.end_byte].strip(b"'\x22"),
        data[value.start_byte : value.end_byte].strip(b"'\x22"),
    )


def _html_embedding(node: Node, data: bytes, path: str) -> Iterator[_Span]:
    parent = node.parent
    if parent is None or parent.type not in {"script_element", "style_element"}:
        return
    start_tag = parent.named_children[0]
    attributes = {}
    for attribute in start_tag.named_children:
        if attribute.type == "attribute":
            name, _, value = data[attribute.start_byte : attribute.end_byte].partition(b"=")
            attributes[name.strip().lower()] = value.strip().strip(b"'\x22").lower()
    mime_type = attributes.get(b"type", b"").split(b";", 1)[0]
    if parent.type == "style_element" and mime_type in {b"", b"text/css"}:
        language = "css"
    elif parent.type == "script_element" and mime_type in {
        b"",
        b"module",
        b"text/javascript",
        b"application/javascript",
        b"text/ecmascript",
        b"application/ecmascript",
    }:
        language = "javascript"
    else:
        return
    yield from _shifted_spans(language, data[node.start_byte : node.end_byte], node.start_byte, path)


def _docker_embedding(node: Node, data: bytes, path: str) -> Iterator[_Span]:
    if node.parent is None or node.parent.type != "run_instruction":
        return
    command = node.prev_named_sibling
    if command is None or command.type != "shell_command" or command.named_child_count != 1:
        return
    fragment = command.named_children[0]
    marker = next((child for child in fragment.named_children if child.type == "heredoc_marker"), None)
    terminator = next((child for child in node.named_children if child.type == "heredoc_end"), None)
    if marker is None or terminator is None:
        return
    trailing = data[marker.end_byte : command.end_byte]
    preceding = data[command.start_byte : marker.start_byte]
    invocation = (preceding + b" " + trailing).split()
    language = _stdin_language(invocation) if invocation else "bash"
    if language:
        start = data.find(b"\n", node.start_byte, terminator.start_byte) + 1
        if start:
            yield from _shifted_spans(language, data[start : terminator.start_byte], start, path)


def _bash_embedding(node: Node, data: bytes, path: str) -> Iterator[_Span]:
    redirect = node.parent
    if redirect is None or redirect.parent is None:
        return
    command = redirect.parent.child_by_field_name("body")
    if command is None:
        return
    name = command.child_by_field_name("name")
    if name is None:
        return
    language = _stdin_language(data[name.start_byte : redirect.start_byte].split())
    if language is None:
        return
    start = next((child for child in redirect.named_children if child.type == "heredoc_start"), None)
    if start is None or not data[start.start_byte : start.end_byte].startswith((b"'", b'"', b"\\")):
        return
    text = data[node.start_byte : node.end_byte]
    if data[redirect.start_byte : redirect.start_byte + 3] == b"<<-":
        yield from _dedented_spans(language, text, node.start_byte, path, tabs=True)
    else:
        yield from _shifted_spans(language, text, node.start_byte, path)


def _stdin_language(invocation: list[bytes]) -> Language | None:
    if not invocation:
        return None
    executable = invocation[0].strip(b"'\x22").rsplit(b"/", 1)[-1]
    arguments = invocation[1:]
    if re.fullmatch(rb"python(?:[23](?:\.\d+)?)?", executable) and (not arguments or arguments[0] == b"-"):
        return "python"
    if executable in {b"bash", b"sh"} and (not arguments or arguments[0] == b"-s"):
        return "bash"
    return None


def _shifted_spans(language: Language, data: bytes, offset: int, path: str) -> Iterator[_Span]:
    for span in _spans(language, data, path):
        yield _Span(offset + span.start, offset + span.end, span.language)


def _dedented_spans(
    language: Language,
    data: bytes,
    offset: int,
    path: str,
    *,
    indent: int | None = None,
    tabs: bool = False,
    folded: bool = False,
) -> Iterator[_Span]:
    original_starts = _line_starts(data)
    lines = data.split(b"\n")
    if tabs:
        stripped = [line.lstrip(b"\t") for line in lines]
    else:
        if indent is None:
            indent = min((len(line) - len(line.lstrip(b" ")) for line in lines if line.strip()), default=0)
        stripped = [line[min(indent, len(line) - len(line.lstrip(b" "))) :] for line in lines]
    padding = [len(original) - len(dedented) for original, dedented in zip(lines, stripped, strict=True)]
    if folded:
        stripped = [line.removesuffix(b"\r") for line in stripped]
    separators = _folded_separators(stripped) if folded else [b"\n"] * (len(stripped) - 1)
    embedded = bytearray()
    embedded_starts = []
    for line, separator in zip(stripped, [*separators, b""], strict=True):
        embedded_starts.append(len(embedded))
        embedded.extend(line)
        embedded.extend(separator)

    def original_offset(pos: int) -> int:
        row = bisect.bisect_right(embedded_starts, pos) - 1
        return offset + original_starts[row] + padding[row] + pos - embedded_starts[row]

    for span in _spans(language, bytes(embedded), path):
        yield _Span(original_offset(span.start), original_offset(span.end - 1) + 1, span.language)


def _folded_separators(lines: list[bytes]) -> list[bytes]:
    separators = [b"\n"] * (len(lines) - 1)
    content_rows = [index for index, line in enumerate(lines) if line]
    for previous, current in pairwise(content_rows):
        if not lines[previous].startswith((b" ", b"\t")) and not lines[current].startswith((b" ", b"\t")):
            separators[previous] = b" " if current == previous + 1 else b""
    return separators
