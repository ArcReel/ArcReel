"""Safe parsing and validation for Agent Profile Markdown frontmatter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class FrontmatterError(ValueError):
    """The Markdown frontmatter is missing, malformed, or has invalid metadata."""


@dataclass(frozen=True)
class ProfileMetadata:
    name: str
    description: str
    user_invocable: bool = True


def parse_profile_metadata(path: Path) -> ProfileMetadata:
    """Parse validated YAML metadata from a Skill or Subagent Markdown file."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise FrontmatterError("frontmatter is not valid UTF-8") from exc

    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise FrontmatterError("frontmatter must start with a YAML delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    source = "\n".join(lines[1:end])
    try:
        loaded: Any = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise FrontmatterError("frontmatter must be an object")

    name = loaded.get("name")
    description = loaded.get("description")
    user_invocable = loaded.get("user-invocable", True)
    if not isinstance(name, str) or not name.strip():
        raise FrontmatterError("name must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise FrontmatterError("description must be a non-empty string")
    if not isinstance(user_invocable, bool):
        raise FrontmatterError("user-invocable must be a boolean")
    return ProfileMetadata(
        name=name.strip(),
        description=description.strip(),
        user_invocable=user_invocable,
    )
