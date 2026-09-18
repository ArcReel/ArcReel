"""成片演示的选中媒体落盘路径解析。

打包下载与社交分发都要把「读模型选中的版本」换成磁盘上的真实文件，且都必须经
``safe_join`` 收口——相对路径来自 manifest，越界即拒。放在共享模块而不是各自复制一份：
两处对「文件不在了」的回法必须一致（都退化成 ``PresentationUnavailableError``），
复制会让其中一处在后续演化里退回 500。
"""

from __future__ import annotations

from pathlib import Path

from lib.path_safety import PathTraversalError, safe_join
from server.services.presentation_read_model import PresentationUnavailableError


def selected_media_path(project_path: Path, relative_path: str) -> Path:
    """把读模型给的相对路径换成项目内的真实文件路径。"""
    try:
        return safe_join(project_path, relative_path, require_file=True)
    except (PathTraversalError, FileNotFoundError) as exc:
        raise PresentationUnavailableError("selected presentation media is unavailable") from exc


__all__ = ["selected_media_path"]
