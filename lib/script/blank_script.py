"""「从空白开始」：在本集没有正式脚本时建出一份空的正式脚本，由创作者在时间线上逐条手写。

四种骨架是同一个动作，按项目两轴定条目数组。未确认的脚本规划与其待修复草稿随之弃置，确认记录一并
删去：此后生成的规划没有确认指纹，一律待确认，经覆盖确认才整集替换。
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from lib.artifacts.artifact_currency import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.project.project_manager import ProjectManager, find_episode
from lib.script import script_review
from lib.script.draft_quarantine import (
    DRAFT_OWNER_AGENT,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    clear_quarantine,
    draft_owner,
    quarantine_path,
    read_quarantine,
)
from lib.script.script_document import build_blank_script


class BlankScriptError(ValueError):
    """从空白开始被拒；``code`` 是稳定的拒绝码。

    - ``episode_not_found``：账本里没有这一集；
    - ``formal_script_exists``：本集已有正式脚本（按产物清单判，与制作状态同一口径），或规范路径上
      放着别集剧本；
    - ``draft_agent_owned``：Agent 正在编辑本集的脚本规划草稿。
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def start_blank_script(pm: ProjectManager, project_name: str, episode: int) -> str:
    """为本集建出空的正式脚本，返回剧本文件名。

    锁序与内容确认一致：草稿 → 正式脚本规划 → 正式脚本。先写正式脚本再弃置规划：弃置失败时留下的是
    一份待确认的规划，不会出现规划已删、正式脚本没建成的半场。规范路径上未登记进产物清单的剧本文件
    不算正式脚本，会被空脚本覆盖。
    """
    project = pm.load_project(project_name)
    if find_episode(project, episode) is None:
        raise BlankScriptError("episode_not_found", f"集（id={episode}）不在分集账本里")
    project_path = pm.get_project_path(project_name)
    plan_path = script_review.script_plan_path(project_path, project, episode)
    draft_kinds = [
        kind
        for kind in (
            script_review.script_plan_quarantine_kind(project),
            QUARANTINE_KIND_PROMPT_AUTHORING if script_review.script_plan_kind(project) == "reference_video" else None,
        )
        if kind is not None
    ]

    with ExitStack() as locks:
        for kind in draft_kinds:
            locks.enter_context(pm.file_lock(quarantine_path(project_path, episode, kind)))
        if plan_path is not None:
            locks.enter_context(script_review.formal_script_plan_lock(project_path, episode, plan_path))
        project = pm.load_project(project_name)
        try:
            filename = script_review.formal_script_filename(project_path, project, episode)
        except script_review.ForeignFormalScriptError as exc:
            raise BlankScriptError("formal_script_exists", str(exc)) from exc
        resolver = active_artifact_currency_resolver(project_path, project)
        key = ArtifactKey.episode_script(episode)
        if resolver.resolve_usable_entry(key, artifact_path=f"scripts/{filename}") is not None:
            raise BlankScriptError("formal_script_exists", f"集（id={episode}）已有正式脚本 scripts/{filename}")
        for kind in draft_kinds:
            draft = read_quarantine(project_path, episode, kind)
            if draft is not None and draft_owner(draft) == DRAFT_OWNER_AGENT:
                raise BlankScriptError("draft_agent_owned", f"集（id={episode}）的草稿正由 Agent 编辑")

        def _drop_review(p: dict[str, Any]) -> None:
            entry = find_episode(p, episode)
            if entry is None:
                raise BlankScriptError("episode_not_found", f"集（id={episode}）不在分集账本里")
            # 旧的确认记录属于已弃置的规划，留着会让之后同内容的规划被当成已确认。
            entry.pop(script_review.REVIEW_FIELD, None)

        pm.save_script(project_name, build_blank_script(project, episode), filename, project_update=_drop_review)

        if plan_path is not None and plan_path.exists():
            with script_review.formal_script_plan_write_transaction(project_path, episode, plan_path):
                plan_path.unlink()
        for kind in draft_kinds:
            clear_quarantine(project_path, episode, kind)
    return filename


__all__ = ["BlankScriptError", "start_blank_script"]
