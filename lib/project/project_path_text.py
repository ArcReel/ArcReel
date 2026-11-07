"""Rewrite absolute project-directory paths in messages to project-relative paths.

External-host messages must not expose server layout. Relative paths locate files for embedded Agents,
Web clients, and remote Agents alike. Replace only project-directory prefixes, preserving all other text."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePath


def project_path_spellings(project_dir: PurePath) -> tuple[str, ...]:
    """Absolute project-directory spellings possible in messages, longest first.

    Include raw paths, resolved symlinks (macOS /tmp → /private/tmp), forward slashes (Windows), and
    escaped exception-text forms: ``OSError`` quotes filenames with ``repr``, doubling Windows backslashes."""
    roots = [project_dir, project_dir.resolve()] if isinstance(project_dir, Path) else [project_dir]
    plain = {spelling for root in roots for spelling in (str(root), root.as_posix())}
    escaped = {quoted for spelling in plain for quoted in _repr_spellings(spelling)}
    return tuple(sorted(plain | escaped, key=len, reverse=True))


def _repr_spellings(spelling: str) -> tuple[str, str]:
    """Two ``spelling`` filename-prefix representations inside ``repr``.

    ``repr`` chooses quotes for whole strings: without double quotes, single quotes remain literal; with
    both quote kinds, it uses single delimiters and escapes single quotes. Append a double quote before
    ``repr`` to obtain the prefix in the latter form."""
    return repr(spelling)[1:-1], repr(f'{spelling}"')[1:-2]


def relativize_project_paths(text: str, spellings: Iterable[str]) -> str:
    """Rewrite absolute project-directory paths in messages to relative paths; the directory itself becomes ``.``.

    Replace only complete paths starting at the project directory. A preceding ASCII path character
    (e.g. ``/mnt`` before ``/data/projects/demo``) belongs to another path and stays unchanged; directly
    adjacent Chinese prose still permits replacement. Remove one following separator (slash, backslash,
    or escaped doubled backslash), preserving the remaining relative spelling."""
    for root in spellings:
        text = re.sub(
            r"(?<![A-Za-z0-9_.\-/\\])" + re.escape(root) + r"(?:(\\\\|[\\/])|(?![\w.\-]))",
            lambda m: "" if m.group(1) else ".",
            text,
        )
    return text


__all__ = ["project_path_spellings", "relativize_project_paths"]
