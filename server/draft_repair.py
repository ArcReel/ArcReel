"""待修复草稿的 AI 修复：按违约报告让文本模型改草稿，修完按手修保存的口径重判。

违约都落在条目上时，只把这些条目交给模型，取回后按下标放回原位，其余条目原样保留；有违约落在整集
层面（或定位不到条目）时才整份修改。附加指令随调用传入，不保存。修改结果经
``DraftWorkflow.save`` 写入并全量重判：违约清零即采用，否则留在草稿里，报告按现值刷新。

与 ``DraftWorkflow`` 同为宿主无关的服务；``server.tool_runtime.repair_draft`` 提交前用 ``check`` 预检，
再以排队文本任务执行 ``repair``。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextTaskType
from lib.backends.text_backends.base import TextGenerationRequest as BackendTextGenerationRequest
from lib.backends.text_generator import TextGenerator
from lib.generation.video_request_facts import VideoRequestFactsError
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

#: 各草稿来源的条目数组键名。
_ITEM_ROOTS: dict[str, str] = {
    QUARANTINE_KIND_SCRIPT_PLAN: "units",
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: "scenes",
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: "segments",
    QUARANTINE_KIND_PROMPT_AUTHORING: "units",
}


#: 交给模型的草稿说明，按草稿来源。
_DOCUMENTS: dict[str, str] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: (
        "剧情演绎的脚本规划草稿：`scenes` 是有序分镜，每个分镜含时长档位、出场资产、画面描述、逐字台词与原文锚。"
    ),
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: (
        "旁白/解说的脚本规划草稿：`segments` 是有序分镜，`novel_text` 逐字摘录源文，"
        "全部分镜按顺序拼起来应覆盖本集源文。"
    ),
    QUARANTINE_KIND_SCRIPT_PLAN: (
        "参考生视频的脚本规划草稿：`units` 是有序视频单元，`source_text` 逐字摘录源文，"
        "`text` 是单元正文，用 `@[名称]` 引用已登记的资产。"
    ),
    QUARANTINE_KIND_PROMPT_AUTHORING: (
        "参考生视频的提示词编写草稿：`units` 与正式剧本的视频单元一一对应，"
        "`text` 是该单元的提示词正文，用 `@[名称]` 引用已登记的资产。"
    ),
}


@dataclass(frozen=True, slots=True)
class RepairScope:
    """修复范围：``indices`` 为要改的条目下标（升序）；为 None 时整份修改。"""

    root: str
    indices: tuple[int, ...] | None


def repair_scope(kind: str, content: dict[str, Any], violations: Sequence[dict[str, Any]]) -> RepairScope:
    """按违约定位决定修复范围：任何一条定位不到现有条目，就整份修改。"""
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
    """把模型回复合并回草稿正文：条目模式只替换范围内的条目，整集模式以回复覆盖原有的顶层字段。

    回复不是约定的形状时抛 ``ValueError``。
    """
    if not isinstance(response, dict):
        raise ValueError("模型回复不是 JSON 对象")
    reply = cast(dict[str, Any], response)
    items = reply.get(scope.root)
    if scope.indices is None:
        if not isinstance(items, list) or not items:
            raise ValueError(f"模型回复缺少非空的 {scope.root} 数组")
        # 只取原草稿已有的顶层字段与条目数组，模型自造的字段不进草稿。
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
    """AI 修复一份待修复草稿；错误以 ``DraftWorkflowError`` 抛出。"""

    def __init__(self, ctx: DraftContext):
        self.ctx = ctx
        self.workflow = DraftWorkflow(ctx)

    async def check(self, episode: int, doc_type: str, base_revision: str) -> tuple[str, QuarantinedDraft]:
        """确认草稿可交给 AI 修复：适用于当前工作流、在场、不归 Agent 编辑、revision 未变。

        返回草稿来源与草稿；不成立时抛 ``DraftWorkflowError``。只读，不调用模型。
        """
        kind = await self.workflow.resolve_kind(episode, doc_type)
        draft = await asyncio.to_thread(read_quarantine, self.ctx.project_path, episode, kind)
        if draft is None:
            unreadable = await asyncio.to_thread(quarantine_exists, self.ctx.project_path, episode, kind)
            detail = "is not a valid JSON envelope" if unreadable else "does not exist"
            raise DraftWorkflowError("draft_not_found", f"集（id={episode}）{doc_type} draft {detail}")
        if draft_owner(draft) == DRAFT_OWNER_AGENT:
            raise DraftWorkflowError(
                "draft_agent_owned", f"集（id={episode}）{doc_type} draft is being edited by the agent"
            )
        actual_revision = draft_revision(draft)
        if base_revision != actual_revision:
            raise DraftWorkflowError(
                "revision_conflict", f"draft revision changed: expected {base_revision}, actual {actual_revision}"
            )
        return kind, draft

    async def repair(
        self, episode: int, doc_type: str, base_revision: str, instructions: str | None = None
    ) -> dict[str, Any]:
        """修复并按保存口径重判；返回值同 ``DraftWorkflow.save``。

        违约已清零的草稿不调用模型，直接采用。模型调用失败或回复形状不符时抛 ``draft_repair_failed``，
        草稿不变。
        """
        kind, draft = await self.check(episode, doc_type, base_revision)
        violations = await self._violations(episode, kind, draft)
        if not violations:
            return await self.workflow.save(episode, doc_type, draft.content, base_revision)

        scope = repair_scope(kind, draft.content, violations)
        prompt = build_repair_prompt(
            kind,
            draft.content,
            scope,
            violations,
            source_text=await self._source_text(episode, kind, draft),
            instructions=(instructions or "").strip() or None,
        )
        try:
            generator = await TextGenerator.create(
                TextTaskType.SCRIPT, self.ctx.project_name, purpose=CallPurpose.SCRIPT_GENERATION
            )
            result = await generator.generate(
                BackendTextGenerationRequest(prompt=prompt, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS),
                project_name=self.ctx.project_name,
            )
            merged = merge_repair(draft.content, scope, json.loads(strip_json_code_fences(result.text)))
        except Exception as exc:
            raise DraftWorkflowError("draft_repair_failed", f"AI 修复未完成：{exc}") from exc
        return await self.workflow.save(episode, doc_type, merged, base_revision)

    async def _violations(self, episode: int, kind: str, draft: QuarantinedDraft) -> list[dict[str, Any]]:
        """修复所依据的违约：脚本规划草稿按现值重判，提示词编写草稿取快照（它没有只读重判器）。"""
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return draft.violations
        project = await asyncio.to_thread(self.ctx.pm.load_project, self.ctx.project_name)
        try:
            revalidation = await revalidate_script_plan_draft(
                self.ctx.project_path, project, episode, draft, config_resolver=self.ctx.config_resolver
            )
        except (VideoRequestFactsError, ValueError) as exc:
            raise DraftWorkflowError("draft_repair_failed", f"草稿无法重新校验：{exc}") from exc
        return violation_entries(revalidation.violations)

    async def _source_text(self, episode: int, kind: str, draft: QuarantinedDraft) -> str | None:
        """脚本规划草稿的源文，供模型对照原文锚；读不出时不提供。"""
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return None
        try:
            return await asyncio.to_thread(
                load_novel_source, self.ctx.project_path, draft.meta.get("source"), episode=episode
            )
        except ValueError as exc:
            logger.info("AI 修复不附带源文 episode=%s：%s", episode, exc)
            return None
