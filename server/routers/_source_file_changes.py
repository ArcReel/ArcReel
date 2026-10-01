"""整本源文文件改动（``SourceFileChangeOutcome`` / ``SourceFileChangeError``）→ HTTP 响应的共享映射。

命令从 ``episodes_view``（「分集」视图的文件条）与 ``files``（上传、按文件名写入与删除源文件）两个 router 冒出来，
同一结果给出同一种响应形态。
"""

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException

from lib.episode.source_file_changes import (
    SourceFileChangeError,
    SourceFileChangeOutcome,
    render_source_file_impact_text,
)
from server.i18n import Translator
from server.routers.episode_management import episode_has_active_tasks

_STATUS: dict[str, int] = {
    "source_file_not_found": 404,
    "source_file_unreadable": 422,
    "source_text_empty": 422,
}


def source_file_change_payload(
    outcome: SourceFileChangeOutcome, project: Mapping[str, Any], _t: Translator
) -> dict[str, Any]:
    """已执行时返回 ``status=applied``；需要确认时返回服务端成文的受影响集清单与它的 ``revision``。

    ``project`` 须是改动前的项目，确认清单里的集按改动前的标题或播出位置指称。
    """
    impact = outcome.impact.to_dict()
    if outcome.applied:
        return {"status": "applied", "impact": impact}
    text = render_source_file_impact_text(impact, project, _t)
    return {"status": "confirmation_required", "impact": {**impact, "text": text}, "revision": outcome.revision}


def source_file_change_http_error(exc: SourceFileChangeError, _t: Translator) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 409), detail=_t(f"source_file_change_{exc.code}"))


async def ensure_no_displaced_tasks(
    project_name: str,
    revision: str | None,
    preview: Callable[[], SourceFileChangeOutcome],
    _t: Translator,
) -> None:
    """带着 ``revision`` 执行前，用 ``preview``（命令的 ``dry_run``）重算受影响集清单。

    清单与确认过的一致、而要退下或移除的集还有排队或执行中的任务时返回 409，不写入。不带 ``revision``
    或清单已经变了时不拦，交给命令本身返回确认。
    """
    if revision is None:
        return
    outcome = await asyncio.to_thread(preview)
    if outcome.revision != revision:
        return
    for episode in (*outcome.impact.retired, *outcome.impact.removed):
        if await episode_has_active_tasks(project_name, episode):
            raise HTTPException(status_code=409, detail=_t("source_file_change_tasks_active"))


__all__ = ["ensure_no_displaced_tasks", "source_file_change_http_error", "source_file_change_payload"]
