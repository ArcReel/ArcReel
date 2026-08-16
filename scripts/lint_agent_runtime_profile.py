#!/usr/bin/env python3
"""Static integrity checks for the materialized Agent Runtime Profile.

``--target-profile`` 额外启用目标档案专属的废弃用法检查。其中禁用字符串
(`_TARGET_DEPRECATED_STRINGS`) 的判定边界如下：

- 判定粒度是**子句**——按换行、中英文逗号/分号/句号/问号/叹号与括号切分；只看命中
  字符串所在的那个子句，不看整段上下文。
- 子句含废弃语境标记（否定词、「残留」「旧项目」「废弃」「legacy」等）时视为反向说明，
  即告诫读者不要再用旧格式，不判违规。该判断优先于路由标记。
- 否则要求子句含路由/指令标记（读取/使用/运行/参数/路径、read/use/run、命令行形态等）
  才判违规；两者都没有的纯提及不判违规。

因此「旧稿 X 不算有效输入」不报，而「读取 X 作为输入」仍报。
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
from collections import defaultdict
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
_CLAUSE_SPLIT_RE = re.compile(r"[\n，,。；;！!？?（）()]|——")
_DEPRECATION_CONTEXT_RE = re.compile(
    r"不算|不再|不要|不需|不得|不能|不应|不作为|不视为|不当|不是|无需|禁止|勿|别用"
    r"|残留|遗留|废弃|已移除|已删除|旧项目|旧稿|旧格式|历史"
    r"|deprecated|legacy|obsolete|removed|no longer|instead of",
    re.IGNORECASE,
)
_ROUTING_MARKER_RE = re.compile(
    r"读取|读|写入|写|使用|用|运行|执行|调用|传入|传|加上|附加|指定|填|生成|保存|输出|输入|参数|选项|路径"
    r"|\bread\b|\bwrite\b|\buse\b|\bruns?\b|\bpass\b|\bexec\b|\bpython\b|\.py\b|\$\s|^\s*[>`]",
    re.IGNORECASE,
)
_DIRECT_STEP1_EDIT_RE = re.compile(
    r"(?:Edit|Write).{0,100}(?:step1_normalized_script|narration.{0,30}step1|drama.{0,30}step1)",
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


def _routes_to_deprecated_string(text: str, needle: str) -> bool:
    """判断文本是否真的把 ``needle`` 当作可用路由/指令，而非反向说明它已废弃。"""
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if needle not in clause:
            continue
        if _DEPRECATION_CONTEXT_RE.search(clause):
            continue
        if _ROUTING_MARKER_RE.search(clause):
            return True
    return False


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
        if _DIRECT_STEP1_EDIT_RE.search(text):
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
