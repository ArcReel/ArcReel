/**
 * Browser timeline playback scheduling: arrange readouts into playable segments and convert global/source times.
 *
 * Pure functions; useTimelinePlayback applies these decisions to `<video>` loading/playback/switching.
 */

import type { EditClip, EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";

/** One playable editing clip: global `[start, end)`, source `sourceIn` to `sourceOut`, then freeze extension. */
export interface PlaybackSegment {
  clipId: string;
  unitId: string;
  start: number;
  end: number;
  sourceIn: number;
  sourceOut: number;
  /** Playback volume: clip source volume × generation-provider audio switch (off means 0). */
  sourceVolume: number;
  /** Unit current video version; switching versions changes source media. */
  videoVersion: number | null;
  /** Missing usable videos use planned-duration placeholders: advance clocks without loading videos. */
  hasVideo: boolean;
  /** Cut transitions occupy half on each side: clip start fade-in/end fade-out seconds, 0 for hard cuts. */
  fadeIn: number;
  fadeOut: number;
}

export interface PlaybackPlan {
  segments: PlaybackSegment[];
  duration: number;
}

export interface PlaybackLocation {
  index: number;
  /** Source-media display time, null without usable clip video. */
  sourceTime: number | null;
  /** Picture reached the out-point and freezes on the last frame. */
  holding: boolean;
}

export interface PlayheadState {
  index: number;
  t: number;
}

export interface PlayheadSample {
  /** Advancing currentTime for the current clip; null while frozen, placeholder, or video ended. */
  videoTime: number | null;
  /** Seconds since the last sample. */
  elapsed: number;
}

/** Treat this proximity to out-point as complete, absorbing decoder frame-alignment differences. */
const OUT_POINT_TOLERANCE = 0.03;

function round(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

/** Trims apply only to their basis versions; use whole videos after switching, matching server rules. */
export function trimApplies(clip: EditClip): boolean {
  return clip.trim !== null && clip.video_version !== null && clip.trim.basis_version === clip.video_version;
}

function segmentOf(clip: EditClip, providerAudio: boolean): PlaybackSegment {
  const hasVideo = clip.status === "ready";
  const sourceIn = hasVideo && trimApplies(clip) && clip.trim ? clip.trim.source_in : 0;
  const pictureLength = hasVideo ? Math.max(0, clip.duration - clip.hold) : 0;
  return {
    clipId: clip.id,
    unitId: clip.unit_id,
    start: clip.start,
    end: round(clip.start + clip.duration),
    sourceIn,
    sourceOut: round(sourceIn + pictureLength),
    sourceVolume: providerAudio ? clip.source_volume : 0,
    videoVersion: clip.video_version,
    hasVideo,
    fadeIn: 0,
    fadeOut: 0,
  };
}

/**
 * Exclude clips whose units were deleted; other clips use server-computed starts/durations.
 * Transitions apply at cuts to the next playable clip; last-clip transitions have no effect.
 * Source volume matches final renders: clip volume × generation-provider audio switch, assumed on before preview layers arrive or without matching units.
 */
export function buildPlaybackPlan(readout: EditTimelineReadout, media: EditTimelinePreviewMedia | null): PlaybackPlan {
  const providerAudio = new Map((media?.units ?? []).map((unit) => [unit.unit_id, unit.provider_audio]));
  const clips = readout.clips.filter((clip) => clip.status !== "unit_deleted" && clip.duration > 0);
  const segments = clips.map((clip) => segmentOf(clip, providerAudio.get(clip.unit_id) !== false));
  clips.forEach((clip, index) => {
    const next = segments[index + 1];
    if (!clip.transition_to_next || !next) return;
    const half = round(clip.transition_to_next.duration / 2);
    segments[index].fadeOut = half;
    next.fadeIn = half;
  });
  return { segments, duration: readout.duration };
}

/** Approximate transitions with opacity fades: fade out before cuts, in afterward, fully black at cuts. */
export function transitionOpacity(segment: PlaybackSegment, t: number): number {
  let opacity = 1;
  if (segment.fadeIn > 0) opacity = Math.min(opacity, (t - segment.start) / segment.fadeIn);
  if (segment.fadeOut > 0) opacity = Math.min(opacity, (segment.end - t) / segment.fadeOut);
  return Math.min(Math.max(opacity, 0), 1);
}

function pictureLength(segment: PlaybackSegment): number {
  return round(segment.sourceOut - segment.sourceIn);
}

/** At global `t`, whether picture ended and freezes on the last frame; normalize offsets first so floating errors do not include out-points as picture. */
function holdingAt(segment: PlaybackSegment, t: number): boolean {
  return segment.hasVideo && round(t - segment.start) >= pictureLength(segment);
}

/** Clip/source moment at global time: cuts belong to following clips; clamp beyond both ends to start/end. */
export function locate(plan: PlaybackPlan, t: number): PlaybackLocation | null {
  const { segments } = plan;
  if (segments.length === 0) return null;
  const clamped = Math.min(Math.max(t, 0), plan.duration);
  let index = segments.findIndex((segment) => clamped >= segment.start && clamped < segment.end);
  if (index === -1) index = clamped < segments[0].start ? 0 : segments.length - 1;
  const segment = segments[index];
  if (!segment.hasVideo) return { index, sourceTime: null, holding: false };
  const offset = round(Math.min(Math.max(clamped - segment.start, 0), segment.end - segment.start));
  const length = pictureLength(segment);
  const holding = holdingAt(segment, clamped) && clamped < segment.end;
  return { index, sourceTime: round(segment.sourceIn + Math.min(offset, length)), holding };
}

/** Idle `<video>` preload: in-point of the next clip with usable video, null if none follows. */
export function preloadTarget(
  plan: PlaybackPlan,
  index: number,
): { index: number; sourceTime: number } | null {
  for (let next = index + 1; next < plan.segments.length; next += 1) {
    const segment = plan.segments[next];
    if (segment.hasVideo) return { index: next, sourceTime: segment.sourceIn };
  }
  return null;
}

/**
 * Advance playhead one frame. Picture follows video currentTime (stalls pause playheads); freezes/placeholders follow wall clock.
 * At clip end, switch to the next start; finish after the last clip.
 * `holding` means advancement reached the out-point and must freeze last frame, no longer following even still-playing video.
 */
export function advancePlayhead(
  plan: PlaybackPlan,
  state: PlayheadState,
  sample: PlayheadSample,
): PlayheadState & { ended: boolean; holding: boolean } {
  const segment = plan.segments[state.index];
  if (!segment) return { index: state.index, t: plan.duration, ended: true, holding: false };
  const length = segment.hasVideo ? pictureLength(segment) : 0;
  const inPicture = segment.hasVideo && !holdingAt(segment, state.t);
  let t: number;
  if (inPicture && sample.videoTime !== null) {
    const reachedOut = sample.videoTime >= segment.sourceOut - OUT_POINT_TOLERANCE;
    const offset = reachedOut ? length : Math.max(sample.videoTime - segment.sourceIn, 0);
    t = Math.max(state.t, segment.start + offset);
  } else {
    t = state.t + sample.elapsed;
  }
  t = round(t);
  if (t < segment.end) return { index: state.index, t, ended: false, holding: holdingAt(segment, t) };
  const next = state.index + 1;
  if (next >= plan.segments.length) return { index: state.index, t: plan.duration, ended: true, holding: false };
  const nextSegment = plan.segments[next];
  return { index: next, t: nextSegment.start, ended: false, holding: holdingAt(nextSegment, nextSegment.start) };
}
