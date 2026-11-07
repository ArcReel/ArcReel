"""Episode-planning declarations: plan the next source window or reset the ledger to unplanned.

``plan_episodes`` is a long task: ArcReel Agent waits for completion and receives ``episode_plan``; external Agents immediately receive a ``generation_batch`` handle and poll. Reset makes no text-model call and returns synchronously on both hosts."""

from __future__ import annotations

from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from server.agent_toolset.declaration import BLOCKED, TEXT_OUTPUT_TRUNCATED_NOTE, ToolDeclaration
from server.agent_toolset.envelope import json_value
from server.tool_runtime import (
    PlanEpisodesRequest,
    PlanEpisodesResult,
    ResetEpisodePlanningRequest,
    plan_episodes,
    reset_episode_planning,
)


def _plan_structured(value: PlanEpisodesResult | GenerationBatchReadModel) -> dict[str, Any]:
    """Project batch handles under ``generation_batch`` and completed planning ledger summaries under ``episode_plan``."""
    if isinstance(value, GenerationBatchReadModel):
        return {"generation_batch": json_value(value)}
    return {"episode_plan": json_value(value)}


PLAN_EPISODES = ToolDeclaration(
    name="plan_episodes",
    description=(
        "Episode planning: read a source window from the end of the last extracted episode, following the registered whole-source file list. Call the configured text model once to plan all episodes with complete plot arcs in that window (title / hook / source range; drama also includes outlines), then write the ledger, derive source/episode_{episode ID}.txt, and clean residual derived files under the same project lock. New extracted episodes follow the last extracted episode; own-source and no-source episodes do not participate and retain their positions. Unlisted source/ files are not whole-source material and are not read. Returns a ledger summary (per-episode title, ID, playback position, hook, volume, and source opening/closing sentences, truncated if long), allowing the user to check boundaries. Allocate IDs above the project’s historical maximum; IDs are not playback order, so refer to episodes by title and playback position. The source window is fixed at 50000 characters, and the maximum episodes per batch derives from the current text model’s registered output limit; neither is configurable. If no complete-arc cut exists in the window, the batch errors: ask the user to split that range manually before continuing. Only one episode-planning operation may run per project; calls during Web AI episode planning are rejected. Target volume uses episode_target_units, otherwise converts episode_target_duration using speaking rate (the returned checking material identifies the source of conversion). Pass user-supplied planning instructions verbatim through instructions (such as chapter alignment or a specified ending); the prompt also receives planned count, remaining volume, and window volume for pacing. Planning is batched by window (long works require repeated calls). instructions is not persisted: repeat the same instructions on every batch until planning finishes. The last batch, or a later call finding no new content, also returns overall volume-checking material (total episodes, smallest-volume episodes, median volume, target volume). Check user structural preferences such as total count or chapter alignment and explicitly report deviations; normal batches report only cumulative planned count. Submission records source-file content fingerprints. If recorded files changed or disappeared (replacement / edit / deletion, even if ledger coordinates remain in bounds), planning rejects and names changed files; call reset_episode_planning for a full reset before replanning. An unhandled candidate new episode plan in the Web Episodes view also rejects planning; ask the user to accept or discard it there first."
        + TEXT_OUTPUT_TRUNCATED_NOTE
    ),
    request_model=PlanEpisodesRequest,
    migration=BLOCKED,
    domain_key="episode_plan",
    handler=plan_episodes,
    long_task=True,
    projection=_plan_structured,
)

RESET_EPISODE_PLANNING = ToolDeclaration(
    name="reset_episode_planning",
    description=(
        "Reset episode planning: an escape hatch returning the ledger to an unplanned state, without a text-model call or dependence on provider configuration. Changes only whole-source extracted episodes; preserves own-source and no-source episodes. Omit episode_id (or give the first extracted episode’s ID in playback order) for full reset: removes all extracted episodes, recovering from replaced / deleted-and-recreated source or corrupted ledgers causing persistent start-out-of-bounds / invalid-range errors, with no prevalidation and success in any damaged state. Another extracted episode’s episode_id performs partial reset: retain preceding episodes in playback order and remove that and later extracted episodes; the next planning start returns to the end of the last retained extracted episode. Partial reset validates all recorded fingerprints against current source and requires retained coordinates to be in bounds, increasing, and nonoverlapping. Failure rejects with a specific reason; use full reset instead. Subsequent plan_episodes allocates new IDs and never reuses cleared IDs. Both modes initially refuse if consumed episodes are affected (existing script_plan / script / media), returning confirmation_required=true and an impact list. Inform the user, obtain confirmation, then retry with confirm_consumed=true. After confirmation, consumed episodes become no-source, stale (source replanned), and move to the end of playback order; they retain scripts, media, and artifact records, which are not transferred to newly planned episodes. Unconsumed extracted episodes leave the ledger. Reconstructible derived episode files in reset scope are deleted; episode files without source-range records are renamed for safekeeping, preserving content. Retained derived files are unaffected."
    ),
    request_model=ResetEpisodePlanningRequest,
    migration=BLOCKED,
    domain_key="episode_reset",
    handler=reset_episode_planning,
)

EPISODE_PLANNING_TOOLS = (PLAN_EPISODES, RESET_EPISODE_PLANNING)

__all__ = ["EPISODE_PLANNING_TOOLS", "PLAN_EPISODES", "RESET_EPISODE_PLANNING"]
