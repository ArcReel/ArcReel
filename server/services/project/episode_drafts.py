"""一集草稿的 Web 读写：列出在场草稿、读取呈现视图、手修保存（违约清零即采用）、丢弃。

写入走 ``server.draft_workflow.DraftWorkflow``，与 Agent 的草稿工具同一套命令；本层只补 Web 呈现
需要的视图：脚本规划草稿的视图由内容确认服务按产出口径读时重算，参考生视频提示词编写草稿的视图
取草稿里的违约快照（每次生成、晋升与手修保存都会按现值刷新它），并按正式剧本给出条目 ID。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from lib.config.resolver import ConfigResolver
from lib.episode.episode_paths import episode_script_filename
from lib.generation.video_request_facts import VideoRequestFactsError
from lib.infra.json_io import load_json_or_none
from lib.project.project_manager import ProjectManager
from lib.script import script_review
from lib.script.draft_quarantine import (
    DOC_TYPE_TO_QUARANTINE_KIND,
    DRAFT_OWNER_AGENT,
    DRAFT_OWNER_USER,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_TO_DOC_TYPE,
    QuarantinedDraft,
    draft_owner,
    draft_revision,
    quarantine_exists,
    read_quarantine,
)
from server.draft_workflow import DraftContext, DraftWorkflow, DraftWorkflowError
from server.services.project.script_review import ScriptReviewService
from server.text_generation import (
    _fetch_reference_split_caps,
    _uses_reference_video_units,
    reference_soft_violations,
    soft_violation_entries,
)


class EpisodeDraftService:
    """一集草稿的 Web 读写入口；错误以 ``DraftWorkflowError`` 抛出，由 router 映射为 HTTP 响应。"""

    def __init__(self, pm: ProjectManager, *, config_resolver: ConfigResolver | None = None):
        self.pm = pm
        self.config_resolver = config_resolver

    def _workflow(self, project_name: str) -> DraftWorkflow:
        return DraftWorkflow(
            DraftContext(
                project_name=project_name,
                data_root=self.pm.data_root,
                pm=self.pm,
                config_resolver=self.config_resolver,
            )
        )

    def _applicable_kinds(self, project: dict[str, Any]) -> list[str]:
        kinds: list[str] = []
        script_plan_kind = script_review.script_plan_quarantine_kind(project)
        if script_plan_kind is not None:
            kinds.append(script_plan_kind)
        if _uses_reference_video_units(project):
            kinds.append(QUARANTINE_KIND_PROMPT_AUTHORING)
        return kinds

    async def _resolve_kind(self, project_name: str, doc_type: str) -> tuple[dict[str, Any], str]:
        kind = DOC_TYPE_TO_QUARANTINE_KIND.get(doc_type)
        if kind is None:
            raise DraftWorkflowError("invalid_request", f"unsupported doc_type: {doc_type}")
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if kind not in self._applicable_kinds(project):
            raise DraftWorkflowError(
                "doc_type_not_applicable", f"doc_type {doc_type} does not match the project workflow"
            )
        return project, kind

    async def list_drafts(self, project_name: str, episode: int) -> list[dict[str, Any]]:
        """本集在场的草稿摘要（``doc_type`` / ``editable_by`` / ``violation_count``），不做读时重算。"""
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        project_path = self.pm.get_project_path(project_name)

        def _summaries() -> list[dict[str, Any]]:
            summaries: list[dict[str, Any]] = []
            for kind in self._applicable_kinds(project):
                if not quarantine_exists(project_path, episode, kind):
                    continue
                draft = read_quarantine(project_path, episode, kind)
                summaries.append(
                    {
                        "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[kind],
                        "editable_by": draft_owner(draft) if draft is not None else DRAFT_OWNER_USER,
                        # 信封损坏本身就是一条待处置的违约。
                        "violation_count": len(draft.violations) if draft is not None else 1,
                    }
                )
            return summaries

        return await asyncio.to_thread(_summaries)

    async def get_draft(self, project_name: str, episode: int, doc_type: str) -> dict[str, Any]:
        """一份草稿的呈现视图；形状见 ``ScriptReviewService.get_quarantine_info``，另带 ``item_ids``。"""
        project, kind = await self._resolve_kind(project_name, doc_type)
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            view = await self._prompt_authoring_view(project_name, project, episode)
        else:
            view = await ScriptReviewService(self.pm, config_resolver=self.config_resolver).get_quarantine_info(
                project_name, episode
            )
            if view is not None:
                view["item_ids"] = None
        if view is None:
            raise DraftWorkflowError("draft_not_found", f"episode {episode} has no {doc_type} draft")
        return {"episode": episode, **view}

    async def save_draft(
        self, project_name: str, episode: int, doc_type: str, content: dict[str, Any], base_revision: str
    ) -> dict[str, Any]:
        """手修保存：写入后按晋升口径全量重判，违约清零即采用，否则返回刷新后的草稿视图。"""
        result = await self._workflow(project_name).save(episode, doc_type, content, base_revision)
        if result["adopted"]:
            return {"episode": episode, "doc_type": doc_type, "adopted": True, "draft": None}
        return {
            "episode": episode,
            "doc_type": doc_type,
            "adopted": False,
            "draft": await self.get_draft(project_name, episode, doc_type),
        }

    async def discard_draft(self, project_name: str, episode: int, doc_type: str, base_revision: str) -> dict[str, Any]:
        """丢弃草稿，回到正式内容；Agent 的可编辑草稿同样可以丢弃。"""
        return await self._workflow(project_name).discard(episode, doc_type, base_revision)

    async def _prompt_authoring_view(
        self, project_name: str, project: dict[str, Any], episode: int
    ) -> dict[str, Any] | None:
        project_path = self.pm.get_project_path(project_name)
        formal_path = project_path / "scripts" / episode_script_filename(episode)
        exists, draft, formal = await asyncio.to_thread(self._read_prompt_authoring, project_path, formal_path, episode)
        if not exists:
            return None
        view: dict[str, Any] = {
            "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[QUARANTINE_KIND_PROMPT_AUTHORING],
            "revision": None,
            "editable_by": DRAFT_OWNER_USER,
            "content": None,
            "violations": [],
            "soft_violations": [],
            "formal_exists": formal is not None,
            "item_ids": None,
        }
        if draft is None:
            view["violations"] = [
                {"code": "quarantine_unreadable", "label": "", "message": "", "line": None},
            ]
            return view
        view["revision"] = draft_revision(draft)
        view["editable_by"] = draft_owner(draft)
        view["item_ids"] = _prompt_authoring_item_ids(draft, formal)
        if view["editable_by"] == DRAFT_OWNER_AGENT:
            return view
        view["content"] = draft.content
        view["violations"] = draft.violations
        view["soft_violations"] = await self._prompt_authoring_soft_violations(
            project, episode, draft, view["item_ids"]
        )
        return view

    @staticmethod
    def _read_prompt_authoring(
        project_path: Path, formal_path: Path, episode: int
    ) -> tuple[bool, QuarantinedDraft | None, dict[str, Any] | None]:
        exists = quarantine_exists(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
        draft = read_quarantine(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING) if exists else None
        formal = load_json_or_none(formal_path)
        return exists, draft, formal if isinstance(formal, dict) else None

    async def _prompt_authoring_soft_violations(
        self, project: dict[str, Any], episode: int, draft: QuarantinedDraft, item_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        units = draft.content.get("units")
        if not isinstance(units, list):
            return []
        try:
            caps = await _fetch_reference_split_caps(project, config_resolver=self.config_resolver)
        except VideoRequestFactsError:
            return []
        texts = [str(unit.get("text") or "") if isinstance(unit, dict) else "" for unit in units]
        entries = soft_violation_entries(reference_soft_violations(texts, project, episode=episode, voice=caps.voice))
        if item_ids is not None:
            for entry in entries:
                index = entry["item_index"]
                if index < len(item_ids):
                    entry["item_id"] = item_ids[index]
        return entries


def _prompt_authoring_item_ids(draft: QuarantinedDraft, formal: dict[str, Any] | None) -> list[str] | None:
    """草稿正文 ``units[i]`` 对应的正式剧本单元 ID：生成留下的草稿记在 ``meta.unit_ids``，整份取回的对应全部单元。"""
    recorded = draft.meta.get("unit_ids")
    if isinstance(recorded, list):
        return [str(unit_id) for unit_id in recorded]
    units = formal.get("video_units") if formal is not None else None
    if not isinstance(units, list):
        return None
    return [str(unit.get("unit_id")) for unit in units if isinstance(unit, dict)]
