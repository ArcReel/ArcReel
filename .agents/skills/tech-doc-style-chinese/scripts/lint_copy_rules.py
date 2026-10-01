#!/usr/bin/env python3
"""轻量中文技术文案检查器。

检查结果分为三类：
- error：高度确定的错误，默认导致非零退出。
- warning：依赖语境的可疑表达，需人工判断。
- style：团队风格或术语偏好，需结合项目规范判断。

脚本忽略 Markdown front matter、代码块、行内代码、URL、链接目标和
常见 API 路径。行尾加入 ``<!-- copy-lint-disable-line -->`` 可以忽略该行。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGETS = ["."]
INLINE_IGNORE_MARKER = "<!-- copy-lint-disable-line -->"
SKIP_DIR_NAMES = {".git", ".venv", "node_modules", "vendor"}

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
QUOTE_PREFIX_RE = re.compile(r"^ {0,3}>[ \t]?")
LIST_ITEM_RE = re.compile(r"^( *)(?:[-+*]|\d{1,9}[.)])([ \t]+)(.*)$")
HTML_TAG_RE = re.compile(
    r'''</?[A-Za-z][A-Za-z0-9:-]*(?=[\s/>])(?:[^<>"']|"[^"]*"|'[^']*')*>'''
)
AUTOLINK_RE = re.compile(r"<https?://[^<>\s]+>")
INLINE_CODE_RE = re.compile(r"(`+)(.*?)\1")
BLOCK_BOUNDARY_RE = re.compile(
    r"(?m)^ *(?:$|#{1,6}(?:\s|$)|`{3,}|~{3,}|(?:[-*_] *){3,}$)"
)


def mask_code_spans(source: str) -> str:
    """Mask exact backtick pairs without losing diagnostic coordinates."""
    chars = list(source)
    boundaries = [match.start() for match in BLOCK_BOUNDARY_RE.finditer(source)]
    boundaries = sorted({0, len(source), *boundaries})
    for start, end in zip(boundaries, boundaries[1:]):
        runs = list(re.finditer(r"`+", source[start:end]))
        index = 0
        while index < len(runs):
            opening = runs[index]
            before = source[start:start + opening.start()]
            if (len(before) - len(before.rstrip("\\"))) % 2:
                index += 1
                continue
            closing_index = next((j for j in range(index + 1, len(runs))
                                  if runs[j].group() == opening.group()), None)
            if closing_index is None:
                index += 1
                continue
            for pos in range(start + opening.start(), start + runs[closing_index].end()):
                if chars[pos] not in "\r\n":
                    chars[pos] = " "
            index = closing_index + 1
    return "".join(chars)


URL_RE = re.compile(r'''https?://[^\s<>"'，。；：！？、（）「」『』【】]+''')
API_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])/[A-Za-z0-9._~%-]+"
    r"(?:/[A-Za-z0-9._~%-]+)*(?:\?[^\s)]*)?(?![A-Za-z0-9_])"
)
INLINE_LINK_RE = re.compile(r"(!?\[[^\]]*]\()([^)]+)(\))")

FORBIDDEN_QUOTES = {
    '"': "ASCII 双引号",
    "“": "中文弯引号",
    "”": "中文弯引号",
}

NON_WORD_CHARS = r"\u4e00-\u9fffA-Za-z0-9_"
PREFIX_CONTEXT_CHARS = "与跟对向给帮替为请让"
SUFFIX_HINTS = "可会要能应需请把将来去做看读写用"

FORBIDDEN_ADDRESS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            rf"(?<![{NON_WORD_CHARS}])你(?=$|[^\u4e00-\u9fff]|[{SUFFIX_HINTS}])"
        ),
        "你",
    ),
    (re.compile(rf"(?<=[{PREFIX_CONTEXT_CHARS}])你"), "你"),
    (re.compile(r"您"), "您"),
    (re.compile(r"同学(?:们|們)?"), "同学"),
]

CASE_RULES = [
    (re.compile(r"(?<![A-Za-z0-9_])(?:id|Id)(?![A-Za-z0-9_])"), "ID"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:http|Http)(?![A-Za-z0-9_])"), "HTTP"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:url|Url)(?![A-Za-z0-9_])"), "URL"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:json|Json)(?![A-Za-z0-9_])"), "JSON"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:api|Api)(?![A-Za-z0-9_])"), "API"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:ai|Ai)(?![A-Za-z0-9_])"), "AI"),
]

ABBREVIATION_RULES = [
    (re.compile(r"(?<![A-Za-z0-9_])(?:JS|Js)(?![A-Za-z0-9_])"), "JavaScript"),
    (re.compile(r"(?<![A-Za-z0-9_])H5(?![A-Za-z0-9_])"), "移动 Web 页面或 HTML5"),
]

AI_TERM_RULES = [
    (re.compile(r"(?<![A-Za-z0-9_])(?:llm|Llm)(?![A-Za-z0-9_])"), "LLM"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:aigc|Aigc)(?![A-Za-z0-9_])"), "AIGC"),
    (re.compile(r"(?<![A-Za-z0-9_])(?:rag|Rag)(?![A-Za-z0-9_])"), "RAG"),
    (
        re.compile(r"(?<![A-Za-z0-9_])(?:chatgpt|Chatgpt)(?![A-Za-z0-9_])"),
        "ChatGPT",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:openai|OpenAI)\s+(?:api|Api)(?![A-Za-z0-9_])"
        ),
        "OpenAI API",
    ),
    (re.compile(r"(?<![A-Za-z0-9_])embeding(?![A-Za-z0-9_])"), "embedding"),
    (re.compile(r"(?<![A-Za-z0-9_])finetune(?![A-Za-z0-9_])"), "fine-tuning"),
    (
        re.compile(r"(?<![A-Za-z0-9_])fine\s+tune(?![A-Za-z0-9_])"),
        "fine-tuning",
    ),
    (re.compile(r"提示工程学"), "提示工程"),
    (re.compile(r"模型出现幻听"), "模型出现幻觉"),
]

ERROR_TYPO_RULES = [
    (re.compile(r"阀值"), "阈值"),
    (re.compile(r"布署"), "部署"),
    (re.compile(r"反回"), "返回"),
    (re.compile(r"回朔"), "回溯"),
    (re.compile(r"做为"), "作为"),
]

CONTEXT_WARNING_RULES = [
    (re.compile(r"登陆"), "确认语义：登录系统；登陆陆地或天体"),
    (re.compile(r"配制"), "确认语义：配置参数；配制溶液"),
    (re.compile(r"起用"), "确认语义：启用功能；起用人员"),
    (re.compile(r"标示"), "确认语义：标识字段；标示位置"),
    (re.compile(r"帐户"), "项目可统一为账户"),
    (re.compile(r"帐号"), "项目可统一为账号"),
    (re.compile(r"截止"), "确认语义：截至某时；截止日期"),
    (re.compile(r"搜寻"), "技术文档通常使用搜索"),
    (
        re.compile(
            r"即时(?=(通信|消息|处理|监控|更新|同步|计算|响应|数据|日志|推送|渲染|指标|告警|检索|查询|任务|分析|流式|调用|服务|接口|系统))"
        ),
        "技术能力语境通常使用实时",
    ),
]


@dataclass(frozen=True)
class Violation:
    file: Path
    line: int
    col: int
    severity: str
    kind: str
    message: str
    snippet: str


def mask_match(text: str, regex: re.Pattern[str]) -> str:
    return regex.sub(lambda match: " " * (match.end() - match.start()), text)


def prepare_visible_line(line: str) -> str:
    visible = mask_match(line, INLINE_CODE_RE)
    visible = mask_match(visible, AUTOLINK_RE)
    visible = INLINE_LINK_RE.sub(
        lambda match: (
            f"{match.group(1)}{' ' * len(match.group(2))}{match.group(3)}"
        ),
        visible,
    )
    visible = mask_match(visible, URL_RE)
    visible = mask_match(visible, API_PATH_RE)
    return visible


def iter_forbidden_address_matches(line: str):
    seen: set[tuple[int, int, str]] = set()
    for pattern, label in FORBIDDEN_ADDRESS_PATTERNS:
        for match in pattern.finditer(line):
            key = (match.start(), match.end(), label)
            if key not in seen:
                seen.add(key)
                yield match, label


def add_rule_matches(
    violations: list[Violation],
    *,
    path: Path,
    line_no: int,
    raw: str,
    visible: str,
    rules: list[tuple[re.Pattern[str], str]],
    severity: str,
    kind: str,
    label: str,
) -> None:
    for pattern, suggested in rules:
        for match in pattern.finditer(visible):
            wrong = match.group(0)
            violations.append(
                Violation(
                    file=path,
                    line=line_no,
                    col=match.start() + 1,
                    severity=severity,
                    kind=kind,
                    message=f"{label}「{wrong}」：{suggested}",
                    snippet=raw.strip(),
                )
            )


def scan_markdown(path: Path) -> list[Violation]:
    violations: list[Violation] = []
    fence_delimiter: str | None = None
    source = path.read_text(encoding="utf-8")
    raw_lines = source.splitlines()
    # Preserve line/column positions, including multiline HTML attributes.
    source = HTML_TAG_RE.sub(
        lambda match: re.sub(r"[^\r\n]", " ", match.group()), source
    )
    lines = mask_code_spans(source).splitlines()
    in_front_matter = bool(lines and lines[0].strip() == "---")
    previous_depth = 0
    paragraph_open = False
    indented_code = False
    list_indents: list[int] = []
    fence_depth = 0
    fence_indent = 0

    for line_no, content in enumerate(lines, start=1):
        raw = raw_lines[line_no - 1]
        # HTML masking must not turn a tag into apparent code indentation.
        content = raw
        if in_front_matter:
            if line_no != 1 and raw.strip() in {"---", "..."}:
                in_front_matter = False
            continue

        # Only consume the containers recorded at the opening fence. Symbols
        # inside code (including > and list markers) are literal content.
        if fence_delimiter is not None:
            candidate = raw.expandtabs(4)
            container_present = True
            for _ in range(fence_depth):
                prefix = QUOTE_PREFIX_RE.match(candidate)
                if prefix is None:
                    container_present = False
                    break
                candidate = candidate[prefix.end():]
            if container_present and fence_indent:
                if candidate.strip() and not candidate.startswith(" " * fence_indent):
                    container_present = False
                else:
                    candidate = candidate[fence_indent:]
            if container_present:
                closing = FENCE_RE.match(candidate)
                if (
                    closing
                    and closing.group(1)[0] == fence_delimiter[0]
                    and len(closing.group(1)) >= len(fence_delimiter)
                    and not closing.group(2).strip()
                ):
                    fence_delimiter = None
                continue
            fence_delimiter = None

        content = raw.expandtabs(4)
        depth = 0
        while prefix := QUOTE_PREFIX_RE.match(content):
            content = content[prefix.end():]
            depth += 1
        if depth != previous_depth:
            paragraph_open = False
            indented_code = False
            list_indents.clear()
        previous_depth = depth

        if not content.strip():
            paragraph_open = False
            continue
        indentation = len(content) - len(content.lstrip(" "))
        while list_indents and indentation < list_indents[-1]:
            list_indents.pop()
            paragraph_open = False
            indented_code = False
        container_indent = list_indents[-1] if list_indents else 0
        content = content[container_indent:]
        item = LIST_ITEM_RE.match(content)
        if item and len(item.group(1)) <= 3:
            # Up to four padding spaces belong to a list marker. Greater
            # padding leaves indentation that can start an indented code block.
            padding = len(item.group(2))
            marker_width = item.start(3) - (padding - 1 if padding > 4 else 0)
            container_indent += marker_width
            list_indents.append(container_indent)
            content = content[marker_width:]
            paragraph_open = False
            indented_code = False

        fence_match = FENCE_RE.match(content)
        if content.startswith(("    ", "\t")) and (
            indented_code or not paragraph_open
        ):
            indented_code = True
            continue
        indented_code = False

        if fence_match:
            delimiter = fence_match.group(1)
            if delimiter[0] != "`" or "`" not in fence_match.group(2):
                fence_delimiter = delimiter
                fence_depth = depth
                fence_indent = container_indent
                paragraph_open = False
                continue

        paragraph_open = not bool(re.match(
            r"^ {0,3}(?:#{1,6}(?:\s|$)|(?:[-*_] *){3,}$|=+\s*$)", content
        ))
        if INLINE_IGNORE_MARKER in raw:
            continue

        visible = prepare_visible_line(lines[line_no - 1])

        for quote, label in FORBIDDEN_QUOTES.items():
            for match in re.finditer(re.escape(quote), visible):
                violations.append(
                    Violation(
                        file=path,
                        line=line_no,
                        col=match.start() + 1,
                        severity="style",
                        kind="quote",
                        message=f"可见正文包含{label}，确认是否应改为直角引号「」",
                        snippet=raw.strip(),
                    )
                )

        for match, term in iter_forbidden_address_matches(visible):
            violations.append(
                Violation(
                    file=path,
                    line=line_no,
                    col=match.start() + 1,
                    severity="style",
                    kind="address",
                    message=f"可见正文包含称呼「{term}」，确认项目是否允许",
                    snippet=raw.strip(),
                )
            )

        add_rule_matches(
            violations,
            path=path,
            line_no=line_no,
            raw=raw,
            visible=visible,
            rules=CASE_RULES,
            severity="style",
            kind="casing",
            label="术语写法",
        )
        add_rule_matches(
            violations,
            path=path,
            line_no=line_no,
            raw=raw,
            visible=visible,
            rules=ABBREVIATION_RULES,
            severity="style",
            kind="abbreviation",
            label="缩写需结合语境确认",
        )
        add_rule_matches(
            violations,
            path=path,
            line_no=line_no,
            raw=raw,
            visible=visible,
            rules=AI_TERM_RULES,
            severity="warning",
            kind="ai-term",
            label="AI 术语",
        )
        add_rule_matches(
            violations,
            path=path,
            line_no=line_no,
            raw=raw,
            visible=visible,
            rules=ERROR_TYPO_RULES,
            severity="error",
            kind="typo",
            label="高置信度错词",
        )
        add_rule_matches(
            violations,
            path=path,
            line_no=line_no,
            raw=raw,
            visible=visible,
            rules=CONTEXT_WARNING_RULES,
            severity="warning",
            kind="context",
            label="语境词",
        )

    return violations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验中文技术文案高频规则")
    parser.add_argument(
        "files",
        nargs="*",
        help="要检查的 Markdown 文件或目录；为空时检查当前目录",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="将 warning 和 style 也作为失败处理",
    )
    return parser.parse_args()


def collect_targets(args: argparse.Namespace) -> list[Path]:
    raw_targets = args.files if args.files else DEFAULT_TARGETS
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
        {path.resolve(): path for path in targets}.values(), key=lambda path: str(path)
    )


def main() -> int:
    args = parse_args()
    targets = collect_targets(args)
    if not targets:
        print("未找到可检查的 Markdown 文件。", file=sys.stderr)
        return 1

    findings = [item for target in targets for item in scan_markdown(target)]
    if not findings:
        print(f"PASS: 共检查 {len(targets)} 个文件，未发现问题。")
        return 0

    counts = {
        severity: sum(item.severity == severity for item in findings)
        for severity in ("error", "warning", "style")
    }
    for item in findings:
        print(
            f"- {item.file}:{item.line}:{item.col} "
            f"[{item.severity}/{item.kind}] {item.message}\n  {item.snippet}"
        )

    should_fail = bool(counts["error"] or (args.strict and findings))
    status = "FAIL" if should_fail else "PASS WITH ADVICE"
    print(
        f"{status}: 共检查 {len(targets)} 个文件；"
        f"error={counts['error']} warning={counts['warning']} style={counts['style']}。"
    )
    return 2 if should_fail else 0


if __name__ == "__main__":
    sys.exit(main())
