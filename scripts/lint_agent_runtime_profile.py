#!/usr/bin/env python3
"""Static integrity checks for the materialized Agent Runtime Profile.

``--target-profile`` 额外启用目标档案专属的废弃用法检查。其中禁用字符串
(`_TARGET_DEPRECATED_STRINGS`) 的判定边界如下：

- 判定粒度是**子句**——散文按句读切分（见 ``_CLAUSE_SPLIT_RE``），围栏代码块内以整行
  为一个子句；只看命中字符串所在的那个子句，不看整段上下文。
- 子句命中废弃语境标记（``_DEPRECATION_CONTEXT_RE``）时视为反向说明，即告诫读者不要
  再用旧格式，不判违规。该判断优先于下面的路由判定。
- 否则，子句满足下列任一条即判违规：位于围栏代码块内（代码块是可执行指令而非散文告诫）、
  禁用串本身是命令行 flag（``--`` 开头，其出现即指令形态）、或子句含路由/指令标记
  （``_ROUTING_MARKER_RE``）。三者皆无的纯提及不判违规。

因此「旧稿 X 不算有效输入」不报，而「读取 X 作为输入」与代码块里的 ``cmd --flag`` 仍报。

「Edit/Write 直改正式 step1」（``_DIRECT_STEP1_EDIT_RE``）同样按子句判定并让废弃语境标记豁免，
否则写禁本身的告诫语句会把自己判成违规。
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote

from lib.profile_frontmatter import FrontmatterError, ProfileMetadata, parse_profile_metadata
from lib.profile_manifest import VALID_CONTENT_MODES, ProfileMisconfiguredError, resolve_profile_files_for_mode
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

_MCP_RE = re.compile(r"mcp__arcreel__([a-zA-Z0-9_*.-]+)")
_MCP_SENTENCE_PUNCTUATION = ".,;:!?"
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ROOT_POINTER_RE = re.compile(r"(?<![\w/])(\.claude/[A-Za-z0-9_./-]+\.md)")
_MARKDOWN_INLINE_LINK_RE = re.compile(
    r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)\n]+))"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
)
_MARKDOWN_REFERENCE_LINK_RE = re.compile(
    r"^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|([^\s\n]+))"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*$",
    re.MULTILINE,
)
_TARGET_DEPRECATED_STRINGS = (
    "--scene-ids",
    "--music-volume",
    "step1_normalized_script.md",
)
_CODE_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_BLOCK_START_RE = re.compile(r"^\s{0,3}([-*+]\s|\d+[.)]\s|#{1,6}\s|>)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_CLAUSE_SPLIT_RE = re.compile(r"[，,。；;！!？?]|——")
_DEPRECATION_CONTEXT_RE = re.compile(
    r"不算|不再|不要|不需|不得|不能|不应|不作为|不视为|无需|禁止|勿|别用"
    r"|残留|遗留|废弃|已移除|已删除|旧项目|旧稿|旧格式"
    r"|deprecated|legacy|obsolete|removed|no longer|instead of",
    re.IGNORECASE,
)
_ROUTING_MARKER_RE = re.compile(
    r"读取|写入|使用|运行|执行|调用|传入|加上|附加|指定|生成|保存|输出|输入|参数|选项|路径"
    r"|\bread\b|\bwrite\b|\buse\b|\bruns?\b|\bpass\b|\bexec\b|\bpython\b|\.py\b|\$\s",
    re.IGNORECASE,
)
_DIRECT_STEP1_EDIT_RE = re.compile(
    r"(?:Edit|Write).{0,100}(?:step1_normalized_script|step1_reference_units"
    r"|narration.{0,30}step1|drama.{0,30}step1|reference.video.{0,30}step1)",
    re.IGNORECASE | re.DOTALL,
)
_PYTHON_RESUME_RE = re.compile(r"python[^\n`]*\s--resume(?:\s|$)")


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _metadata_files(profile_dir: Path) -> list[Path]:
    skills_root = profile_dir / ".claude" / "skills"
    skill_names = ("SKILL.md", *(f"SKILL.{mode}.md" for mode in sorted(VALID_CONTENT_MODES)))
    skills = (path for name in skill_names for path in skills_root.glob(f"*/{name}"))
    agents_root = profile_dir / ".claude" / "agents"
    agents = agents_root.glob("*.md") if agents_root.is_dir() else ()
    return sorted((*skills, *agents))


def _validate_metadata(profile_dir: Path, errors: list[str]) -> None:
    variants: dict[str, list[tuple[Path, ProfileMetadata]]] = defaultdict(list)
    for path in _metadata_files(profile_dir):
        try:
            metadata = parse_profile_metadata(path)
        except (OSError, FrontmatterError) as exc:
            errors.append(f"{path.relative_to(profile_dir)}: invalid frontmatter: {exc}")
            continue
        logical = re.sub(r"\.(?:narration|drama|ad)(?=\.md$)", "", path.relative_to(profile_dir).as_posix())
        variants[logical].append((path, metadata))

    for logical, items in variants.items():
        identities = {(metadata.name, metadata.user_invocable) for _, metadata in items}
        if len(identities) > 1:
            errors.append(f"{logical}: variant metadata name/user-invocable drift")


def _projected_pointer(source_logical: str, pointer: str) -> str | None:
    if pointer.startswith(".claude/"):
        return posixpath.normpath(pointer)
    if pointer.startswith(("/", "#")) or _URI_SCHEME_RE.match(pointer):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_logical), pointer))


def _markdown_link_pointers(text: str) -> set[str]:
    pointers: set[str] = set()
    for pattern in (_MARKDOWN_INLINE_LINK_RE, _MARKDOWN_REFERENCE_LINK_RE):
        for match in pattern.finditer(text):
            destination = match.group(1) or match.group(2)
            path = unquote(destination.split("#", 1)[0])
            if path.lower().endswith(".md"):
                pointers.add(path)
    return pointers


def _validate_projection(
    profile_dir: Path,
    mode: str,
    registered_tools: set[str],
    errors: list[str],
) -> None:
    try:
        mapping = resolve_profile_files_for_mode(profile_dir, mode)
    except (ValueError, ProfileMisconfiguredError) as exc:
        errors.append(f"{mode}: invalid profile projection: {exc}")
        return
    projected = set(mapping)
    if not projected:
        errors.append(f"{mode}: profile projection is empty")
        return

    for logical, source_rel in sorted(mapping.items()):
        source = profile_dir / source_rel
        if source.suffix.lower() != ".md":
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{mode}:{source_rel}: cannot read projected file: {exc}")
            continue
        pointers = set(_ROOT_POINTER_RE.findall(text)) | _markdown_link_pointers(text)
        for pointer in sorted(pointers):
            target = _projected_pointer(logical, pointer)
            if target is not None and target not in projected:
                errors.append(f"{mode}:{source_rel}: missing Markdown pointer {pointer!r}")
        tool_names = {match.rstrip(_MCP_SENTENCE_PUNCTUATION) for match in _MCP_RE.findall(text)}
        for tool_name in sorted(tool_names):
            if tool_name != "*" and tool_name not in registered_tools:
                errors.append(f"{mode}:{source_rel}: unregistered MCP tool mcp__arcreel__{tool_name}")


def _validate_evals(profile_dir: Path, errors: list[str]) -> None:
    seen: dict[object, Path] = {}
    for path in sorted(profile_dir.rglob("*.json")):
        if "eval" not in path.as_posix().lower():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{path.relative_to(profile_dir)}: invalid eval JSON: {exc}")
            continue
        records: list[object]
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict) and isinstance(payload.get("evals"), list):
            records = payload["evals"]
        else:
            records = [payload]
        for record in records:
            if not isinstance(record, dict) or "id" not in record:
                continue
            eval_id = record["id"]
            try:
                duplicate = eval_id in seen
            except TypeError:
                errors.append(f"{path.relative_to(profile_dir)}: eval id must be a scalar")
                continue
            if duplicate:
                errors.append(
                    f"{path.relative_to(profile_dir)}: duplicate eval id {eval_id!r} "
                    f"(first in {seen[eval_id].relative_to(profile_dir)})"
                )
            else:
                seen[eval_id] = path


def _iter_clauses(text: str) -> Iterator[tuple[str, bool]]:
    """按行遍历 Markdown，产出 ``(子句, 是否位于围栏代码块内)``。

    三条判定形状：围栏内以整行为一个子句（代码里的逗号分号是语法而非句读，不能当切分点）；
    开合围栏须同字符、闭合长度不短于开启长度、且闭合行不带信息字符串才配对，避免更短的嵌套
    反引号序列被误判为收围栏；围栏外先把连续的非空行合并为一个逻辑段落再切分子句——Markdown
    软换行只是排版折行，不是句读边界，逐物理行切分会把同一子句拆散到两行而漏判。合并在遇到
    新的列表项/标题/引用起始行（``_BLOCK_START_RE``）时截断，紧邻无空行分隔的两个列表项各
    自独立成句——否则前一项若命中废弃语境，会连带吞掉后一项里本应报告的真实路由指令；标题
    行（``_HEADING_RE``）额外在自身之后立即截断——ATX 标题恒为单行块，不与下方段落同句。
    """
    fence_char = ""
    fence_len = 0
    prose_lines: list[str] = []

    def _flush_prose() -> list[str]:
        nonlocal prose_lines
        if not prose_lines:
            return []
        paragraph = " ".join(prose_lines)
        prose_lines = []
        return _CLAUSE_SPLIT_RE.split(paragraph)

    for line in text.splitlines():
        if fence_char:
            match = _CODE_FENCE_RE.match(line)
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_len
                and not match.group(2).strip()
            ):
                fence_char = ""
                fence_len = 0
            else:
                yield line, True
            continue
        match = _CODE_FENCE_RE.match(line)
        if match:
            for clause in _flush_prose():
                yield clause, False
            fence_char = match.group(1)[0]
            fence_len = len(match.group(1))
            continue
        if line.strip():
            if _BLOCK_START_RE.match(line):
                for clause in _flush_prose():
                    yield clause, False
            prose_lines.append(line)
            if _HEADING_RE.match(line):
                for clause in _flush_prose():
                    yield clause, False
        else:
            for clause in _flush_prose():
                yield clause, False
    for clause in _flush_prose():
        yield clause, False


def _routes_to_deprecated_string(text: str, needle: str) -> bool:
    """判断文本是否真的把 ``needle`` 当作可用路由/指令，而非反向说明它已废弃。"""
    needle_is_cli_flag = needle.startswith("--")
    for clause, in_code_fence in _iter_clauses(text):
        if needle not in clause:
            continue
        if _DEPRECATION_CONTEXT_RE.search(clause):
            continue
        if in_code_fence or needle_is_cli_flag or _ROUTING_MARKER_RE.search(clause):
            return True
    return False


def _edits_formal_step1_directly(text: str) -> bool:
    """判断文本是否指示用 Edit/Write 直改正式 step1，而非反向告诫不要这么做。"""
    return any(
        _DIRECT_STEP1_EDIT_RE.search(clause) and not _DEPRECATION_CONTEXT_RE.search(clause)
        for clause, _ in _iter_clauses(text)
    )


def _validate_target_deprecations(profile_dir: Path, errors: list[str]) -> None:
    for path in sorted(profile_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(profile_dir)}: cannot read: {exc}")
            continue
        for needle in _TARGET_DEPRECATED_STRINGS:
            if _routes_to_deprecated_string(text, needle):
                errors.append(f"{path.relative_to(profile_dir)}: deprecated profile string {needle!r}")
        if _PYTHON_RESUME_RE.search(text):
            errors.append(f"{path.relative_to(profile_dir)}: deprecated Python --resume invocation")
        if _edits_formal_step1_directly(text):
            errors.append(f"{path.relative_to(profile_dir)}: deprecated direct Edit/Write of formal step1")


def lint_profile(
    profile_dir: Path,
    *,
    registered_tools: set[str] | None = None,
    enforce_target_rules: bool = False,
) -> list[str]:
    """Return deterministic profile lint errors; an empty list means success."""
    errors: list[str] = []
    if not profile_dir.is_dir():
        return [f"profile directory does not exist: {profile_dir}"]
    _validate_metadata(profile_dir, errors)
    tool_ids = set(ARCREEL_MCP_TOOL_IDS) if registered_tools is None else registered_tools
    for mode in sorted(VALID_CONTENT_MODES):
        _validate_projection(profile_dir, mode, tool_ids, errors)
    _validate_evals(profile_dir, errors)
    if enforce_target_rules:
        _validate_target_deprecations(profile_dir, errors)
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, default=Path("agent_runtime_profile"))
    parser.add_argument(
        "--target-profile",
        action="store_true",
        help="also enforce strings forbidden in the common target profile",
    )
    args = parser.parse_args()
    errors = lint_profile(args.profile_dir, enforce_target_rules=args.target_profile)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Agent Runtime Profile lint passed: {args.profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
