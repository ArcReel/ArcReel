
export type TimelineNarration = "with_narration" | "without_narration";

export type EditTimelineAuthorKind = "creator" | "arcreel_agent" | "external_agent";

export interface EditTimelineAuthor {
  kind: EditTimelineAuthorKind;
  user_id: string | null;
}

export interface EditTimelineRef {
  id: string;
  name: string;
}

export interface EditTimelineSummary extends EditTimelineRef {
  episode: number;
  revision: number;
  clip_count: number;
  created_at: string;
  updated_at: string;
  updated_by: EditTimelineAuthor;
  update_summary: string;
}

export type EditClipStatus = "ready" | "video_missing" | "unit_deleted";

export interface EditClipTrim {
  source_in: number;
  source_out: number;
  basis_version: number;
}

export interface EditClipTransition {
  type: string;
  duration: number;
}

export interface EditClipNarration {
  start: number;
  end: number | null;
}

export interface EditClip {
  id: string;
  unit_id: string;
  status: EditClipStatus;
  start: number;
  duration: number;
  video_version: number | null;
  source_duration: number | null;
  trim: EditClipTrim | null;
  source_volume: number;
  hold: number;
  carries_narration: boolean;
  narration: EditClipNarration | null;
  reason: string | null;
  transition_to_next: EditClipTransition | null;
}

export interface EditBgmClip {
  id: string;
  bgm_id: string;
  name: string | null;
  start: number;
  end: number;
  source_in: number;
  source_out: number;
  volume: number;
  fade_in: number;
  fade_out: number;
}

export type EditTimelineIssueCode =
  | "trim_ignored"
  | "unit_deleted"
  | "unit_unused"
  | "video_missing"
  | "hold_too_long"
  | "narration_missing"
  | "narration_overrun"
  | "narration_source_collision"
  | "subtitle_missing_glyphs"
  | "bgm_missing";

export interface EditTimelineIssue {
  code: EditTimelineIssueCode;
  severity: "info" | "warning" | "blocking";
  applies_to: "all" | "with_narration";
  clip_ids: string[];
  unit_id: string | null;
  params: Record<string, unknown>;
}

export interface EditTimelineReadout {
  timeline: EditTimelineRef & { episode: number };
  revision: number;
  latest_revision: number;
  duration: number;
  clips: EditClip[];
  bgm: EditBgmClip[];
  issues: EditTimelineIssue[];
}

export interface EpisodeEditOverview {
  episode: number;
  timeline_count: number;
  latest: (EditTimelineRef & { updated_at: string; issue_count: number }) | null;
  stale_final_cuts: EditTimelineRef[];
}

export interface EditPreviewCue {
  start: number;
  duration: number;
  text: string;
}

export interface EditPreviewNarrationAudio {
  path: string;
  version: number;
}

export interface EditPreviewUnitMedia {
  unit_id: string;
  provider_audio: boolean;
  narration_audio: EditPreviewNarrationAudio | null;
  subtitles_follow_narration: boolean;
  subtitles: EditPreviewCue[];
}

export interface EditPreviewBgm {
  bgm_id: string;
  path: string;
  gain: number;
}

export interface EditTimelinePreviewMedia {
  timeline_id: string;
  revision: number;
  narration: TimelineNarration;
  units: EditPreviewUnitMedia[];
  bgm: EditPreviewBgm[];
}

export interface ProjectBgm {
  id: string;
  name: string;
  duration: number;
  gain: number;
  path: string;
  url: string;
}
