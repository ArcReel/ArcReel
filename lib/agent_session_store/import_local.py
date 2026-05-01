"""Startup hook: import local SDK jsonl transcripts into DbSessionStore.

Uses only SDK public APIs (list_sessions / import_session_to_store /
project_key_for_directory) so docker / CLAUDE_CONFIG_DIR / git-worktree path
resolution is delegated to the SDK and stays correct as SDK evolves.
"""

from __future__ import annotations

import asyncio
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

    Idempotent via the marker file ``data_dir / MARKER_FILENAME`` (fast path)
    plus per-project ``store.list_sessions`` membership checks (fallback when
    the marker is absent).

    Single-process safe; for multi-worker uvicorn an outer config-table lock
    must wrap this call.
    """
    marker = data_dir / MARKER_FILENAME
    if marker.exists():
        logger.info("transcript migration: marker present, skipping")
        return {"imported": 0, "skipped": 0, "failed": 0, "skipped_via_marker": True}

    imported = skipped = failed = 0

    if projects_root.exists():
        for project_cwd in sorted(projects_root.iterdir()):
            # Skip dotfiles and underscore-prefixed dirs (e.g. _global_assets)
            # to match ProjectManager.list_projects semantics.
            if not project_cwd.is_dir() or project_cwd.name.startswith((".", "_")):
                continue
            try:
                # list_sessions stat-walks SDK transcript dirs synchronously;
                # offload so the lifespan doesn't block the event loop.
                sessions = await asyncio.to_thread(list_sessions, directory=str(project_cwd))
            except Exception:
                logger.exception("list_sessions failed for %s", project_cwd)
                continue

            project_key = project_key_for_directory(str(project_cwd))
            try:
                already_imported = {row["session_id"] for row in await store.list_sessions(project_key)}
            except Exception:
                logger.exception("store.list_sessions failed for project_key=%s", project_key)
                already_imported = set()

            for info in sessions:
                if info.session_id in already_imported:
                    skipped += 1
                    continue
                try:
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

    marker.write_text(
        json.dumps({"imported": imported, "skipped": skipped, "failed": failed}),
        encoding="utf-8",
    )

    return {"imported": imported, "skipped": skipped, "failed": failed}
