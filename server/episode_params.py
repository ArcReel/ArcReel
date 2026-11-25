"""Shared Pydantic request parameter definitions for episode-scoped server tools."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

PositiveEpisode = Annotated[int, Field(strict=True, ge=1)]
EPISODE_ID_DESCRIPTION = (
    "集 ID：一集的内部标识，取自项目详情 episodes[].episode 或制作计划的 episode_id，不是播出顺序中的第几集"
)

__all__ = ["EPISODE_ID_DESCRIPTION", "PositiveEpisode"]
