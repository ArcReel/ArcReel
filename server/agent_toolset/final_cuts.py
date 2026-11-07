"""Final-cut declaration: render an edit timeline into a final cut."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.final_cuts import (
    RenderFinalCutRequest,
    final_cut_projection,
    final_cut_summary,
    render_final_cut,
)

RENDER_FINAL_CUT = ToolDeclaration(
    name="render_final_cut",
    description=(
        "Render an edit timeline into a final cut locally using bundled ffmpeg, without charges; register it as an artifact and provide a download link. Call only when the creator requests a final cut. narration and burn_subtitles determine the final-cut variant; variants coexist. The narrated variant mixes narration into episode audio; overruns render as recorded: overlaps sound simultaneously, and narration beyond the end is cut off with the film. BGM clips mix into episode audio with upload-normalized loudness multiplied by clip volume, including fades. Before submission, check the selected revision and variant; reject without queueing if applicable blocking issues exist (such as video_missing or, for narrated variants, narration_missing), returning final_cut_blocked with params.issues; with_narration on non-TTS projects returns final_cut_narration_unavailable; unavailable bundled ffmpeg returns final_cut_ffmpeg_unavailable. Corrupt video or narration version files encountered during rendering fail the task with final_cut_video_version_corrupted or final_cut_narration_version_corrupted, with unit_id and version in params. Omit revision to render the latest revision at submission; if the timeline changes during rendering or an old revision was explicitly selected, the final cut is immediately stale but downloadable. Only the latest final cut per timeline and variant is retained; rerendering overwrites it and increments version. Terminal final_cut includes revision, narration, subtitles, version, artifact_path, acceptance (expected_duration is timeline duration; video_duration and audio_duration are measured stream durations, in seconds), and warnings (warning-level issues); download_url is the download link."
    ),
    request_model=RenderFinalCutRequest,
    migration=BLOCKED,
    domain_key="final_cut",
    handler=render_final_cut,
    long_task=True,
    summary=final_cut_summary,
    projection=final_cut_projection,
)

FINAL_CUT_TOOLS = (RENDER_FINAL_CUT,)

__all__ = ["FINAL_CUT_TOOLS", "RENDER_FINAL_CUT"]
