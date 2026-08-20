#!/usr/bin/env python3
"""Static integrity checks for the materialized Agent Runtime Profile.

校验范围限于能对照代码真相源的结构：frontmatter 与变体身份、按创作类型物化后的
Markdown 指针可达性、`mcp__arcreel__*` 工具名是否已注册、eval id 是否唯一。
档案散文本身不做措辞校验——越界行为（如直改正式 step1）由 ``AgentAccessPolicy``
在工具边界上拒绝，不靠对散文做黑名单。
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


def lint_profile(profile_dir: Path, *, registered_tools: set[str] | None = None) -> list[str]:
    """Return deterministic profile lint errors; an empty list means success."""
    errors: list[str] = []
    if not profile_dir.is_dir():
        return [f"profile directory does not exist: {profile_dir}"]
    _validate_metadata(profile_dir, errors)
    tool_ids = set(ARCREEL_MCP_TOOL_IDS) if registered_tools is None else registered_tools
    for mode in sorted(VALID_CONTENT_MODES):
        _validate_projection(profile_dir, mode, tool_ids, errors)
    _validate_evals(profile_dir, errors)
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, default=Path("agent_runtime_profile"))
    args = parser.parse_args()
    errors = lint_profile(args.profile_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Agent Runtime Profile lint passed: {args.profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
