#!/usr/bin/env python3
"""Unwrap hard line breaks in Chinese Markdown paragraphs.

Manually breaking paragraphs to limit source width fragments source reading and can add or
remove spaces at Chinese/Western text boundaries in some renderers. This script restores
paragraphs and list items to one source line each, read through editor soft wrapping.

Join boundaries follow Chinese/Western spacing rules: no space between Chinese characters,
one space between Chinese and half-width English/digits, and no spaces beside full-width punctuation.

The script scans structure before joining paragraphs. Preserve the following as entire blocks,
not just their opening lines:

- Markdown front matter
- Fenced code blocks, including fences after list markers (``- ```sh``)
- Indented code blocks, including code within list items
- Table rows containing pipes
- Headings, Setext underlines, thematic breaks
- HTML blocks, including multiline ``<pre>``, ``<script>``, ``<style>``, ``<textarea>``, and comments
- Link and footnote reference definitions
- Blockquotes, which may contain quotations needing verbatim preservation
- Explicit breaks ending in two spaces or a backslash

A standalone ``<!-- unwrap-disable-file -->`` outside code/HTML blocks skips the entire file;
the same marker within code examples has no effect.

Known simplifications: do not descend into blockquotes; spaces beyond four after a list marker
still count as marker prefix; unclosed fences extend to their list item’s end or the file end.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGETS = ["."]
FILE_IGNORE_MARKER = "<!-- unwrap-disable-file -->"
SKIP_DIR_NAMES = {".git", ".venv", "node_modules", "vendor"}
CONTINUATION_SLACK = 3
CODE_INDENT = 4

# Structural scan classifications for each line.
KIND_VERBATIM = "verbatim"  # Output verbatim and terminate the current join.
KIND_BLANK = "blank"
KIND_TEXT = "text"  # Body line eligible for joining.
KIND_LIST_ITEM = "list-item"  # First line of a list item.

# Fences may open after list markers; closing lines allow only fence characters and trailing whitespace.
FENCE_OPEN_RE = re.compile(
    r"^(\s*)((?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})(.*)$"
)
FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})[ \t]*$")

# HTML-block opening conditions and corresponding closing strings, applied in CommonMark decision order.
HTML_RAW_TEXT_ENDS = {
    "pre": "</pre",
    "script": "</script",
    "style": "</style",
    "textarea": "</textarea",
}
HTML_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|"
    "colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|"
    "footer|form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|hr|html|"
    "iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|"
    "option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|"
    "title|tr|track|ul"
)
HTML_RAW_TEXT_OPEN_RE = re.compile(
    r"^<(pre|script|style|textarea)(?=[\s/>]|$)", re.IGNORECASE
)
HTML_DECLARATION_RE = re.compile(r"^<![A-Za-z]")
HTML_BLOCK_TAG_RE = re.compile(
    rf"^</?(?:{HTML_BLOCK_TAGS})(?=[\s/>]|$)", re.IGNORECASE
)
HTML_TAG_LINE_RE = re.compile(r"^</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>[ \t]*$")

ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)\s*$")
THEMATIC_BREAK_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])([ \t]+)(\S.*)$")
BLOCKQUOTE_RE = re.compile(r"^ {0,3}>")
HTML_BLOCK_RE = re.compile(r"^ {0,3}<")
REFERENCE_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s")
TABLE_CELL_RE = re.compile(r"(?<!\\)\|")
HARD_BREAK_RE = re.compile(r"(?:[ \t]{2,}|\\)$")

# Wide-character ranges: Chinese, Japanese, Korean, full-width characters, etc.
WIDE_RE = re.compile(
    "["
    "ᄀ-ᅟ"  # Hangul letters
    "⺀-〾"  # CJK radicals and punctuation
    "ぁ-㏿"  # Kana, Bopomofo, compatibility characters
    "㐀-䶿"  # CJK Extension A
    "一-鿿"  # CJK basic block
    "ꀀ-꓏"  # Yi
    "가-힣"  # Hangul syllables
    "豈-﫿"  # CJK compatibility ideographs
    "︰-﹏"  # CJK compatibility forms
    "＀-￦"  # Full-width forms
    "]"
)
# Full-width punctuation; add no spaces beside it.
WIDE_PUNCT_RE = re.compile(
    "["
    "‘’“”"  # Curly quotation marks
    "—…"  # Dash, ellipsis
    "、。"  # Ideographic comma, full stop
    "《-】"  # Book-title marks, square brackets, corner quotation marks
    "〔-〟"  # Tortoise-shell brackets, etc.
    "！（），：；？～｟｠"
    "]"
)
# Inline markup to skip when joining; inspect actual visible characters for spacing.
EMPHASIS_CHARS = set("*_~`")
HEAD_SKIP_CHARS = EMPHASIS_CHARS | {"["}
# Exclude trailing link targets from spacing decisions; use the final link-text character.
LINK_TAIL_RE = re.compile(r"\]\((?:[^()\s]*)(?:\s+\"[^\"]*\")?\)$")
ASCII_TRAILING_NO_SPACE = set("([{<-/@#$&+=\\\"'")
ASCII_LEADING_NO_SPACE = set(",.;:!?)]}>%\"'")


@dataclass(frozen=True)
class Join:
    """One paragraph hard break, for command-line output."""

    file: Path
    line: int
    merged: int
    preview: str


def is_wide(char: str) -> bool:
    return bool(WIDE_RE.match(char)) or bool(WIDE_PUNCT_RE.match(char))


def is_wide_punct(char: str) -> bool:
    return bool(WIDE_PUNCT_RE.match(char))


def effective_tail(text: str) -> str:
    text = LINK_TAIL_RE.sub("", text)
    index = len(text) - 1
    while index >= 0 and text[index] in EMPHASIS_CHARS:
        index -= 1
    return text[index] if index >= 0 else ""


def effective_head(text: str) -> str:
    index = 0
    while index < len(text) and text[index] in HEAD_SKIP_CHARS:
        index += 1
    return text[index] if index < len(text) else ""


def joiner(left: str, right: str) -> str:
    """Determine whether joining two lines requires one space."""
    tail, head = effective_tail(left), effective_head(right)
    if not tail or not head:
        return ""

    if is_wide(tail) or is_wide(head):
        if is_wide(tail) and is_wide(head):
            return ""
        if is_wide_punct(tail) or is_wide_punct(head):
            return ""
        return " "
    if tail in ASCII_TRAILING_NO_SPACE or head in ASCII_LEADING_NO_SPACE:
        return ""
    return " "


def indent_width(line: str) -> int:
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += CODE_INDENT
        else:
            break
    return width


def visual_width(text: str) -> int:
    """Compute prefix length by column width to judge continuation indentation."""
    return sum(CODE_INDENT if char == "\t" else 1 for char in text)


def is_structural(line: str) -> bool:
    """Structural lines such as headings, tables, thematic breaks, and blockquotes do not join.

    Lines starting with ``<`` that do not form HTML blocks (e.g. autolinks) are also protected individually.
    """
    return bool(
        ATX_HEADING_RE.match(line)
        or SETEXT_UNDERLINE_RE.match(line)
        or THEMATIC_BREAK_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or HTML_BLOCK_RE.match(line)
        or REFERENCE_DEF_RE.match(line)
        or TABLE_CELL_RE.search(line)
    )


def html_block_end(content: str) -> str | None:
    """Determine whether content opens an HTML block; return its closing string or ``blank``.

    Return ``None`` when it does not; :func:`is_structural` still protects that line individually.
    """
    text = content.lstrip()
    if not text.startswith("<"):
        return None
    lowered = text.lower()
    if lowered.startswith("<!--"):
        return "-->"
    if lowered.startswith("<?"):
        return "?>"
    if lowered.startswith("<![cdata["):
        return "]]>"
    if HTML_DECLARATION_RE.match(text):
        return ">"
    raw_text = HTML_RAW_TEXT_OPEN_RE.match(text)
    if raw_text:
        return HTML_RAW_TEXT_ENDS[raw_text.group(1).lower()]
    if HTML_BLOCK_TAG_RE.match(text) or HTML_TAG_LINE_RE.match(text):
        return "blank"
    return None


def fence_info_is_valid(delimiter: str, info: str) -> bool:
    """Backtick fence info strings cannot contain backticks; such text is inline code instead."""
    return not (delimiter[0] == "`" and "`" in info)


def hard_break_suffix(line: str) -> str:
    """Return an explicit line-end break marker: two or more whitespace characters or a backslash."""
    match = HARD_BREAK_RE.search(line)
    return match.group(0) if match else ""


def interrupts_paragraph(line: str) -> bool:
    """Lines that interrupt paragraphs: structural lines, opening fences, and list items; not indented code."""
    return bool(
        is_structural(line) or FENCE_OPEN_RE.match(line) or LIST_ITEM_RE.match(line)
    )


def pop_containers(containers: list[int], indent: int) -> None:
    """Leave corresponding list containers when indentation decreases."""
    while containers and indent < containers[-1]:
        containers.pop()


@dataclass(frozen=True)
class ScanLine:
    """Structural scan result for one line."""

    line: int
    raw: str
    kind: str
    indent: int
    prefix: str = ""
    content: str = ""
    hard_break: str = ""


@dataclass(frozen=True)
class Scan:
    """Structural scan results for an entire file."""

    lines: tuple[ScanLine, ...]
    ignore_file: bool


@dataclass
class _Fence:
    """An open fenced code block."""

    char: str
    length: int
    indent: int
    floor: int


@dataclass
class _HtmlBlock:
    """An open HTML block."""

    end: str
    floor: int


def scan_structure(text: str) -> Scan:
    """Determine line by line what stays verbatim and what may join."""
    lines = text.splitlines()
    result: list[ScanLine] = []
    ignore_file = False
    in_front_matter = bool(lines and lines[0].strip() == "---")
    fence: _Fence | None = None
    html: _HtmlBlock | None = None
    code_floor: int | None = None
    containers: list[int] = []
    paragraph_open = False

    index = 0
    while index < len(lines):
        raw = lines[index]
        line_no = index + 1
        stripped = raw.strip()
        indent = indent_width(raw)

        def verbatim() -> ScanLine:
            return ScanLine(line_no, raw, KIND_VERBATIM, indent)

        if in_front_matter:
            result.append(verbatim())
            if line_no != 1 and stripped in {"---", "..."}:
                in_front_matter = False
            index += 1
            continue

        if fence is not None:
            if stripped and indent < fence.floor:
                # Indentation left the container; an unclosed fence ends with its list item. Reevaluate this line.
                fence = None
                continue
            result.append(verbatim())
            close = FENCE_CLOSE_RE.match(raw)
            if (
                close
                and close.group(1)[0] == fence.char
                and len(close.group(1)) >= fence.length
                and fence.floor <= indent <= fence.floor + CONTINUATION_SLACK
            ):
                fence = None
            index += 1
            continue

        if html is not None:
            if html.end == "blank":
                if not stripped:
                    result.append(ScanLine(line_no, raw, KIND_BLANK, indent))
                    html = None
                    paragraph_open = False
                    index += 1
                    continue
            elif stripped and indent < html.floor:
                html = None
                continue
            result.append(verbatim())
            if html.end != "blank" and html.end in raw.lower():
                html = None
            index += 1
            continue

        if not stripped:
            result.append(ScanLine(line_no, raw, KIND_BLANK, indent))
            paragraph_open = False
            index += 1
            continue

        if not (paragraph_open and not interrupts_paragraph(raw)):
            # Lazy paragraph continuation stays in the list container; otherwise pop by indentation.
            pop_containers(containers, indent)
        floor = containers[-1] if containers else 0

        if code_floor is not None:
            if indent >= code_floor:
                result.append(verbatim())
                index += 1
                continue
            code_floor = None

        # Detect indented code before list items, or `    - first` would count as a list.
        if not paragraph_open and indent >= floor + CODE_INDENT:
            code_floor = floor + CODE_INDENT
            result.append(verbatim())
            index += 1
            continue

        opener = FENCE_OPEN_RE.match(raw)
        if opener and fence_info_is_valid(opener.group(3), opener.group(4)):
            marker = opener.group(2)
            if marker:
                # Opening fence follows a list marker; preserve the whole line and assign code to the list body column.
                prefix = f"{opener.group(1)}{marker}"
                content_indent = visual_width(prefix)
                containers.append(content_indent)
                fence = _Fence(
                    opener.group(3)[0],
                    len(opener.group(3)),
                    content_indent,
                    content_indent,
                )
            else:
                fence = _Fence(
                    opener.group(3)[0], len(opener.group(3)), indent, floor
                )
            result.append(verbatim())
            paragraph_open = False
            index += 1
            continue

        if stripped == FILE_IGNORE_MARKER and indent < CODE_INDENT:
            # Only standalone markers outside code/HTML blocks skip the entire file.
            ignore_file = True
            result.append(verbatim())
            paragraph_open = False
            index += 1
            continue

        block_end = html_block_end(raw)
        if block_end is not None:
            result.append(verbatim())
            if block_end == "blank" or block_end not in raw.lower():
                html = _HtmlBlock(block_end, floor)
            paragraph_open = False
            index += 1
            continue

        item = LIST_ITEM_RE.match(raw)
        if item:
            lead, marker, spaces, content = item.groups()
            prefix = f"{lead}{marker}{spaces}"
            content_indent = visual_width(prefix)
            containers.append(content_indent)
            item_html = html_block_end(content)
            if item_html is not None:
                # HTML blocks after list markers are likewise preserved as complete blocks.
                result.append(verbatim())
                if item_html == "blank" or item_html not in raw.lower():
                    html = _HtmlBlock(item_html, content_indent)
                paragraph_open = False
            else:
                result.append(
                    ScanLine(
                        line_no,
                        raw,
                        KIND_LIST_ITEM,
                        indent,
                        prefix,
                        content.rstrip(),
                        hard_break_suffix(raw),
                    )
                )
                paragraph_open = True
            index += 1
            continue

        if is_structural(raw):
            if SETEXT_UNDERLINE_RE.match(raw) and paragraph_open:
                # The preceding paragraph belongs to this Setext heading.
                # Preserve every title line, not only its underline.
                previous = len(result) - 1
                while previous >= 0 and result[previous].kind == KIND_TEXT:
                    item = result[previous]
                    result[previous] = ScanLine(
                        item.line, item.raw, KIND_VERBATIM, item.indent
                    )
                    previous -= 1
            result.append(verbatim())
            paragraph_open = False
            index += 1
            continue

        leading = raw[: len(raw) - len(raw.lstrip())]
        result.append(
            ScanLine(
                line_no,
                raw,
                KIND_TEXT,
                indent,
                leading,
                stripped,
                hard_break_suffix(raw),
            )
        )
        paragraph_open = True
        index += 1

    return Scan(tuple(result), ignore_file)


def has_file_ignore_marker(text: str) -> bool:
    """Determine whether the file contains an effective whole-file skip marker."""
    return scan_structure(text).ignore_file


class _Block:
    """A paragraph or list item currently being joined."""

    def __init__(self, prefix: str, text: str, start_line: int) -> None:
        self.prefix = prefix
        self.text = text
        self.start_line = start_line
        # Body starting column, the reference for continuation indentation and indented-code detection.
        self.content_indent = visual_width(prefix)
        self.joined_lines: list[int] = []

    def append(self, line_no: int, content: str) -> None:
        self.text += joiner(self.text, content) + content
        self.joined_lines.append(line_no)

    @property
    def merged(self) -> int:
        return len(self.joined_lines)

    def render(self, suffix: str = "") -> str:
        return f"{self.prefix}{self.text}{suffix}"


def multiline_code_lines(scan: Scan) -> set[int]:
    """Protect complete lines containing a code span across paragraph lines."""
    protected: set[int] = set()
    group: list[ScanLine] = []

    def finish() -> None:
        text = "\n".join(item.raw for item in group)
        runs = list(re.finditer(r"`+", text))
        index = 0
        while index < len(runs):
            opening = runs[index]
            # Backslash escapes apply outside code spans only.
            before = text[:opening.start()]
            if (len(before) - len(before.rstrip("\\"))) % 2:
                index += 1
                continue
            closing_index = next(
                (j for j in range(index + 1, len(runs))
                 if len(runs[j].group()) == len(opening.group())), None
            )
            if closing_index is None:
                index += 1
                continue
            closing = runs[closing_index]
            start = text.count("\n", 0, opening.start())
            end = text.count("\n", 0, closing.end())
            if end > start:
                protected.update(item.line for item in group[start:end + 1])
            index = closing_index + 1
        group.clear()

    for item in scan.lines:
        if item.kind != KIND_TEXT:
            finish()
        if item.kind in {KIND_TEXT, KIND_LIST_ITEM}:
            group.append(item)
    finish()
    return protected


def join_scanned(scan: Scan) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Join paragraphs according to scan results; handle body lines without reevaluating structure."""
    output: list[str] = []
    joins: list[tuple[int, int, str]] = []
    block: _Block | None = None
    protected = multiline_code_lines(scan)

    def close(current: _Block, suffix: str = "") -> None:
        rendered = current.render(suffix)
        output.append(rendered)
        if current.joined_lines:
            joins.append((current.start_line, current.merged, rendered.strip()[:60]))

    def flush() -> None:
        nonlocal block
        if block is not None:
            close(block)
            block = None

    for item in scan.lines:
        if item.kind in {KIND_VERBATIM, KIND_BLANK} or item.line in protected:
            flush()
            output.append(item.raw)
            continue

        if item.kind == KIND_LIST_ITEM:
            flush()
            block = _Block(item.prefix, item.content, item.line)
        elif block is not None:
            if item.indent > block.content_indent + CONTINUATION_SLACK:
                # Indentation is much deeper than the body; conservatively preserve it verbatim.
                flush()
                output.append(item.raw)
                continue
            block.append(item.line, item.content)
        else:
            block = _Block(item.prefix, item.content, item.line)

        if item.hard_break and block is not None:
            # Two trailing spaces or a backslash mark explicit breaks; preserve them and finish the current join.
            suffix = item.hard_break
            if block.text.endswith(suffix):
                block.text = block.text[: -len(suffix)]
            close(block, suffix)
            block = None

    flush()
    return output, joins


