"""Startup hook: import local SDK jsonl transcripts into DbSessionStore.

Uses only SDK public APIs (list_sessions / import_session_to_store /
project_key_for_directory) so docker / CLAUDE_CONFIG_DIR / git-worktree path
resolution is delegated to the SDK and stays correct as SDK evolves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    import_session_to_store,
    list_sessions,
    project_key_for_directory,
)

from lib.agent_session_store.store import DbSessionStore

logger = logging.getLogger("arcreel.session_store.import")

MARKER_FILENAME = ".session_store_migration_done"


async def migrate_local_transcripts_to_store(
    store: DbSessionStore,
    *,
    projects_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Replay all on-disk SDK transcripts into ``store``.

    Idempotent via:
      1. ``data_dir / MARKER_FILENAME`` — fast-path skip on subsequent boots
      2. ``store.load(key)``           — fallback when marker is absent

    Single-process safe; for multi-worker uvicorn an outer config-table lock
    must wrap this call (Task 17, conditional).

    Returns stats dict ``{imported, skipped, failed}`` plus
    ``skipped_via_marker: True`` when the marker fast-path triggered.
    """
    marker = data_dir / MARKER_FILENAME
    if marker.exists():
        logger.info("transcript migration: marker present, skipping")
        return {"imported": 0, "skipped": 0, "failed": 0, "skipped_via_marker": True}

    imported = skipped = failed = 0

    if projects_root.exists():
        for project_cwd in sorted(projects_root.iterdir()):
            if not project_cwd.is_dir() or project_cwd.name.startswith("."):
                continue
            try:
                sessions = list_sessions(directory=str(project_cwd))
            except Exception:
                logger.exception("list_sessions failed for %s", project_cwd)
                continue

            project_key = project_key_for_directory(str(project_cwd))

            for info in sessions:
                key = {"project_key": project_key, "session_id": info.session_id}
                try:
                    if await store.load(key) is not None:
                        skipped += 1
                        continue
                    await import_session_to_store(info.session_id, store, directory=str(project_cwd))
                    imported += 1
                except Exception:
                    logger.exception(
                        "failed to migrate session=%s cwd=%s",
                        info.session_id,
                        project_cwd,
                    )
                    failed += 1

    logger.info(
        "transcript migration: imported=%d skipped=%d failed=%d",
        imported,
        skipped,
        failed,
    )

    # Always write marker — even with zero data — so we don't rescan next boot.
    marker.write_text(
        json.dumps({"imported": imported, "skipped": skipped, "failed": failed}),
        encoding="utf-8",
    )

    return {"imported": imported, "skipped": skipped, "failed": failed}
