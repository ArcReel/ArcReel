"""Shared mapping of whole-source file changes (``SourceFileChangeOutcome`` / ``SourceFileChangeError``) to HTTP responses.

Commands surface through ``episodes_view`` (the episode view’s file bar) and ``files`` (source uploads, filename-based writes
and deletion); identical results produce the same response shape."""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException

from lib.episode.source_file_changes import (
    SourceFileChangeError,
    SourceFileChangeOutcome,
    render_source_file_impact_text,
)
from lib.infra.async_thread import run_sync_transaction
from server.i18n import Translator
from server.routers.episode_management import episode_has_active_tasks

_STATUS: dict[str, int] = {
    "source_file_not_found": 404,
    "source_file_unreadable": 422,
    "source_text_empty": 422,
}


def source_file_change_payload(
    outcome: SourceFileChangeOutcome, project: Mapping[str, Any], _t: Translator
) -> dict[str, Any]:
    """Return ``status=applied`` after execution; when confirmation is needed, return the server-written affected-episode list and its ``revision``.

    ``project`` must be the pre-change project; the confirmation list identifies episodes by their pre-change titles or broadcast positions."""
    impact = outcome.impact.to_dict()
    if outcome.applied:
        return {"status": "applied", "impact": impact}
    text = render_source_file_impact_text(impact, project, _t)
    return {"status": "confirmation_required", "impact": {**impact, "text": text}, "revision": outcome.revision}


def source_file_change_http_error(exc: SourceFileChangeError, _t: Translator) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 409), detail=_t(f"source_file_change_{exc.code}"))


async def ensure_no_displaced_tasks(
    project_name: str,
    revision: str | None,
    preview: Callable[[], SourceFileChangeOutcome],
    _t: Translator,
) -> None:
    """Before executing with ``revision``, recompute affected episodes using ``preview`` (the command’s ``dry_run``).

    If the list matches the confirmed one but episodes being retired or removed still have queued/running tasks, return 409
    without writing. Do not block when ``revision`` is absent or the list changed; let the command return confirmation itself."""
    if revision is None:
        return
    outcome = await run_sync_transaction(preview)
    if outcome.revision != revision:
        return
    for episode in (*outcome.impact.retired, *outcome.impact.removed):
        if await episode_has_active_tasks(project_name, episode):
            raise HTTPException(status_code=409, detail=_t("source_file_change_tasks_active"))


__all__ = ["ensure_no_displaced_tasks", "source_file_change_http_error", "source_file_change_payload"]
