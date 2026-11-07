/**
 * Narration/BGM alignment to global clocks and global-clock pauses for stalled buffering. Pure functions;
 * useTimelinePlayback samples media each frame and applies these decisions to `<audio>` / `<video>`.
 */

import type { AudioPlacement } from "./preview-tracks";

/** Drift within this tolerance counts as synchronized and needs no correction. */
export const DRIFT_DEADBAND = 0.03;
/** Drift above this seeks directly; intermediate drift adjusts speed to avoid repeated seeks/clicks. */
export const DRIFT_RESEEK = 0.25;
/** Maximum playback-speed adjustment, faster or slower by at most this amount. */
export const MAX_RATE_NUDGE = 0.1;
const NUDGE_GAIN = 1;
/** Align audio to in-points this far before start, avoiding another seek when playback begins. */
export const AUDIO_LOOKAHEAD = 2;
/** Buffer only this many seconds before start; leave farther audio unloaded rather than fetching entire long timelines at opening. */
export const AUDIO_PRELOAD_AHEAD = 10;
/** Require continuous data waiting this many milliseconds before marking stalled, absorbing brief post-seek shortages. */
export const STALL_GRACE_MS = 150;

const HAVE_FUTURE_DATA = 3;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** Audio volume at global `t`: zero outside the interval, linear fade envelopes inside. */
export function placementGain(placement: AudioPlacement, t: number): number {
  if (t < placement.start || t >= placement.end) return 0;
  let envelope = 1;
  if (placement.fadeIn > 0) envelope = Math.min(envelope, (t - placement.start) / placement.fadeIn);
  if (placement.fadeOut > 0) envelope = Math.min(envelope, (placement.end - t) / placement.fadeOut);
  return clamp(placement.volume * envelope, 0, 1);
}

/** Preload at global `t`: buffer from {@link AUDIO_PRELOAD_AHEAD} seconds before start through end; otherwise leave unloaded. */
export function audioPreload(placement: AudioPlacement, t: number): "auto" | "none" {
  return t >= placement.start - AUDIO_PRELOAD_AHEAD && t < placement.end ? "auto" : "none";
}

export interface AudioElementState {
  currentTime: number;
  paused: boolean;
  seeking: boolean;
  /** Source audio reached its end. */
  ended: boolean;
}

export interface AudioDirective {
  play: boolean;
  /** Source-audio seek target, null when unnecessary. */
  seekTo: number | null;
  playbackRate: number;
  volume: number;
}

/**
 * Current audio action. `running` means global clock advances: playing without stalled buffering.
 * Inside interval: stationary drift beyond tolerance seeks before playback; playing large drift seeks, small drift adjusts speed, slower if ahead/faster if behind.
 * Outside: silence, aligning to the in-point shortly before start. Do not issue new seeks while seeking.
 * If source audio ends before the interval, stop at its end; play on ended elements would restart from the beginning.
 */
export function syncAudio(
  placement: AudioPlacement,
  t: number,
  running: boolean,
  element: AudioElementState,
): AudioDirective {
  const active = t >= placement.start && t < placement.end;
  if (!active) {
    const upcoming = t < placement.start && placement.start - t <= AUDIO_LOOKAHEAD;
    const misplaced = Math.abs(element.currentTime - placement.sourceIn) > DRIFT_DEADBAND;
    return {
      play: false,
      seekTo: upcoming && misplaced && !element.seeking ? placement.sourceIn : null,
      playbackRate: 1,
      volume: 0,
    };
  }
  const expected = placement.sourceIn + (t - placement.start);
  const drift = element.currentTime - expected;
  const volume = placementGain(placement, t);
  const outOfSync = Math.abs(drift) > DRIFT_DEADBAND;
  if (element.seeking) return { play: running, seekTo: null, playbackRate: 1, volume };
  if (element.ended && expected >= element.currentTime - DRIFT_DEADBAND) {
    return { play: false, seekTo: null, playbackRate: 1, volume };
  }
  if (!running || element.paused) {
    return { play: running, seekTo: outOfSync ? expected : null, playbackRate: 1, volume };
  }
  if (Math.abs(drift) > DRIFT_RESEEK) return { play: true, seekTo: expected, playbackRate: 1, volume };
  const playbackRate = outOfSync ? 1 - clamp(drift * NUDGE_GAIN, -MAX_RATE_NUDGE, MAX_RATE_NUDGE) : 1;
  return { play: true, seekTo: null, playbackRate, volume };
}

export interface MediaReadiness {
  readyState: number;
  seeking: boolean;
  /** Stop waiting for failed media; continue as though absent. */
  errored: boolean;
  /** Ended media has no further data to await. */
  ended: boolean;
}

/** Whether media awaits data: seeking or insufficient data to continue. */
export function mediaWaiting(media: MediaReadiness): boolean {
  return !media.errored && !media.ended && (media.seeking || media.readyState < HAVE_FUTURE_DATA);
}

export interface StallState {
  /** Wait-start time in milliseconds, null when not waiting. */
  since: number | null;
  stalled: boolean;
}

/** Continuous waiting beyond grace counts as stalled; any nonwaiting moment clears it. */
export function trackStall(since: number | null, waiting: boolean, now: number): StallState {
  if (!waiting) return { since: null, stalled: false };
  const start = since ?? now;
  return { since: start, stalled: now - start >= STALL_GRACE_MS };
}
