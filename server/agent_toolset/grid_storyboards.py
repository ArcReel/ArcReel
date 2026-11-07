"""Storyboard-grid declarations: generate composite grids, then split with user agreement.

``generate_grid`` is a generation long task. A ``list_only`` preview is not a generation result and returns immediately under the tool name via grid-specific hooks; other values have the normal generation-tool shape. ``split_grids`` is an ordinary write tool."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.agent_toolset.media_generation import generation_tool
from server.media_tools.grid import (
    GenerateGridRequest,
    SplitGridsRequest,
    generate_grid,
    grid_is_error,
    grid_structured,
    grid_summary,
    split_grids,
)

GENERATE_GRID = generation_tool(
    name="generate_grid",
    description=(
        "Generate composite storyboard grids for storyboard projects with grid assembly enabled (generation_mode=storyboard and grid_storyboard=true; paid generation; groups follow segment_break and split across images when above the per-grid panel limit). Produces only composite images and writes no storyboard images: successful shots report their grid composite (artifact_path grids/<grid_id>.png), with status composite ready, not split. Unsplit grid_id values also appear in terminal grid_ids_awaiting_split. Batch handles and terminal batch queries lack that field: existing ready unsplit grids appear in skipped items’ artifact_path; newly generated ones appear in successful shots’ artifact_path. Ask the user to review composites in the grid panel, then call split_grids only after explicit agreement. list_only=true previews the plan without queueing, under the generate_grid key. Without scene_ids, stale but usable old images are reused and ready unsplit composites are not regenerated; in-flight tasks for the same group are reused without duplicate charges. Admission is all-or-nothing: any blocked shot (missing references, pending prompt, partial overlap with an in-flight grid, etc.) prevents tasks for the entire batch, with generation_batch_admission_withheld for otherwise healthy shots. Existing in-flight grids finish normally; their shots carry generation_active_task_conflict (action=wait_for_task). Terminal generation_result gives requested / succeeded / failed / blocked outcomes by shot ID."
    ),
    request_model=GenerateGridRequest,
    handler=generate_grid,
    summary=grid_summary,
    projection=grid_structured,
    is_error=grid_is_error,
)

SPLIT_GRIDS = ToolDeclaration(
    name="split_grids",
    description=(
        "Split one or more composite grids into storyboard images for all covered shots; old storyboard images remain in version history for rollback. Call only with explicit user agreement: after generate_grid, ask the user to review the composite in the grid panel (regenerate, replace by upload, or roll back if desired), then pass grid_id values after confirmation. Do not call automatically after generation. Obtain grid_id from generate_grid grid_ids_awaiting_split, successful-shot artifact_path (grids/<grid_id>.png), or list_only preview. Returns per-grid results: split grids list written shots; generating grids, grids without composites, or absent grids are skipped with reasons. If none are split, returns a problem with per-grid reasons in params.results."
    ),
    request_model=SplitGridsRequest,
    migration=BLOCKED,
    domain_key="split_grids",
    handler=split_grids,
)

GRID_STORYBOARD_TOOLS = (GENERATE_GRID, SPLIT_GRIDS)

__all__ = ["GENERATE_GRID", "GRID_STORYBOARD_TOOLS", "SPLIT_GRIDS"]
