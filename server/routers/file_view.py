"""File views: lazily list project directories with file roles, and preview/download individual files with authentication, including text and JSON.

Separate from anonymous media endpoints (``files.public_router``): their allowlist is unchanged; these routes are protected.
Infrastructure files, symlinks and invalid paths return the same 404 shape as missing files. List remote-content versions
even after cache cleanup, retrieving them from object storage before preview, download or deletion (:mod:`lib.project.file_view`)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.file_view import iter_file_chunks, list_file_view, local_file, move_file_to_trash, open_project_file
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.routers._file_responses import guess_media_type

router = APIRouter(prefix="/projects/{project_name}/file-view")

#: Return text files as UTF-8 for previews; infer other types by extension.
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".json", ".py", ".yaml", ".yml", ".srt", ".log", ".csv"})


def _project_dir(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.get("")
async def list_file_view_directory(project_name: str, path: str = Query("")):
    """List direct children of ``path``, defaulting to the project root."""

    project_dir = await asyncio.to_thread(_project_dir, project_name)
    try:
        entries = await list_file_view(project_dir, path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise NotFoundError("file_not_found", path=path) from exc
    return {"path": path, "entries": [entry.to_dict() for entry in entries]}


@router.delete("")
async def delete_file_view_entry(project_name: str, path: str = Query(...)):
    """Move an unreferenced file to the project trash; the server rejects all other file roles."""

    project_dir = await asyncio.to_thread(_project_dir, project_name)

    def _sync() -> dict[str, object]:
        try:
            with project_change_source("webui"):
                record = move_file_to_trash(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return {"record": record.to_dict()}

    async with local_file(project_dir, path, deleting=True):
        return await run_sync_transaction(_sync)


@router.get("/content")
async def read_file_view_content(project_name: str, path: str = Query(...), download: bool = Query(False)):
    """Preview or download one file."""

    name = Path(path).name
    project_dir = await asyncio.to_thread(_project_dir, project_name)

    def _sync() -> tuple[BinaryIO, int, str]:
        try:
            handle, info = open_project_file(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return handle, info.st_size, content_media_type(name)

    # Cleanup cannot invalidate an open handle, so usage registration only needs to cover opening.
    async with local_file(project_dir, path):
        handle, size, media_type = await asyncio.to_thread(_sync)
    return stream_file(handle, size, name, media_type, download=download)


def content_media_type(name: str) -> str:
    """Return text files as UTF-8 for previews; infer other types by extension (the first call reads the system MIME table, so use a worker thread)."""
    return "text/plain; charset=utf-8" if Path(name).suffix.lower() in _TEXT_SUFFIXES else guess_media_type(name)


def stream_file(handle: BinaryIO, size: int, name: str, media_type: str, *, download: bool) -> StreamingResponse:
    """Stream an already opened file; shared with project-trash downloads."""
    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Length": str(size),
        "Content-Disposition": f"{disposition}; filename*=utf-8''{quote(name)}",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        # Projects may contain Agent-authored HTML / SVG; prevent scripts from running as this site when opened directly.
        "Content-Security-Policy": "sandbox",
    }
    return StreamingResponse(iter_file_chunks(handle), media_type=media_type, headers=headers)
