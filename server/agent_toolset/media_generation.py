"""Media-generation tool declarations.

Generation tools are long tasks: ArcReel Agent waits until the batch is terminal and receives ``generation_result`` and ``batch_id``; external Agents immediately receive a ``generation_batch`` handle and poll. The same result hooks project both forms; terminal ``generation_result`` matches the generation result attached to terminal batch queries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

from pydantic import BaseModel

from server.agent_toolset.declaration import BLOCKED, READ_CHECK, ScopedHandler, ToolDeclaration
from server.media_tools.assets import (
    GenerateAssetsRequest,
    ListPendingAssetsRequest,
    generate_assets,
    list_pending_assets,
)
from server.media_tools.context import (
    GenerationToolValue,
    generation_is_error,
    generation_structured,
    generation_summary,
)
from server.media_tools.image_edits import EditImagesRequest, edit_images
from server.media_tools.narration_audio import GenerateNarrationAudioRequest, generate_narration_audio
from server.media_tools.storyboards import GenerateStoryboardsRequest, generate_storyboards
from server.media_tools.videos import GenerateVideosRequest, generate_videos


@overload
def generation_tool[RequestT: BaseModel](
    *,
    name: str,
    description: str,
    request_model: type[RequestT],
    handler: ScopedHandler[RequestT, GenerationToolValue],
) -> ToolDeclaration[RequestT, GenerationToolValue]: ...


@overload
def generation_tool[RequestT: BaseModel, ResultT](
    *,
    name: str,
    description: str,
    request_model: type[RequestT],
    handler: ScopedHandler[RequestT, ResultT],
    summary: Callable[[ResultT], str | None],
    projection: Callable[[ResultT], dict[str, Any]],
    is_error: Callable[[ResultT], bool],
) -> ToolDeclaration[RequestT, ResultT]: ...


def generation_tool(
    *,
    name: str,
    description: str,
    request_model: type[BaseModel],
    handler: ScopedHandler[Any, Any],
    summary: Callable[[Any], str | None] = generation_summary,
    projection: Callable[[Any], dict[str, Any]] = generation_structured,
    is_error: Callable[[Any], bool] = generation_is_error,
) -> ToolDeclaration[Any, Any]:
    """Declare a generation long task: block migration-failed projects at entry and project results through generation hooks by default.

    If successful values have other forms (such as a grid planning preview), supply three hooks covering that form."""
    return ToolDeclaration(
        name=name,
        description=description,
        request_model=request_model,
        migration=BLOCKED,
        domain_key="generation_result",
        handler=handler,
        long_task=True,
        summary=summary,
        projection=projection,
        is_error=is_error,
    )


LIST_PENDING_ASSETS = ToolDeclaration(
    name="list_pending_assets",
    description=(
        "List characters, scenes, props, and products without asset sheets (names and description summaries); omit type to aggregate all types. Read-only, no side effects. If project-data upgrade failed, returns project_migration_failed; repair according to its details and call retry_project_migration."
    ),
    request_model=ListPendingAssetsRequest,
    migration=READ_CHECK,
    domain_key="list_pending_assets",
    handler=list_pending_assets,
)

GENERATE_ASSETS = generation_tool(
    name="generate_assets",
    description=(
        "Batch-generate character / scene / prop / product asset sheets (paid generation; writes images into the project). Assets of multiple types share one batch. When only filling missing images, stale but usable images are reused rather than automatically regenerated. Assets without description are individually blocked. Terminal generation_result gives requested / succeeded / failed / blocked outcomes by ID, such as character/张三; each failed item has a stable code and next action."
    ),
    request_model=GenerateAssetsRequest,
    handler=generate_assets,
)

GENERATE_STORYBOARDS = generation_tool(
    name="generate_storyboards",
    description=(
        "Generate storyboard images for narration or drama scripts (paid generation; images written to project storyboards/). When only filling missing images, stale but usable images are not automatically regenerated. Individually blocks shots referencing unregistered names or subjects without asset sheets, and shots without image_prompt. Generation failures are also recorded in storyboards/generation_failures.json. Terminal generation_result gives requested / succeeded / failed / blocked outcomes by ID; each failed item has a stable code and next action."
    ),
    request_model=GenerateStoryboardsRequest,
    handler=generate_storyboards,
)

EDIT_IMAGES = generation_tool(
    name="edit_images",
    description=(
        "Make instruction-based local edits to existing asset sheets / storyboard images: keep the overall original image and change only unsatisfactory parts as instructed (such as hair color, background clutter, or lighting). Supports same-type batches (paid generation; writes edited images into the project). Editing differs from regeneration: editing makes small changes using the base image and leaves the original image_prompt unchanged; later regeneration redraws from the original prompt and discards the edit. Regeneration redraws the whole image from the original prompt, replacing satisfactory parts too. Use editing for local changes; use regeneration when the user wants new composition / content or the original image_prompt itself must change. Editing always requires image-to-image (i2i); if the current project image provider lacks i2i, each ID is blocked with image_capability_missing_i2i and no generation tasks are created. Editing is always an explicit selection, with an instruction per edit. Terminal generation_result gives requested / succeeded / failed / blocked outcomes by ID."
    ),
    request_model=EditImagesRequest,
    handler=edit_images,
)

GENERATE_NARRATION_AUDIO = generation_tool(
    name="generate_narration_audio",
    description=(
        "Explicitly generate narration voiceover for units whose speech belongs to narrator, in any generation mode (TTS; paid generation; audio written to project audio/). When only filling missing voiceover, stale but usable voiceover is not automatically regenerated. Individually blocks named units whose speech is not narration, which fail speech admission, or which have no synthesizable narration text. Terminal generation_result gives requested / succeeded / failed / blocked outcomes by ID; each failed item has a stable code and next action. At worker start, synthesis text is read from canonical narrator utterances in the latest script, independent of storyboard images or videos."
    ),
    request_model=GenerateNarrationAudioRequest,
    handler=generate_narration_audio,
)

GENERATE_VIDEOS = generation_tool(
    name="generate_videos",
    description=(
        "Generate video clips for storyboard-video or reference-video projects (paid generation; videos written into the project). Existing usable videos (Manifest current / stale, or selected manually uploaded versions) are reused by default. Requested duration tiers derive from script planned duration, regardless of narration delivery. Admission is all-or-nothing: if any target fails admission (missing material, speech conflicts, pending prompts, unreadable artifact state, etc.), or needs user confirmation of cross-tier duration costs, zero tasks are enqueued and no batch is created. Results include per-unit batch_admission details and request_projections. After the user confirms tiers, resend the original full target set once; do not split calls by tier. preview=true only prechecks: same admission without queueing or batch creation, returning video_quote (per-unit generate / reuse / blocked outcome, planned duration, requested tier, tier_changed, estimated_cost, problems, plus estimated_total and confirmed_request_durations). Quotes match formal submission; preview first when costs need confirmation. For units whose price cannot be calculated, estimated_cost.amount is null with unpriced_reason, and unpriced is true; estimated_total sums only priced units, unpriced_count counts unpriced units, and the total is null if none are priced. Unpriced does not mean free: tell the user those costs are unknown and do not present the sum as the full-batch price. On formal submission after confirmation, pass quoted confirmed_request_durations unchanged to avoid another tier confirmation; if tiers changed between calls, confirmation is requested again. Terminal generation_result gives requested / succeeded / failed / blocked outcomes by ID; each failed item has a stable code and next action."
    ),
    request_model=GenerateVideosRequest,
    handler=generate_videos,
)

MEDIA_GENERATION_TOOLS = (
    LIST_PENDING_ASSETS,
    GENERATE_ASSETS,
    GENERATE_STORYBOARDS,
    EDIT_IMAGES,
    GENERATE_NARRATION_AUDIO,
    GENERATE_VIDEOS,
)

__all__ = ["MEDIA_GENERATION_TOOLS", "generation_tool"]
