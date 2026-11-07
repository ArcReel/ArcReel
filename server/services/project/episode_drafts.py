"""Web reads/writes for episode drafts: list existing drafts, read presentation views, save manual repairs (adopting once violations clear), and discard.

Writes use ``server.draft_workflow.DraftWorkflow``, sharing commands with Agent draft tools. This layer adds only Web presentation views:
content confirmation recalculates script-planning draft views on read using output rules. Reference-video prompt-authoring draft views
use stored violation snapshots refreshed against current values on every generation, promotion and manual save, with item IDs from the formal script."""

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
    fetch_reference_split_caps,
    reference_soft_violations,
    soft_violation_entries,
    uses_reference_video_units,
)


class EpisodeDraftService:
    """Web entry point for episode-draft reads/writes; raise ``DraftWorkflowError`` for router mapping to HTTP responses."""

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
        if uses_reference_video_units(project):
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
        """Summaries of existing episode drafts (``doc_type`` / ``editable_by`` / ``violation_count``), without recalculation on read."""
        project = await asyncio.to_thread(self.pm.load_project, project_name)

        def _summaries() -> list[dict[str, Any]]:
            project_path = self.pm.get_project_path(project_name)
            summaries: list[dict[str, Any]] = []
            for kind in self._applicable_kinds(project):
                if not quarantine_exists(project_path, episode, kind):
                    continue
                draft = read_quarantine(project_path, episode, kind)
                summaries.append(
                    {
                        "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[kind],
                        "editable_by": draft_owner(draft) if draft is not None else DRAFT_OWNER_USER,
                        # A corrupt envelope is itself a violation requiring handling.
                        "violation_count": len(draft.violations) if draft is not None else 1,
                    }
                )
            return summaries

        return await asyncio.to_thread(_summaries)

    async def get_draft(self, project_name: str, episode: int, doc_type: str) -> dict[str, Any]:
        """A draft presentation view, shaped like ``ScriptReviewService.get_quarantine_info`` with additional ``item_ids``."""
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
        """Save manual repairs: fully revalidate using promotion rules after writing; adopt if no violations remain, otherwise return the refreshed draft view."""
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
        """Discard the draft and return to formal content; editable Agent drafts can also be discarded."""
        return await self._workflow(project_name).discard(episode, doc_type, base_revision)

    async def _prompt_authoring_view(
        self, project_name: str, project: dict[str, Any], episode: int
    ) -> dict[str, Any] | None:
        def _read() -> tuple[bool, QuarantinedDraft | None, dict[str, Any] | None]:
            project_path = self.pm.get_project_path(project_name)
            formal_path = project_path / "scripts" / episode_script_filename(episode)
            return self._read_prompt_authoring(project_path, formal_path, episode)

        exists, draft, formal = await asyncio.to_thread(_read)
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
            caps = await fetch_reference_split_caps(project, config_resolver=self.config_resolver)
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
    """Formal-script unit IDs corresponding to draft ``units[i]``: generated drafts store them in ``meta.unit_ids``; full retrieval corresponds to all units."""
    recorded = draft.meta.get("unit_ids")
    if isinstance(recorded, list):
        return [str(unit_id) for unit_id in recorded]
    units = formal.get("video_units") if formal is not None else None
    if not isinstance(units, list):
        return None
    return [str(unit.get("unit_id")) for unit in units if isinstance(unit, dict)]
