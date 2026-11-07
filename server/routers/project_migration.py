"""Web entry point for project-data upgrades: retry upgrades and acknowledge reports.

Retry uses the same service command as Agent ``retry_project_migration``; routes only map results to HTTP. Upgrade failures
block other project writes, and retry clears that blockage, so it does not use ``require_project_migration_ok``. Report
acknowledgment only records that the user has seen it and likewise omits that guard."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from lib.infra.api_errors import NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_change_hints import emit_project_change_hint, project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.project_migration_report import acknowledge_migration_report
from server.auth import CurrentUser
from server.tool_runtime import (
    CallerContext,
    NoArguments,
    ProjectScope,
    Services,
    ToolRequest,
    retry_project_migration,
)

router = APIRouter()


@router.post("/projects/{name}/migration/retry")
async def retry_migration(name: str, user: CurrentUser):
    """Rerun this project’s upgrade chain, including artifact backfill; succeed immediately if already current.

    Continued upgrade failure returns 422 with original failure text in diagnostic ``reason`` and structured episode/file/violation ``details``."""
    pm = get_project_manager()
    if not await asyncio.to_thread(pm.project_exists, name):
        raise NotFoundError("project_not_found", name=name)
    with project_change_source("webui"):
        try:
            outcome = await retry_project_migration(
                ToolRequest(NoArguments()),
                ProjectScope(project_name=name, data_root=pm.data_root),
                CallerContext(user_id=user.id, source="webui"),
                Services.defaults(pm),
            )
        finally:
            # The upgrade chain rewrites project.json/scripts without hints; emit them here and broadcast upgrade changes as webui,
            # rather than waiting for periodic polling to discover and broadcast them as filesystem.
            emit_project_change_hint(name)
    if outcome.problem is not None:
        params = outcome.problem.params or {}
        raise UnprocessableError("project_migration_retry_failed").with_diagnostic(
            {"reason": outcome.problem.detail, "details": params.get("details", [])}
        )
    return {"success": True}


class AcknowledgeReportRequest(BaseModel):
    #: Generation time of the report the user saw. Do not acknowledge a replacement report after another upgrade; keep prompting for it.
    migrated_at: str


@router.post("/projects/{name}/migration-report/acknowledge")
async def acknowledge_report(name: str, body: AcknowledgeReportRequest, _user: CurrentUser):
    """Record that the user has seen the last upgrade report so the project page stops prompting. Also succeed when no report awaits acknowledgment.

    False ``acknowledged`` means this pending report is absent on disk: it was already acknowledged or replaced by another."""
    with project_change_source("webui"):
        acknowledged = await run_sync_transaction(_acknowledge, get_project_manager(), name, body.migrated_at)
    return {"success": True, "acknowledged": acknowledged}


def _acknowledge(pm: ProjectManager, name: str, migrated_at: str) -> bool:
    if not pm.project_exists(name):
        raise NotFoundError("project_not_found", name=name)
    return acknowledge_migration_report(pm.get_project_path(name), migrated_at=migrated_at)
