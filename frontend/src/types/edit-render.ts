/**
 * Edit timeline rendering types: artifact status and submission results for final cuts and Jianying drafts.
 *
 * Matches response shapes from backend server/routers/edit_timelines.py.
 */

import type { EditTimelineIssue, TimelineNarration } from "./edit-timeline";

/** Artifact currency relative to the edit timeline; stale artifacts remain downloadable. */
export type RenderArtifactStatus = "current" | "stale" | "missing" | "blocked";

/** The two render deliverables. */
export type RenderKind = "final_cut" | "jianying_draft";

/** Whether the final cut burns subtitles into the picture. */
export type SubtitleMode = "burned_subtitles" | "no_subtitles";

/** Render options: narration variants apply to both deliverables; burned subtitles apply only to final cuts. */
export interface RenderOptions {
  narration: TimelineNarration;
  subtitles: SubtitleMode;
}

/** Jianying version: 6 means version 6 and later; 5 means 5.x. */
export type JianyingVersion = "5" | "6";

/** Rendering entry points use only issue severity, scope, and location; the edit view readout contains the full shape. */
export type EditTimelineIssueRef = Pick<EditTimelineIssue, "code" | "severity" | "applies_to" | "unit_id">;

interface RenderArtifactStatusBase {
  episode: number;
  timeline_id: string;
  status: RenderArtifactStatus;
  artifact_path: string;
  version: number | null;
  rendered_at: string | null;
}

export interface FinalCutStatus extends RenderArtifactStatusBase {
  narration: TimelineNarration;
  subtitles: SubtitleMode;
  /** Present only when the file exists; uses an anonymous media route for direct download. */
  download_url: string | null;
}

export interface JianyingDraftStatus extends RenderArtifactStatusBase {
  narration: TimelineNarration;
}

export interface RenderSubmission {
  task_id: string;
  deduped: boolean;
  artifact_path: string;
}
