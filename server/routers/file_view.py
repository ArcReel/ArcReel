"""文件视图：按目录懒加载项目文件并标出角色，预览与下载任意单个文件（鉴权，含文本与 JSON）。

与匿名媒体端点（``files.public_router``）分开：那边的白名单不变，这里挂在受保护的 router 上。
基础设施文件、符号链接与非法路径一律与文件不存在同形返回 404。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.infra.async_thread import run_sync_transaction
from lib.project.file_view import iter_file_chunks, list_directory, move_file_to_trash, open_project_file
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.routers._file_responses import guess_media_type

router = APIRouter(prefix="/projects/{project_name}/file-view")

#: 文本类文件按 UTF-8 文本返回，便于预览；其余按扩展名推断。
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".json", ".py", ".yaml", ".yml", ".srt", ".log", ".csv"})


def _project_dir(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_path(project_name)
    except ValueError as exc:
        raise BadRequestError("invalid_project_name", name=project_name) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.get("")
async def list_file_view(project_name: str, path: str = Query("")):
    """列出 ``path`` 目录（缺省为项目根）的直接子项。"""

    def _sync() -> dict[str, object]:
        project_dir = _project_dir(project_name)
        try:
            entries = list_directory(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return {"path": path, "entries": [entry.to_dict() for entry in entries]}

    return await asyncio.to_thread(_sync)


@router.delete("")
async def delete_file_view_entry(project_name: str, path: str = Query(...)):
    """把一个未引用文件移进项目回收站；其余角色在服务端一律拒绝。"""

    def _sync() -> dict[str, object]:
        project_dir = _project_dir(project_name)
        try:
            with project_change_source("webui"):
                record = move_file_to_trash(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        return {"record": record.to_dict()}

    return await run_sync_transaction(_sync)


@router.get("/content")
async def read_file_view_content(project_name: str, path: str = Query(...), download: bool = Query(False)):
    """预览或下载单个文件。"""

    name = Path(path).name

    def _sync() -> tuple[BinaryIO, int, str]:
        project_dir = _project_dir(project_name)
        try:
            handle, info = open_project_file(project_dir, path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise NotFoundError("file_not_found", path=path) from exc
        text = Path(name).suffix.lower() in _TEXT_SUFFIXES
        return handle, info.st_size, "text/plain; charset=utf-8" if text else guess_media_type(name)

    handle, size, media_type = await asyncio.to_thread(_sync)
    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Length": str(size),
        "Content-Disposition": f"{disposition}; filename*=utf-8''{quote(name)}",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        # 项目里可能有 Agent 写下的 HTML / SVG，直接打开时不让其中的脚本以本站身份运行。
        "Content-Security-Policy": "sandbox",
    }
    return StreamingResponse(iter_file_chunks(handle), media_type=media_type, headers=headers)
