/**
 * Edit timeline read shape, matching TimelineSummary in lib/edit_timeline/readout.py and service.py.
 * All times are seconds with up to three decimal places; absolute starts and durations are calculated by the server.
 */

/** Final cuts, Jianying drafts, and previews have variants with and without narration. */
export type TimelineNarration = "with_narration" | "without_narration";

export type EditTimelineAuthorKind = "creator" | "arcreel_agent" | "external_agent";

export interface EditTimelineAuthor {
  kind: EditTimelineAuthorKind;
  user_id: string | null;
}

/** Edit timeline reference: ID and a display name unique within the episode. */
export interface EditTimelineRef {
  id: string;
  name: string;
}

/** One edit timeline list entry (`GET /projects/{name}/edit-timelines`); `updated_*` describes the latest revision. */
export interface EditTimelineSummary extends EditTimelineRef {
  episode: number;
  revision: number;
  clip_count: number;
  created_at: string;
  updated_at: string;
  updated_by: EditTimelineAuthor;
  update_summary: string;
}

/** `unit_deleted` is skipped during rendering and counts as zero duration; `video_missing` temporarily uses the arranged duration as a placeholder. */
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
  /** Trimmed picture duration plus the freeze-frame extension. */
  duration: number;
  video_version: number | null;
  /** Full duration of the current video; null when no usable video exists. */
  source_duration: number | null;
  trim: EditClipTrim | null;
  source_volume: number;
  hold: number;
  carries_narration: boolean;
  narration: EditClipNarration | null;
  reason: string | null;
  transition_to_next: EditClipTransition | null;
}

/** BGM clip. `end` is the actual end after clipping to the timeline end; `fade_in` / `fade_out` are the effective fades after placement. */
export interface EditBgmClip {
  id: string;
  bgm_id: string;
  /** Name of the referenced BGM; null when the BGM is no longer in the project. */
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

/** Editing overview for one episode (`GET /projects/{name}/episodes/{episode}/edit-overview`). */
export interface EpisodeEditOverview {
  episode: number;
  timeline_count: number;
  /** The most recently modified timeline and its issue count; null when no edit timeline exists. */
  latest: (EditTimelineRef & { updated_at: string; issue_count: number }) | null;
  /** Timelines whose final cuts lag behind the edit timeline; excludes timelines that have never been rendered. */
  stale_final_cuts: EditTimelineRef[];
}

/** One preview subtitle cue: time is relative to the unit, measured from narration start when following narration, otherwise from source video time. */
export interface EditPreviewCue {
  start: number;
  duration: number;
  text: string;
}

/** Project-relative path and version of the current narration audio file. */
export interface EditPreviewNarrationAudio {
  path: string;
  version: number;
}

export interface EditPreviewUnitMedia {
  unit_id: string;
  /** Provider audio switch used to generate this unit's current video; when false, preview is muted even if the file has an audio track, matching final-cut behavior. */
  provider_audio: boolean;
  /** Present only when this unit is presented in the variant with narration. */
  narration_audio: EditPreviewNarrationAudio | null;
  subtitles_follow_narration: boolean;
  subtitles: EditPreviewCue[];
}

/** Project-relative BGM file path and static loudness gain (linear multiplier); preview volume equals clip volume multiplied by this gain. */
export interface EditPreviewBgm {
  bgm_id: string;
  path: string;
  gain: number;
}

/** Media layer for the edit view preview, matching server/services/presentation/timeline_preview.py. */
export interface EditTimelinePreviewMedia {
  timeline_id: string;
  revision: number;
  narration: TimelineNarration;
  units: EditPreviewUnitMedia[];
  /** Includes only BGM referenced by the BGM track whose files exist in the project. */
  bgm: EditPreviewBgm[];
}

/** One project BGM (`GET /projects/{name}/bgm` and upload results); `gain` is the linear gain normalizing loudness to −16 LUFS. */
export interface ProjectBgm {
  id: string;
  name: string;
  duration: number;
  gain: number;
  path: string;
  url: string;
}
