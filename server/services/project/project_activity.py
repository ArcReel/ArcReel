"""Recent project activity, computed on read.

The project lobby uses it to place recently active projects first. Compute the latest modification from the ledger and recognized content, taking the latest of:

1. Business timestamps in the ledger: `project.json` and each episode script’s `metadata.updated_at`. Business writes refresh them;
   project-structure migrations preserve them when rewriting JSON, so do not use these JSON files’ modification times.
2. The project activity ledger (`lib.project.project_activity`): write entry points record business writes to other JSON such as drafts,
   script plans and edit timelines, and deletions such as memory or timeline deletion. If absent, use only the other two sources.
3. Modification times of other content files: source text, generated images/videos, project memory, and so on.

Excluded files do not represent creator activity but are rewritten together at startup or in background flows; including them would make every project appear just updated:

- Files/directories beginning with `.`: locks, artifact manifests, migration reports, Agent configuration copies, etc. Count project memory separately and use category 2 for the activity ledger.
- All `.json` files: use category 1 business times for project/scripts and category 2 for other JSON writes. Their modification times cannot distinguish business writes from migrations, so exclude them.
- Migration backups (names containing `.bak`) and lock files (ending in `.lock`).
- Project-root `CLAUDE.md`: startup configuration synchronization rewrites embedded Agent configuration."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.agent.agent_memory_paths import project_memory_dir
from lib.project.project_activity import recorded_project_activity

logger = logging.getLogger(__name__)

_PROFILE_SYNCED_ROOT_FILES = frozenset({"CLAUDE.md"})


def project_last_activity_at(
    project_dir: Path,
    project: Mapping[str, Any],
    scripts: Iterable[Mapping[str, Any]],
) -> datetime | None:
    """Latest project-content modification time in UTC; None when no usable timestamp exists."""
    candidates = [stamp for stamp in (_metadata_updated_at(doc) for doc in (project, *scripts)) if stamp is not None]
    recorded = recorded_project_activity(project_dir)
    if recorded is not None:
        candidates.append(recorded)
    content_mtime = _latest_content_mtime(project_dir)
    if content_mtime is not None:
        candidates.append(datetime.fromtimestamp(content_mtime, UTC))
    return max(candidates, default=None)


def _metadata_updated_at(doc: Mapping[str, Any]) -> datetime | None:
    metadata = doc.get("metadata")
    raw = metadata.get("updated_at") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # Interpret timezone-naive legacy data in local machine time.
    return parsed.astimezone(UTC)


def _latest_content_mtime(project_dir: Path) -> float | None:
    latest: float | None = None
    pending = [(project_dir, True), (project_memory_dir(project_dir), False)]
    while pending:
        directory, is_root = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            logger.debug("读取项目目录失败 dir=%s err=%s", directory, exc)
            continue
        for entry in entries:
            if not _counts_as_content(entry.name, is_root=is_root):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), False))
                elif entry.is_file(follow_symlinks=False):
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                    latest = mtime if latest is None else max(latest, mtime)
            except OSError:
                continue
    return latest


def _counts_as_content(name: str, *, is_root: bool) -> bool:
    if name.startswith("."):
        return False
    if name.endswith((".json", ".lock")) or ".bak" in name:
        return False
    return not (is_root and name in _PROFILE_SYNCED_ROOT_FILES)
