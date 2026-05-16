"""SourceLoader 异常体系。

路由层根据异常类型映射到不同 HTTP 状态：
- UnsupportedFormatError → 400
- SourceDecodeError      → 422
- CorruptFileError       → 422
- FileSizeExceededError  → 413
- ConflictError          → 409
"""

from collections.abc import Sequence


class SourceLoaderError(Exception):
    pass


class UnsupportedFormatError(SourceLoaderError):
    def __init__(self, ext: str):
        self.ext = ext
        super().__init__(f"Unsupported source format: {ext}")


class SourceDecodeError(SourceLoaderError):
    def __init__(self, filename: str, tried_encodings: Sequence[str | None]):
        self.filename = filename
        self.tried_encodings = [e for e in tried_encodings if e]
        super().__init__(f"Failed to decode {filename} (tried: {', '.join(self.tried_encodings) or 'n/a'})")


class CorruptFileError(SourceLoaderError):
    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"{filename} corrupt or unreadable: {reason}")


class FileSizeExceededError(SourceLoaderError):
    def __init__(self, filename: str, size_bytes: int, limit_bytes: int):
        self.filename = filename
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(f"{filename} ({size_bytes} bytes) exceeds limit ({limit_bytes} bytes)")


class ConflictError(SourceLoaderError):
    def __init__(self, existing: str, suggested_name: str):
        self.existing = existing
        self.suggested_name = suggested_name
        super().__init__(f"File conflict: {existing}; suggested rename: {suggested_name}")
