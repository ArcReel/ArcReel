"""AI repair of quarantined drafts: let the text model repair reported violations and revalidate using manual-save rules.

When every violation locates an item, send only affected items and restore them by index, preserving all others.
Modify the whole document only for episode-level/unlocated violations. Additional instructions apply to the call only
and are not saved. ``DraftWorkflow.save`` writes and fully revalidates: adopt if violations clear, otherwise retain
the draft and refresh its report against current values.

Like ``DraftWorkflow``, this service is host-neutral. ``server.tool_runtime.repair_draft`` preflights with ``check``
before submitting ``repair`` as a queued text task."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextOutputTruncatedError, TextTaskType
from lib.backends.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.backends.text_generator import TextGenerator
from lib.catalog.errors import ModelNotInCatalogError
from lib.generation.video_request_facts import VideoRequestFactsError
from lib.infra.content_digest import unprefixed
from lib.infra.text_utils import strip_json_code_fences
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script.draft_quarantine import (
    DRAFT_OWNER_AGENT,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QuarantinedDraft,
    draft_owner,
    draft_revision,
    quarantine_exists,
    read_quarantine,
    violation_entries,
)
from server.draft_workflow import DraftContext, DraftWorkflow, DraftWorkflowError, revalidate_script_plan_draft
from server.text_generation import load_novel_source

logger = logging.getLogger(__name__)

#: Item-array keys by draft kind.
_ITEM_ROOTS: dict[str, str] = {
    QUARANTINE_KIND_SCRIPT_PLAN: "units",
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: "scenes",
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: "segments",
    QUARANTINE_KIND_PROMPT_AUTHORING: "units",
}


#: Model-facing draft descriptions by draft kind.
_DOCUMENTS: dict[str, str] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: (
        "Drama script-planning draft: `scenes` contains ordered storyboards, each with a duration tier, appearing assets, visual description, verbatim dialogue and source anchor."
    ),
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: (
        "Narration script-planning draft: `segments` contains ordered storyboards; `novel_text` excerpts the source verbatim. "
        "All storyboard text joined in order must cover this episode's source text."
    ),
    QUARANTINE_KIND_SCRIPT_PLAN: (
        "Reference-to-video script-planning draft: `units` contains ordered video units; `source_text` excerpts the source verbatim. "
        "`text` is the unit body, referencing registered assets with `@[名称]`."
    ),
    QUARANTINE_KIND_PROMPT_AUTHORING: (
        "Reference-to-video prompt-authoring draft: `units` corresponds one-to-one to the formal script's video units. "
        "`text` is that unit's prompt body, referencing registered assets with `@[名称]`."
    ),
}


@dataclass(frozen=True, slots=True)
class RepairScope:
    """Repair scope: ascending item ``indices`` to change, or None for whole-document repair."""

    root: str
    indices: tuple[int, ...] | None


def repair_scope(kind: str, content: dict[str, Any], violations: Sequence[dict[str, Any]]) -> RepairScope:
    """Choose repair scope from violation locations; any violation not locating an existing item requires whole-document repair."""
    root = _ITEM_ROOTS[kind]
    items = content.get(root)
    if not isinstance(items, list):
        return RepairScope(root, None)
    indices: set[int] = set()
    for violation in violations:
        index = violation.get("item_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(cast(list[Any], items)):
            return RepairScope(root, None)
        indices.add(index)
    return RepairScope(root, tuple(sorted(indices)))


def merge_repair(content: dict[str, Any], scope: RepairScope, response: object) -> dict[str, Any]:
    """Merge model replies into draft content: item mode replaces only scoped items; episode mode replaces existing top-level fields.

    Unexpected reply shapes raise ``ValueError``."""
    if not isinstance(response, dict):
        raise ValueError("模型回复不是 JSON 对象")
    reply = cast(dict[str, Any], response)
    items = reply.get(scope.root)
    if scope.indices is None:
        if not isinstance(items, list) or not items:
            raise ValueError(f"模型回复缺少非空的 {scope.root} 数组")
        # Keep only original top-level fields/item arrays; do not add model-invented fields.
        return {**content, **{key: value for key, value in reply.items() if key in content or key == scope.root}}
    if not isinstance(items, list) or len(cast(list[Any], items)) != len(scope.indices):
        raise ValueError(f"模型回复的 {scope.root} 条目数与待修复条目数（{len(scope.indices)}）不符")
    merged = copy.deepcopy(content)
    for index, item in zip(scope.indices, cast(list[Any], items), strict=True):
        if not isinstance(item, dict):
            raise ValueError(f"模型回复的 {scope.root} 条目不是 JSON 对象")
        merged[scope.root][index] = item
    return merged


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_repair_prompt(
    kind: str,
    content: dict[str, Any],
    scope: RepairScope,
    violations: Sequence[dict[str, Any]],
    *,
    source_text: str | None,
    instructions: str | None,
) -> str:
    targets: list[dict[str, Any]] | None = None
    messages: list[str] | None = None
    if scope.indices is None:
        messages = [str(violation.get("message") or "") for violation in violations]
    else:
        items = content[scope.root]
        targets = [
            {
                "index": index,
                "item_json": _dumps(items[index]),
                "messages": [str(v.get("message") or "") for v in violations if v.get("item_index") == index],
            }
            for index in scope.indices
        ]
    return builtin_templates.render(
        "text/draft_repair",
        document=_DOCUMENTS[kind],
        root=scope.root,
        draft_json=_dumps(content),
        targets=targets,
        messages=messages,
        source_text=source_text,
        instructions=instructions,
    )


class DraftRepair:
    """Repair a quarantined draft with AI; raise ``DraftWorkflowError`` for failures."""

    def __init__(self, ctx: DraftContext):
        self.ctx = ctx
        self.workflow = DraftWorkflow(ctx)

    async def check(self, episode: int, doc_type: str, base_revision: str) -> tuple[str, QuarantinedDraft]:
        """Check AI-repair eligibility: current-workflow applicability, existing draft, non-Agent editing ownership and unchanged revision.

        Return draft kind/content or raise ``DraftWorkflowError``. Read-only, without model calls."""
        kind = await self.workflow.resolve_kind(episode, doc_type)

        def _read_draft() -> tuple[QuarantinedDraft | None, bool]:
            project_path = self.ctx.project_path
            draft = read_quarantine(project_path, episode, kind)
            return draft, draft is None and quarantine_exists(project_path, episode, kind)

        draft, unreadable = await asyncio.to_thread(_read_draft)
        if draft is None:
            detail = "is not a valid JSON envelope" if unreadable else "does not exist"
            raise DraftWorkflowError("draft_not_found", f"集（id={episode}）{doc_type} draft {detail}")
        if draft_owner(draft) == DRAFT_OWNER_AGENT:
            raise DraftWorkflowError(
                "draft_agent_owned", f"集（id={episode}）{doc_type} draft is being edited by the agent"
            )
        actual_revision = draft_revision(draft)
        if unprefixed(base_revision) != unprefixed(actual_revision):
            raise DraftWorkflowError(
                "revision_conflict", f"draft revision changed: expected {base_revision}, actual {actual_revision}"
            )
        return kind, draft

    async def repair(
        self, episode: int, doc_type: str, base_revision: str, instructions: str | None = None
    ) -> dict[str, Any]:
        """Repair and revalidate using save rules, returning the same result as ``DraftWorkflow.save``.

        Adopt violation-free drafts directly without model calls. Failures before writeback, including source reads, model calls
        and reply shape, raise ``draft_repair_failed`` without changing the draft. Propagate ``TextOutputTruncatedError`` unchanged
        for truncated replies, also preserving drafts. Post-writeback failures retain normal save-stage error codes."""
        try:
            content = await self._repaired_content(episode, doc_type, base_revision, instructions)
        except (DraftWorkflowError, TextOutputTruncatedError, ModelNotInCatalogError):
            # Missing referenced catalog models are reselectable configuration issues; propagate unchanged for caller provider-configuration guidance.
            raise
        except Exception as exc:
            raise DraftWorkflowError("draft_repair_failed", f"AI 修复未完成：{exc}") from exc
        return await self.workflow.save(episode, doc_type, content, base_revision)

    async def _repaired_content(
        self, episode: int, doc_type: str, base_revision: str, instructions: str | None
    ) -> dict[str, Any]:
        """Draft content to write back: return unchanged if violation-free, otherwise repair with the model and merge into the original."""
        kind, draft = await self.check(episode, doc_type, base_revision)
        violations = await self._violations(episode, kind, draft)
        if not violations:
            return draft.content

        scope = repair_scope(kind, draft.content, violations)
        prompt = build_repair_prompt(
            kind,
            draft.content,
            scope,
            violations,
            source_text=await self._source_text(episode, kind, draft),
            instructions=(instructions or "").strip() or None,
        )
        generator = await TextGenerator.create(
            TextTaskType.SCRIPT, self.ctx.project_name, purpose=CallPurpose.SCRIPT_GENERATION
        )
        result = await generator.generate(
            BackendTextGenerationRequest(prompt=prompt, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS),
            project_name=self.ctx.project_name,
            require_complete=True,
        )
        return merge_repair(draft.content, scope, json.loads(strip_json_code_fences(result.text)))

    async def _violations(self, episode: int, kind: str, draft: QuarantinedDraft) -> list[dict[str, Any]]:
        """Repair violations: revalidate script-planning drafts against current values; use snapshots for prompt-authoring drafts without read-only revalidators."""
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return draft.violations
        project = await asyncio.to_thread(self.ctx.pm.load_project, self.ctx.project_name)
        try:
            revalidation = await revalidate_script_plan_draft(
                await self.ctx.aproject_path(), project, episode, draft, config_resolver=self.ctx.config_resolver
            )
        except VideoRequestFactsError as exc:
            raise DraftWorkflowError.video_facts(exc.failure) from exc
        except ModelNotInCatalogError:
            raise
        except ValueError as exc:
            raise DraftWorkflowError("draft_repair_failed", f"草稿无法重新校验：{exc}") from exc
        return violation_entries(revalidation.violations)

    async def _source_text(self, episode: int, kind: str, draft: QuarantinedDraft) -> str | None:
        """Source text for script-planning drafts, letting models check source anchors; omit if unreadable."""
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return None
        try:
            return await asyncio.to_thread(
                lambda: load_novel_source(self.ctx.project_path, draft.meta.get("source"), episode=episode)
            )
        except ValueError as exc:
            logger.info("AI 修复不附带源文 episode=%s：%s", episode, exc)
            return None
