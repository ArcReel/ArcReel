
import type { EditTimelineIssue, TimelineNarration } from "./edit-timeline";

export type RenderArtifactStatus = "current" | "stale" | "missing" | "blocked";

export type RenderKind = "final_cut" | "jianying_draft";

export type SubtitleMode = "burned_subtitles" | "no_subtitles";

export interface RenderOptions {
  narration: TimelineNarration;
  subtitles: SubtitleMode;
}

export type JianyingVersion = "5" | "6";

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
