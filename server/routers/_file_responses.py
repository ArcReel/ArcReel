"""File responses shared by public media endpoints: cache-header policy and conditional requests (304).

Starlette ``FileResponse`` emits ETag / Last-Modified and handles Range but never returns 304 itself. Conditional
requests are evaluated only in ``StaticFiles.file_response``. Reuse ``StaticFiles.is_not_modified`` and
``NotModifiedResponse``; ``FileResponse`` derives ETag from the same ``stat_result``, matching Starlette static-file
revalidation semantics exactly."""

from __future__ import annotations

import mimetypes
import os
from collections.abc import Mapping

from fastapi import Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import NotModifiedResponse

#: URLs with version keys (?v= / ?fp= / versions/ snapshots) are immutable and browser-reusable indefinitely.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
#: Unversioned URLs may be overwritten: allow caching but require revalidation before every use (304 on a match).
REVALIDATE_CACHE_CONTROL = "no-cache"

# Reuse only is_not_modified logic: If-None-Match first, then If-Modified-Since;
# neither mount nor read directories; directory=None construction does not touch the filesystem.
_CONDITIONAL_CHECKER = StaticFiles(directory=None, check_dir=False)


def guess_media_type(path: str | os.PathLike[str]) -> str:
    """Infer response media type by extension, matching ``FileResponse`` defaults.

    The process’s first call loads the system MIME table from disk; callers should use a worker thread."""
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def cached_file_response(
    path: str | os.PathLike[str],
    stat_result: os.stat_result,
    request: Request,
    *,
    immutable: bool,
    headers: Mapping[str, str] | None = None,
    media_type: str | None = None,
) -> Response:
    """Build a file response using cache policy; return 304 when request validators match the current file.

    Callers obtain ``stat_result`` and ``media_type`` during path validation, usually in a worker thread with
    ``guess_media_type`` for the latter. This function performs no I/O; omitted ``media_type`` lets ``FileResponse``
    infer it in the current thread. ``FileResponse`` still handles Range / If-Range; matching If-None-Match
    returns 304 before Range processing, following RFC 9110 §13.2.2 evaluation order."""
    merged = dict(headers or {})
    merged["Cache-Control"] = IMMUTABLE_CACHE_CONTROL if immutable else REVALIDATE_CACHE_CONTROL
    response = FileResponse(path, headers=merged, media_type=media_type, stat_result=stat_result)
    if request.method in ("GET", "HEAD") and _CONDITIONAL_CHECKER.is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response
