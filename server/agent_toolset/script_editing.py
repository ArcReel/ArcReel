"""Formal-script editing declaration."""

from __future__ import annotations

from lib.i18n import _ as translate
from lib.script.storyboard_mentions import render_storyboard_mention_warnings
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.tool_runtime import PatchEpisodeScriptRequest, ScriptPatchResult, patch_episode_script


def _script_patch_summary(result: ScriptPatchResult) -> str:
    if result.success:
        lines = [
            f"✅ patch_episode_script committed atomically; revision={result.revision}; "
            f"affected_ids={', '.join(result.affected_ids) or 'none'}",
            *(f"⚠️ {line}" for line in render_storyboard_mention_warnings(result.warnings, translate)),
        ]
        if result.regeneration_required_ids:
            lines.append(
                f"⚠️ Shots with changed prompts ({', '.join(result.regeneration_required_ids)}) require immediate regeneration of their corresponding images/videos."
            )
        return "\n".join(lines)
    problem = result.problems[0]
    locations = ", ".join(
        ".".join(str(part) for part in location.path) for location in problem.locations if location.path
    )
    return (
        f"patch_episode_script failed: code={problem.code}, operation_index={problem.operation_index}, "
        f"unit_id={problem.unit_id}, location={locations or None}, next_action={problem.next_action}"
    )


PATCH_EPISODE_SCRIPT = ToolDeclaration(
    name="patch_episode_script",
    description=(
        "Atomically execute ordered script operations (update / insert / move / remove / split) by canonical revision. First build a complete candidate in memory, then validate schema, project references, Artifact Manifest, and SpeechComposition together. Any issue produces zero writes for the entire batch; success=false, problems contain stable code, operation_index, unit/field location, and next_action; speech-composition issues also include speech_admission. The server allocates insert and split entry IDs after this episode’s highest previously used sequence: gaps are normal and deleted IDs are never reissued. Read new IDs from affected_ids or reread the script. Omitted after_id inserts at the beginning, including the first entry of an empty script. remove moves the entry, media, and version history into project trash for restoration. move places id after after_id, or first if omitted, moving generated media with the entry. After moving, storyboard images whose predecessor changed (except at chapter breaks) become stale but are not listed in regeneration_required_ids; the user decides regeneration. Existing paid media are never deleted or cleared. Entries with changed image_prompt / video_prompt appear in regeneration_required_ids and must be explicitly regenerated immediately afterward; unbound @[name] mentions in visual descriptions appear in warnings. When project source exists, source_text must be a verbatim substring of this episode’s source or is rejected with source_text_not_verbatim. Missing script returns script_not_found."
    ),
    request_model=PatchEpisodeScriptRequest,
    migration=BLOCKED,
    domain_key="script_patch",
    handler=patch_episode_script,
    summary=_script_patch_summary,
    is_error=lambda result: not result.success,
)

SCRIPT_EDITING_TOOLS = (PATCH_EPISODE_SCRIPT,)

__all__ = ["PATCH_EPISODE_SCRIPT", "SCRIPT_EDITING_TOOLS"]
