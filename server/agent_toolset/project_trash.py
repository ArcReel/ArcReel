"""Project-trash declarations: move unreferenced files or historical versions into trash, list deletion records, and restore them.

Emptying and permanent deletion are not declared: both are irreversible and available only to creators in the file view (``docs/adr/0107``)."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.media_tools.project_trash import (
    DeleteProjectFileRequest,
    RestoreTrashRecordRequest,
    delete_project_file,
    delete_project_file_summary,
    list_trash_records,
    restore_trash_record,
    restore_trash_record_summary,
)
from server.tool_runtime import NoArguments

DELETE_PROJECT_FILE = ToolDeclaration(
    name="delete_project_file",
    description=(
        "Move an unreferenced project file or noncurrent historical version (a snapshot under versions/) into project trash as a deletion record, recoverable within 30 days through restore_trash_record; restored historical versions return to version history. This is the Agent’s only way to delete project files: do not directly write or delete media directories or versions/. Rechecks references under the project lock before deletion; returns file_view_delete_not_allowed for files referenced by project data, scripts, artifact manifests, or version history, version_delete_current for the current version, file_view_delete_source_in_episodes for source files not incorporated into whole source, or file_not_found if absent. Delete only files explicitly requested by the creator; only the creator can empty trash or permanently delete in the file view. Success returns record (id, files, deleted_at, size)."
    ),
    request_model=DeleteProjectFileRequest,
    migration=BLOCKED,
    domain_key="delete_project_file",
    handler=delete_project_file,
    summary=delete_project_file_summary,
)

LIST_TRASH_RECORDS = ToolDeclaration(
    name="list_trash_records",
    description=(
        "List project-trash deletion records newest first: id, object (deleted object type and identity), files (pre-deletion project-relative paths), deleted_at, origin (user for creator-initiated, agent for Agent-initiated), and size; also returns total_size and retention_days. Read-only, no side effects."
    ),
    request_model=NoArguments,
    migration=Exempt(
        "Read-only deletion records in the project-private area; no artifact-manifest access or write credentials. Must remain queryable after migration failure."
    ),
    domain_key="trash_records",
    handler=list_trash_records,
)

RESTORE_TRASH_RECORD = ToolDeclaration(
    name="restore_trash_record",
    description=(
        "Restore one deletion record. Restoration only adds, never overwrites: restore to the original path if free; if that resource’s current file occupies the original path, add the deleted file as a historical version without changing the current version; rename other occupied paths as “name (n).ext”. Returns trash_record_not_found if absent (possibly already restored or cleared). Success returns restored, each item with original (pre-deletion path), path (restored path), and as_history."
    ),
    request_model=RestoreTrashRecordRequest,
    migration=BLOCKED,
    domain_key="restore_trash_record",
    handler=restore_trash_record,
    summary=restore_trash_record_summary,
)

PROJECT_TRASH_TOOLS = (DELETE_PROJECT_FILE, LIST_TRASH_RECORDS, RESTORE_TRASH_RECORD)

__all__ = ["DELETE_PROJECT_FILE", "LIST_TRASH_RECORDS", "PROJECT_TRASH_TOOLS", "RESTORE_TRASH_RECORD"]
