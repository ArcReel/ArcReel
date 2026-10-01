"""上传的源文件规范化为文本时的失败（``SourceLoader`` 的异常）→ HTTP 响应的共享映射。"""

import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from lib.script.source_loader import (
    CorruptFileError,
    FileSizeExceededError,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from server.i18n import Translator

SourceLoaderError = UnsupportedFormatError | FileSizeExceededError | SourceDecodeError | CorruptFileError


def source_loader_http_error(exc: SourceLoaderError, _t: Translator) -> HTTPException:
    if isinstance(exc, UnsupportedFormatError):
        return HTTPException(status_code=400, detail=_t("source_unsupported_format", ext=exc.ext))
    if isinstance(exc, FileSizeExceededError):
        return HTTPException(
            status_code=413,
            detail=_t(
                "source_too_large",
                filename=exc.filename,
                size_mb=round(exc.size_bytes / 1024 / 1024, 1),
                limit_mb=round(exc.limit_bytes / 1024 / 1024, 1),
            ),
        )
    if isinstance(exc, SourceDecodeError):
        return HTTPException(
            status_code=422,
            detail=_t("source_decode_failed", filename=exc.filename, tried=", ".join(exc.tried_encodings)),
        )
    return HTTPException(status_code=422, detail=_t("source_corrupt_file", filename=exc.filename, reason=exc.reason))


def extract_uploaded_source_text(file: UploadFile, _t: Translator) -> str:
    """把上传的文件规范化为 UTF-8 文本，不落到 ``source/``；格式或内容不合格时抛出对应的 ``HTTPException``。

    同步执行，经临时文件读取上传内容；路由里用 ``asyncio.to_thread`` 调用。
    """
    original_filename = file.filename or ""
    with tempfile.NamedTemporaryFile(suffix=Path(original_filename).suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tmp_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        return SourceLoader.extract(tmp_path, original_filename=original_filename).text
    except (UnsupportedFormatError, FileSizeExceededError, SourceDecodeError, CorruptFileError) as exc:
        raise source_loader_http_error(exc, _t) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


__all__ = ["extract_uploaded_source_text", "source_loader_http_error"]
