"""Host-neutral BGM tools: list tracks or import an existing project audio file.

The handler is a thin adapter over the lib BGM command; domain errors keep their stable codes as tool problems.
"""

from __future__ import annotations

import asyncio
import os
import stat
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from lib.bgm.library import BGM_EXTENSIONS, BGM_MAX_BYTES, BgmTrack
from lib.bgm.service import BgmError, BgmLibraryService
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.path_safety import PathTraversalError, safe_join
from server.media_tools.context import tool_error, tool_problem
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest


class ListBgmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportBgmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: str = Field(min_length=1, description="Project-relative path to an existing MP3, WAV or M4A file")


class BgmListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    duration: float


class BgmImportResult(BgmListItem):
    integrated_lufs: float
    gain_db: float


def _read_audio_bytes(project_path: Path, relative: PurePosixPath) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | nofollow
    with ExitStack() as stack:
        if nofollow and os.open in os.supports_dir_fd:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
            directory = os.open(project_path, directory_flags)
            stack.callback(os.close, directory)
            for part in relative.parts[:-1]:
                directory = os.open(part, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)
            descriptor = os.open(relative.name, flags, dir_fd=directory)
            stack.callback(os.close, descriptor)
        else:
            current = project_path
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise PathTraversalError("Audio source contains a symbolic link")
            resolved = safe_join(project_path, relative, require_file=True)
            descriptor = os.open(resolved, flags)
            stack.callback(os.close, descriptor)
            opened, after = os.fstat(descriptor), os.lstat(resolved)
            if (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino) or stat.S_ISLNK(after.st_mode):
                raise PathTraversalError("Audio source changed while opening")
            safe_join(project_path, relative, require_file=True)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PathTraversalError("Audio source is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(BGM_MAX_BYTES + 1)


def _read_project_audio(project_path: Path, source_path: str) -> tuple[str, bytes]:
    path = PurePosixPath(source_path)
    if path.is_absolute() or "\\" in source_path or any(part.startswith(".") for part in source_path.split("/")):
        raise BgmError("bgm_invalid_audio", "Choose an audio file inside this project.")
    if path.suffix.lower() not in BGM_EXTENSIONS:
        raise BgmError(
            "bgm_unsupported_type",
            "Choose an MP3, WAV or M4A file.",
            ext=path.suffix,
            allowed=", ".join(BGM_EXTENSIONS),
        )
    try:
        safe_join(project_path, source_path, require_file=True)
        content = _read_audio_bytes(project_path, path)
    except (OSError, ValueError) as exc:
        raise BgmError("bgm_invalid_audio", "The project audio file is missing or inaccessible.") from exc
    if len(content) > BGM_MAX_BYTES:
        raise BgmError(
            "bgm_too_large", "The audio file exceeds the project upload limit.", max_mb=BGM_MAX_BYTES // (1024 * 1024)
        )
    return path.name, content


async def import_bgm(
    request: ToolRequest[ImportBgmRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[BgmImportResult]:
    def read_source() -> tuple[str, bytes]:
        services.projects.load_project(scope.project_name)
        return _read_project_audio(services.projects.get_project_path(scope.project_name), request.value.source_path)

    try:
        filename, content = await asyncio.to_thread(read_source)
        track = await BgmLibraryService(services.projects).upload(
            scope.project_name, filename=filename, content=content
        )
    except BgmError as exc:
        key = {
            "bgm_invalid_audio": "invalid_audio_file",
            "bgm_unsupported_type": "unsupported_audio_type",
            "bgm_too_large": "upload_too_large",
        }.get(exc.code, exc.code)
        return tool_problem(translate(key, DEFAULT_LOCALE, **exc.params), code=exc.code, params=exc.params or None)
    except Exception as exc:
        return tool_error("import_bgm", exc)
    return ToolOutcome(
        value=BgmImportResult(
            id=track.id,
            name=track.name,
            duration=track.duration,
            integrated_lufs=track.integrated_lufs,
            gain_db=track.gain_db,
        )
    )


def bgm_import_summary(track: BgmImportResult) -> str:
    return f"Imported {track.name} as {track.id} ({track.duration}s; normalization gain {track.gain_db:+.2f} dB)."


def _item(track: BgmTrack) -> BgmListItem:
    return BgmListItem(id=track.id, name=track.name, duration=track.duration)


async def list_bgm(
    _request: ToolRequest[ListBgmRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[tuple[BgmListItem, ...]]:
    try:
        tracks = await BgmLibraryService(services.projects).list(scope.project_name)
    except BgmError as exc:
        return tool_problem(str(exc), code=exc.code, params=exc.params or None)
    except Exception as exc:
        return tool_error("list_bgm", exc)
    return ToolOutcome(value=tuple(_item(track) for track in tracks))


def bgm_list_summary(items: tuple[BgmListItem, ...]) -> str:
    if not items:
        return "项目里还没有 BGM；可用 import_bgm 登记项目内已有音频，或由创作者在剪辑视图的 BGM 轨上传。"
    return "\n".join(f"- {item.id}「{item.name}」{item.duration} 秒" for item in items)


__all__ = [
    "BgmImportResult",
    "BgmListItem",
    "ImportBgmRequest",
    "ListBgmRequest",
    "bgm_import_summary",
    "bgm_list_summary",
    "import_bgm",
    "list_bgm",
]
