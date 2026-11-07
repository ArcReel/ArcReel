"""Shared mapping from rejected episode-source registration (``EpisodeSourceError``) to HTTP responses.

Registration commands originate in three routers: ``files`` (upload/delete sources), ``projects`` (enter episode-page source), and ``episodes_view`` (handle unregistered files).
The same reason code produces the same status and wording across endpoints.
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
    """``HTTPException`` for ``EpisodeSourceError``; unregistered reason codes use 422 for invalid content."""
    key = "episode_source_episode_not_found" if exc.code == "episode_not_found" else exc.code
    params = {name: value for name, value in (("episode", episode), ("filename", filename)) if value is not None}
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=_t(key, **params))


__all__ = ["episode_source_http_error"]
