#!/usr/bin/env python3
"""展开中文 Markdown 段落中的硬换行。

为了控制源码行宽而在段落中间手动断行，会带来两个问题：源文件读起来很碎；
中西文边界上的断行在部分渲染器里会多出或吞掉空格。本脚本把段落和列表项还原为
「一段一行」，正文靠编辑器软换行阅读。

拼接边界按中西文留白规则处理：中文与中文之间不加空格，中文与半角英文、数字之间
加一个空格，全角标点两侧不加空格。

脚本先扫描结构，再拼接段落。以下内容按整块保留，不只保护起始行：

- Markdown front matter
- 围栏代码块，包括写在列表标记之后的围栏（``- ```sh``）
- 缩进代码块，包括列表项内的缩进代码
- 含竖线的表格行
- 标题、Setext 下划线、分隔线
- HTML 块，包括多行 ``<pre>``、``<script>``、``<style>``、``<textarea>`` 和注释
- 链接与脚注引用定义
- 引用块，它可能是需要保持原样的引用
- 以两个空格或反斜杠结尾的显式换行

独立成行、且不在代码块和 HTML 块内的 ``<!-- unwrap-disable-file -->`` 会让整份文件跳过；
代码示例里的同名标记不生效。

已知简化：不进入引用块内部；列表标记后超过 4 个空格时，仍把空白算作标记前缀；
未闭合的围栏延伸到所属列表项结束或文件末尾。
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

# 结构扫描给每一行的结论。
KIND_VERBATIM = "verbatim"  # 原样输出，并终止当前拼接
KIND_BLANK = "blank"
KIND_TEXT = "text"  # 可参与拼接的正文行
KIND_LIST_ITEM = "list-item"  # 列表项首行

# 围栏：开栏允许写在列表标记之后；闭栏行只能有围栏字符和行尾空白。
FENCE_OPEN_RE = re.compile(
    r"^(\s*)((?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})(.*)$"
)
FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})[ \t]*$")

# HTML 块的起始条件与对应收尾串，按 CommonMark 的判断顺序使用。
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

# 中文、日文、韩文、全角字符等宽字符区段。
WIDE_RE = re.compile(
    "["
    "ᄀ-ᅟ"  # 韩文字母
    "⺀-〾"  # CJK 部首与标点
    "ぁ-㏿"  # 假名、注音、兼容字符
    "㐀-䶿"  # CJK 扩展 A
    "一-鿿"  # CJK 基本区
    "ꀀ-꓏"  # 彝文
    "가-힣"  # 韩文音节
    "豈-﫿"  # CJK 兼容表意文字
    "︰-﹏"  # CJK 兼容形式
    "＀-￦"  # 全角形式
    "]"
)
# 全角标点，两侧不再补空格。
WIDE_PUNCT_RE = re.compile(
    "["
    "‘’“”"  # 弯引号
    "—…"  # 破折号、省略号
    "、。"  # 顿号、句号
    "《-】"  # 书名号、方头括号、直角引号
    "〔-〟"  # 六角括号等
    "！（），：；？～｟｠"
    "]"
)
# 拼接时需要跳过的行内标记，取到真正的可见字符再判断留白。
EMPHASIS_CHARS = set("*_~`")
HEAD_SKIP_CHARS = EMPHASIS_CHARS | {"["}
# 行尾的链接目标不参与留白判断，取链接文字的最后一个字符。
LINK_TAIL_RE = re.compile(r"\]\((?:[^()\s]*)(?:\s+\"[^\"]*\")?\)$")
ASCII_TRAILING_NO_SPACE = set("([{<-/@#$&+=\\\"'")
ASCII_LEADING_NO_SPACE = set(",.;:!?)]}>%\"'")


@dataclass(frozen=True)
class Join:
    """一处段落硬换行，用于命令行输出。"""

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
    """判断两行拼接处是否需要一个空格。"""
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
    """按列宽计算前缀长度，用于判断续行缩进。"""
    return sum(CODE_INDENT if char == "\t" else 1 for char in text)


def is_structural(line: str) -> bool:
    """标题、表格、分隔线、引用块等结构行不参与拼接。

    以 ``<`` 开头但不构成 HTML 块的行（例如自动链接）也按结构行单行保护。
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
    """判断一段内容是否开启 HTML 块，返回收尾串或 ``blank``。

    不构成 HTML 块时返回 ``None``，该行仍由 :func:`is_structural` 单行保护。
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
    """反引号围栏的 info string 不能再含反引号，否则那是行内代码。"""
    return not (delimiter[0] == "`" and "`" in info)


def hard_break_suffix(line: str) -> str:
    """返回行尾的显式换行标记：两个以上空白或反斜杠。"""
    match = HARD_BREAK_RE.search(line)
    return match.group(0) if match else ""


def interrupts_paragraph(line: str) -> bool:
    """能中断段落的行：结构行、围栏开栏和列表项；缩进代码不算。"""
    return bool(
        is_structural(line) or FENCE_OPEN_RE.match(line) or LIST_ITEM_RE.match(line)
    )


def pop_containers(containers: list[int], indent: int) -> None:
    """缩进变浅时退出对应的列表容器。"""
    while containers and indent < containers[-1]:
        containers.pop()


@dataclass(frozen=True)
class ScanLine:
    """结构扫描对单行的结论。"""

    line: int
    raw: str
    kind: str
    indent: int
    prefix: str = ""
    content: str = ""
    hard_break: str = ""


@dataclass(frozen=True)
class Scan:
    """整份文件的结构扫描结果。"""

    lines: tuple[ScanLine, ...]
    ignore_file: bool


@dataclass
class _Fence:
    """打开中的围栏代码块。"""

    char: str
    length: int
    indent: int
    floor: int


@dataclass
class _HtmlBlock:
    """打开中的 HTML 块。"""

    end: str
    floor: int


def scan_structure(text: str) -> Scan:
    """逐行判断哪些行原样保留，哪些行可以参与拼接。"""
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
                # 缩进退回容器之外，未闭合的围栏随列表项结束；该行重新判断。
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
            # 段落的懒续行不退出列表容器，其余情况按缩进出栈。
            pop_containers(containers, indent)
        floor = containers[-1] if containers else 0

        if code_floor is not None:
            if indent >= code_floor:
                result.append(verbatim())
                index += 1
                continue
            code_floor = None

        # 缩进代码块的判断必须早于列表项，否则 `    - first` 会被当成列表。
        if not paragraph_open and indent >= floor + CODE_INDENT:
            code_floor = floor + CODE_INDENT
            result.append(verbatim())
            index += 1
            continue

        opener = FENCE_OPEN_RE.match(raw)
        if opener and fence_info_is_valid(opener.group(3), opener.group(4)):
            marker = opener.group(2)
            if marker:
                # 开栏写在列表标记之后，整行原样保留，代码归属列表正文列。
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
            # 只有代码块与 HTML 块之外、独立成行的标记才让整份文件跳过。
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
                # HTML 块写在列表标记之后，同样按整块保留。
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
    """判断文件里是否存在生效的整文件跳过标记。"""
    return scan_structure(text).ignore_file


class _Block:
    """正在拼接的段落或列表项。"""

    def __init__(self, prefix: str, text: str, start_line: int) -> None:
        self.prefix = prefix
        self.text = text
        self.start_line = start_line
        # 正文起始列，续行缩进和缩进代码块的判断都以它为基准。
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
    """按扫描结论拼接段落，只处理正文行，不再判断结构。"""
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
                # 比正文缩进深太多，保守起见原样保留。
                flush()
                output.append(item.raw)
                continue
            block.append(item.line, item.content)
        else:
            block = _Block(item.prefix, item.content, item.line)

        if item.hard_break and block is not None:
            # 行尾两个空格或反斜杠是显式换行，保留原样并结束当前拼接。
            suffix = item.hard_break
            if block.text.endswith(suffix):
                block.text = block.text[: -len(suffix)]
            close(block, suffix)
            block = None

    flush()
    return output, joins


def unwrap_text(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """返回展开后的文本，以及每处硬换行的起始行号、合并行数和预览。"""
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
    """读取文件并展开硬换行；跳过标记由 :func:`scan_structure` 统一判断。"""
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
