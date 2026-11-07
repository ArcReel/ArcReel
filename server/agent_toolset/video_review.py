"""Footage-inspection declaration: make video-unit contact sheets and deliver them as image content blocks."""

from __future__ import annotations

from lib.video_review.contact_sheet import MAX_FRAMES_PER_SHEET
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.video_review import (
    MAX_FRAMES_PER_CALL,
    MAX_FRAMES_PER_UNIT,
    InspectVideoUnitsRequest,
    inspect_video_units,
    inspect_video_units_images,
    inspect_video_units_projection,
    inspect_video_units_summary,
)

INSPECT_VIDEO_UNITS = ToolDeclaration(
    name="inspect_video_units",
    description=(
        f"Inspect video-unit imagery: extract frames from one video version per unit and assemble contact sheets, attached as image content blocks. Read-only, free. Assess material usability, defects, better candidate versions, and frame-accurate in/out points. Each sheet has at most {MAX_FRAMES_PER_SHEET} frames, labeled with unit ID and version at the top; below each frame are unit ID and frame start time in seconds, zero at the video’s first frame, on the same time axis as read_timeline trim points. Overflow frames split across sheets. version applies to every requested unit, defaulting to each current version; compare candidates with one call per version. Per-call frame budget is {MAX_FRAMES_PER_CALL}; when unit count × frames exceeds it, divide evenly by unit count, reporting frame_budget_per_unit. More than {MAX_FRAMES_PER_CALL} units returns frame_budget_exceeded; batch calls. A separate uplift rule raises a unit’s frame count to its shot count if shots exceed the unit budget, so actual total may exceed budget. Trust per-unit frame_count in units and total_frames for actual totals. On first inspection of each video version, bundled ffmpeg computes three local signals and caches them per version: BLACK for black-screen ranges, CUT for shot boundaries, FREEZE for static-frame ranges (black ranges are not double-counted). At most {MAX_FRAMES_PER_UNIT} frames per unit: if shot count fits, sample at least one frame per shot and more densely around signals; if above the cap, sample uniformly across shots, leaving some without frames. Signal-hit frames show CUT / BLACK / FREEZE below their time. BLACK and FREEZE are suspected defects: inspect imagery to distinguish generation failure, freezes, or intentional black/still frames. When trimming bad ranges, start is a candidate out-point (removing a bad ending), end a candidate in-point (removing a bad opening). CUT is structure, not a defect: it marks the first frame of a new shot, with the preceding frame the old shot’s last. Use frame-level trim points to avoid cuts mid-transition. Signals are advisory; nothing is automatically trimmed or discarded, and final judgment comes from comparing imagery. units follows request order, each with version, frame_count, available_versions (all existing video versions; others are candidates when more than one), status, sheets, and signals when ok. status ok means usable; video_missing means no usable video or absent version file; video_unreadable means decoding failure, with detail. Each sheets entry has image (one-based attachment index), times, and marked_frames with times and markers. signals contains duration_seconds, black and freeze (start/end ranges in seconds, start inclusive and end exclusive), cuts (boundary times), and shots (cut count plus one). model_review is reserved and always null. Missing units return video_unit_not_found with params.unit_ids; missing versions return version_not_found with params.available_versions."
    ),
    request_model=InspectVideoUnitsRequest,
    migration=BLOCKED,
    domain_key="inspect_video_units",
    handler=inspect_video_units,
    summary=inspect_video_units_summary,
    projection=inspect_video_units_projection,
    images=inspect_video_units_images,
)

VIDEO_REVIEW_TOOLS = (INSPECT_VIDEO_UNITS,)

__all__ = ["INSPECT_VIDEO_UNITS", "VIDEO_REVIEW_TOOLS"]
