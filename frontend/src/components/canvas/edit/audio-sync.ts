
import type { AudioPlacement } from "./preview-tracks";

export const DRIFT_DEADBAND = 0.03;
export const DRIFT_RESEEK = 0.25;
export const MAX_RATE_NUDGE = 0.1;
const NUDGE_GAIN = 1;
export const AUDIO_LOOKAHEAD = 2;
export const AUDIO_PRELOAD_AHEAD = 10;
export const STALL_GRACE_MS = 150;

const HAVE_FUTURE_DATA = 3;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function placementGain(placement: AudioPlacement, t: number): number {
  if (t < placement.start || t >= placement.end) return 0;
  let envelope = 1;
  if (placement.fadeIn > 0) envelope = Math.min(envelope, (t - placement.start) / placement.fadeIn);
  if (placement.fadeOut > 0) envelope = Math.min(envelope, (placement.end - t) / placement.fadeOut);
  return clamp(placement.volume * envelope, 0, 1);
}

export function audioPreload(placement: AudioPlacement, t: number): "auto" | "none" {
  return t >= placement.start - AUDIO_PRELOAD_AHEAD && t < placement.end ? "auto" : "none";
}

export interface AudioElementState {
  currentTime: number;
  paused: boolean;
  seeking: boolean;
  ended: boolean;
}

export interface AudioDirective {
  play: boolean;
  seekTo: number | null;
  playbackRate: number;
  volume: number;
}

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
  errored: boolean;
  ended: boolean;
}

export function mediaWaiting(media: MediaReadiness): boolean {
  return !media.errored && !media.ended && (media.seeking || media.readyState < HAVE_FUTURE_DATA);
}

export interface StallState {
  since: number | null;
  stalled: boolean;
}

export function trackStall(since: number | null, waiting: boolean, now: number): StallState {
  if (!waiting) return { since: null, stalled: false };
  const start = since ?? now;
  return { since: start, stalled: now - start >= STALL_GRACE_MS };
}
