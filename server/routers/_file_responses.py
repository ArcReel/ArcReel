"""公开媒体端点共用的文件响应：缓存头策略 + 条件请求（304）。

Starlette 的 ``FileResponse`` 会发 ETag / Last-Modified 并处理 Range，但自身从不返回 304；
条件请求只在 ``StaticFiles.file_response`` 里判定。这里复用 ``StaticFiles.is_not_modified``
与 ``NotModifiedResponse``，ETag 也由 ``FileResponse`` 按同一份 ``stat_result`` 生成，
因此与 Starlette 静态文件服务的再验证语义完全一致。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import NotModifiedResponse

#: 带版本键（?v= / ?fp= / versions/ 快照）的 URL 内容不变，浏览器可永久复用
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
#: 无版本键的 URL 内容可能被原地覆盖：允许缓存，但每次使用前必须再验证（命中返回 304）
REVALIDATE_CACHE_CONTROL = "no-cache"

# 只借用 is_not_modified 的判定逻辑（If-None-Match 优先，其次 If-Modified-Since），
# 不挂载、不读目录；directory=None 时构造不触碰文件系统
_CONDITIONAL_CHECKER = StaticFiles(directory=None, check_dir=False)


def cached_file_response(
    path: str | os.PathLike[str],
    stat_result: os.stat_result,
    request: Request,
    *,
    immutable: bool,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """按缓存策略构造文件响应；请求带的校验器与当前文件一致时返回 304。

    ``stat_result`` 由调用方在校验路径时一并取得（通常在工作线程里），本函数不做 I/O。
    Range / If-Range 仍由 ``FileResponse`` 自身处理；If-None-Match 命中时先于 Range
    返回 304（RFC 9110 §13.2.2 的求值顺序）。
    """
    merged = dict(headers or {})
    merged["Cache-Control"] = IMMUTABLE_CACHE_CONTROL if immutable else REVALIDATE_CACHE_CONTROL
    response = FileResponse(path, headers=merged, stat_result=stat_result)
    if request.method in ("GET", "HEAD") and _CONDITIONAL_CHECKER.is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response
