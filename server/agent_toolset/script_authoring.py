"""Text generation, content confirmation, and draft declarations.

The two text-generation tools are long tasks: ArcReel Agent waits for terminal text tasks and receives ``text_generation``; external Agents immediately receive a ``generation_batch`` handle and poll. ``dry_run`` returns the prompt directly on both hosts."""

from __future__ import annotations

from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from lib.script.draft_quarantine import OPEN_DRAFT_TOOL_NAME, PROMOTE_TOOL_NAME
from server.agent_toolset.declaration import BLOCKED, TEXT_OUTPUT_TRUNCATED_NOTE, ToolDeclaration
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

_TEXT_GENERATION = "text_generation"
_GENERATION_BATCH = "generation_batch"
_DRAFT = "draft"


def _text_generation_structured(value: Any) -> dict[str, Any]:
    """Place batch handles under ``generation_batch`` and generation results / dry_run prompts under ``text_generation``."""
    if isinstance(value, GenerationBatchReadModel):
        return {_GENERATION_BATCH: json_value(value)}
    return {_TEXT_GENERATION: json_value(value)}


GENERATE_EPISODE_SCRIPT = ToolDeclaration(
    name="generate_episode_script",
    description=(
        "Prompt authoring: complete the visual layer of pending shots / video units in the formal script, using that script’s own content rather than script_plan. Default behavior fills gaps: scope is all pending entries or entries named by entry_ids; image and video prompts are each assessed as a whole, retaining existing ones and filling only the missing one. For reference video, expand unit bodies by pending markers. rewrite=true explicitly rewrites the entire visual layer of scoped entries. If existing content would be overwritten, returns prompt_overwrite_required; the response body is the server-generated loss list, and params.prompt_overwrite.revision is the acceptance token. Relay the list first, obtain user agreement, then retry with that revision as overwrite_revision. Content fields, notes, end frames, and generated artifacts remain unchanged. If an ad / short-film project has no formal script, generate it in full and make the result formal; register new referenced characters / scenes / props according to new_assets decisions as assets awaiting generation, listing them in the response. regenerate=true redoes the whole existing formal script: first returns script_overwrite_required and a loss list, with params.script_overwrite.revision as acceptance token; follow the same confirmation flow. Contract violations in full generation fail the task (ad_script_rejected), leaving formal script, assets, and drafts unchanged; retry with targeted additional instructions if needed. dry_run=true returns the prompt directly without submitting a generation task."
        + TEXT_OUTPUT_TRUNCATED_NOTE
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
        "Generate structured script_plan according to project content mode: drama shots, narration shots, or reference-video units, also listing this episode’s unregistered assets and handling decisions in new_assets. Ad / short-film projects have no script_plan. dry_run=true returns the prompt directly without submitting a generation task."
        + TEXT_OUTPUT_TRUNCATED_NOTE
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
        "Confirm this episode’s script_plan: convert the whole document to a formal script (all shots pending prompt authoring), register this episode’s new assets according to new_assets decisions, and admit prompt_authoring visual generation. Call only after the user explicitly agrees to proceed to visual generation. If a formal script already exists, confirmation overwrites it completely. Without acceptance, returns script_overwrite_required; the response body is the server-generated loss list (the same text as the Web confirmation dialog), and params.script_overwrite.revision is the acceptance token. Relay the list, obtain agreement, then confirm again with that revision as overwrite_revision."
    ),
    request_model=ConfirmScriptReviewRequest,
    migration=BLOCKED,
    domain_key=_TEXT_GENERATION,
    handler=confirm_script_review,
)

OPEN_DRAFT = ToolDeclaration(
    name=OPEN_DRAFT_TOOL_NAME,
    description=(
        "Read the specified draft; if absent, create an editing copy from the corresponding formal document. Returns the full body, violations from the previous output or promotion assessment, canonical revision, and the formal document’s formal_revision (the same representation as get_script_plan_content and the production plan)."
    ),
    request_model=DraftLocator,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=open_draft,
)

PATCH_DRAFT = ToolDeclaration(
    name="patch_draft",
    description=(
        "Atomically replace a draft body by canonical revision; reject revision conflicts without writing. Supplying accept_formal_revision accepts concurrent changes to the formal document; supplying source updates the reassessment scope. Explicit null values for either also take effect. The response contains the new body and revision but no violations: this tool does not reassess; promote_draft’s full reassessment determines violations after editing."
    ),
    request_model=PatchDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=patch_draft,
)

PROMOTE_DRAFT = ToolDeclaration(
    name=PROMOTE_TOOL_NAME,
    description="Fully revalidate the specified draft; if valid, promote it to a formal file and clear the draft; otherwise refresh the violation report. May be called repeatedly.",
    request_model=PromoteDraftRequest,
    migration=BLOCKED,
    domain_key=_DRAFT,
    handler=promote_draft,
)

DISCARD_DRAFT = ToolDeclaration(
    name="discard_draft",
    description="Discard the specified draft by canonical revision, moving it to project trash; the formal document remains unchanged.",
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
