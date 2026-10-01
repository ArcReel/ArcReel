"""集原文登记被拒（``EpisodeSourceError``）→ HTTP 响应的共享映射。

登记命令从 ``files``（上传、删除源文件）、``projects``（集页填写集原文）与 ``episodes_view``（处置未登记文件）
三个 router 冒出来；同一个原因码在不同端点上给出同一个状态码与文案。
"""

from fastapi import HTTPException

from lib.episode.episode_source_commands import EpisodeSourceError
from server.i18n import Translator

_STATUS: dict[str, int] = {
    "source_file_not_found": 404,
    "episode_not_found": 404,
    "source_file_registered": 409,
    "episode_source_present": 409,
    "episode_source_derived": 409,
    "episode_source_symlink": 409,
    "source_kind_not_applicable": 409,
    "source_changed_outside": 409,
}


def episode_source_http_error(
    exc: EpisodeSourceError, _t: Translator, *, episode: int | None = None, filename: str | None = None
) -> HTTPException:
    """``EpisodeSourceError`` 对应的 ``HTTPException``；未登记的原因码按内容不合格落 422。"""
    key = "episode_source_episode_not_found" if exc.code == "episode_not_found" else exc.code
    params = {name: value for name, value in (("episode", episode), ("filename", filename)) if value is not None}
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=_t(key, **params))


__all__ = ["episode_source_http_error"]
