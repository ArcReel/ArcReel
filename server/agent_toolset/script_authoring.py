"""文本生成、内容确认与草稿工具的声明。

两个文本生成工具是长任务：ArcReel Agent 等到文本任务终态，拿到 ``text_generation``；外部 Agent 立即拿到
``generation_batch`` 句柄并轮询。``dry_run`` 两宿主都直接返回 prompt。
"""

from __future__ import annotations

from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from lib.script.draft_quarantine import OPEN_DRAFT_TOOL_NAME, PROMOTE_TOOL_NAME
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.agent_toolset.envelope import json_value
from server.tool_runtime import (
    ConfirmScriptReviewRequest,
    DiscardDraftRequest,
    DraftLocator,
    GenerateEpisodeScriptRequest,
    GenerateScriptPlanRequest,
    PatchDraftRequest,
    PromoteDraftRequest,
    confirm_script_review,
    discard_draft,
    generate_episode_script,
    generate_script_plan,
    open_draft,
    patch_draft,
    promote_draft,
)

_MIGRATION_REFUSAL = "项目数据升级失败时拒绝执行，返回 project_migration_failed problem。"
_TEXT_GENERATION = "text_generation"
_GENERATION_BATCH = "generation_batch"
_DRAFT = "draft"


def _text_generation_structured(value: Any) -> dict[str, Any]:
    """批次句柄放在 ``generation_batch`` 下，生成结果与 dry_run 的 prompt 放在 ``text_generation`` 下。"""
    if isinstance(value, GenerationBatchReadModel):
        return {_GENERATION_BATCH: json_value(value)}
    return {_TEXT_GENERATION: json_value(value)}


GENERATE_EPISODE_SCRIPT = ToolDeclaration(
    name="generate_episode_script",
    description=(
        "提示词编写：为正式脚本中待编写的分镜 / 视频单元补出视觉层，输入是正式脚本自身的内容，不读脚本规划。"
        "默认只编写全部待编写条目；entry_ids 显式重写指定条目的视觉层。内容字段、备注、尾帧与已生成产物原样保留。"
        "ad 项目尚无正式脚本时整份生成。dry_run=true 时直接返回 prompt，不提交生成任务。" + _MIGRATION_REFUSAL
    ),
    request_model=GenerateEpisodeScriptRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_episode_script,
    long_task=True,
    projection=_text_generation_structured,
)

GENERATE_SCRIPT_PLAN = ToolDeclaration(
    name="generate_script_plan",
    description=(
        "按项目创作类型生成结构化 script_plan：剧情分镜、旁白分镜或参考生视频单元。"
        "广告/短片项目无 script_plan。dry_run=true 时直接返回 prompt，不提交生成任务。" + _MIGRATION_REFUSAL
    ),
    request_model=GenerateScriptPlanRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=generate_script_plan,
    long_task=True,
    projection=_text_generation_structured,
)

CONFIRM_SCRIPT_REVIEW = ToolDeclaration(
    name="confirm_script_review",
    description=(
        "确认本集 script_plan：整份转为正式脚本（全部分镜待编写），放行 prompt_authoring 视觉生成。"
        "仅在用户已明确认可进入视觉生成时调用。该集已有正式脚本时确认会整份覆盖它，未认可时返回"
        " script_overwrite_required 与将被移除的分镜清单（params.script_overwrite）；须向用户说明并取得同意后，"
        "再以清单中的 revision 作为 overwrite_revision 重新确认。" + _MIGRATION_REFUSAL
    ),
    request_model=ConfirmScriptReviewRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=confirm_script_review,
)

OPEN_DRAFT = ToolDeclaration(
    name=OPEN_DRAFT_TOOL_NAME,
    description=(
        "读取指定草稿；草稿不存在时从对应正式文档创建编辑副本。返回完整正文、违约与 canonical revision。"
        + _MIGRATION_REFUSAL
    ),
    request_model=DraftLocator,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=open_draft,
)

PATCH_DRAFT = ToolDeclaration(
    name="patch_draft",
    description=(
        "按 canonical revision 原子替换草稿正文；revision 冲突时拒绝且不写入。"
        "给出 accept_formal_revision 即接受正式文档的并发修改，给出 source 即更新重判范围，二者显式传 null 同样生效。"
        + _MIGRATION_REFUSAL
    ),
    request_model=PatchDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=patch_draft,
)

PROMOTE_DRAFT = ToolDeclaration(
    name=PROMOTE_TOOL_NAME,
    description=(
        "重新全量校验指定草稿；通过则晋升为正式文件并清除草稿，不通过则刷新违约报告。可反复调用。" + _MIGRATION_REFUSAL
    ),
    request_model=PromoteDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=promote_draft,
)

DISCARD_DRAFT = ToolDeclaration(
    name="discard_draft",
    description="按 canonical revision 丢弃指定草稿；正式文档保持不变。" + _MIGRATION_REFUSAL,
    request_model=DiscardDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=discard_draft,
)

SCRIPT_AUTHORING_TOOLS = (
    GENERATE_EPISODE_SCRIPT,
    GENERATE_SCRIPT_PLAN,
    CONFIRM_SCRIPT_REVIEW,
    OPEN_DRAFT,
    PATCH_DRAFT,
    PROMOTE_DRAFT,
    DISCARD_DRAFT,
)

__all__ = [
    "CONFIRM_SCRIPT_REVIEW",
    "DISCARD_DRAFT",
    "GENERATE_EPISODE_SCRIPT",
    "GENERATE_SCRIPT_PLAN",
    "OPEN_DRAFT",
    "PATCH_DRAFT",
    "PROMOTE_DRAFT",
    "SCRIPT_AUTHORING_TOOLS",
]