def unwrap_text(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Return unwrapped text and each hard break’s starting line, merged-line count, and preview."""
    scan = scan_structure(text)
    if scan.ignore_file:
        return text, []

    output, joins = join_scanned(scan)
    result = "\n".join(output)
    if text.endswith("\n"):
        result += "\n"
    return result, joins


def collect_targets(raw_targets: list[str]) -> list[Path]:
    targets: list[Path] = []
    for item in raw_targets:
        path = Path(item)
        if not path.exists():
            print(f"[WARN] 文件不存在，已跳过: {item}", file=sys.stderr)
            continue
        if path.is_dir():
            for markdown in sorted(path.rglob("*.md")):
                if any(part in SKIP_DIR_NAMES for part in markdown.parts):
                    continue
                targets.append(markdown)
        else:
            targets.append(path)

    return sorted(
        {path.resolve(): path for path in targets}.values(),
        key=lambda path: str(path),
    )


def process_file(path: Path) -> tuple[str, list[Join]]:
    """Read a file and unwrap hard breaks; :func:`scan_structure` centrally handles skip markers."""
    original = path.read_text(encoding="utf-8")
    unwrapped, joins = unwrap_text(original)
    return unwrapped, [
        Join(file=path, line=line, merged=merged, preview=preview)
        for line, merged, preview in joins
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="展开中文 Markdown 段落的硬换行")
    parser.add_argument(
        "files",
        nargs="*",
        help="要处理的 Markdown 文件或目录；为空时处理当前目录",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只报告段落硬换行，不写回文件；发现问题时返回 2",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="把结果打印到标准输出，不写回文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = collect_targets(args.files if args.files else DEFAULT_TARGETS)
    if not targets:
        print("未找到可处理的 Markdown 文件。", file=sys.stderr)
        return 1

    findings: list[Join] = []
    changed: list[Path] = []

    for path in targets:
        unwrapped, joins = process_file(path)
        if args.stdout:
            print(unwrapped, end="")
            continue
        if not joins:
            continue
        findings.extend(joins)
        changed.append(path)
        if not args.check:
            path.write_text(unwrapped, encoding="utf-8")

    if args.stdout:
        return 0

    if not findings:
        print(f"PASS: 共检查 {len(targets)} 个文件，段落未发现硬换行。")
        return 0

    for item in findings:
        print(
            f"- {item.file}:{item.line} 段落硬换行，"
            f"共 {item.merged + 1} 行\n  {item.preview}"
        )

    if args.check:
        print(
            f"FAIL: 共检查 {len(targets)} 个文件；"
            f"{len(changed)} 个文件存在段落硬换行，共 {len(findings)} 处。"
        )
        return 2

    print(
        f"DONE: 共检查 {len(targets)} 个文件；"
        f"已展开 {len(changed)} 个文件，共 {len(findings)} 处硬换行。"
    )
    print("请复查拼接边界的中西文留白，并确认列表、表格和代码结构未受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
