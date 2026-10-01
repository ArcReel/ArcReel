/**
 * 旁白与 BGM 跟随全局时钟的判定，以及缓冲卡住时暂停全局时钟的判定。全部是纯函数；
 * useTimelinePlayback 每一帧读出媒体元素的状态，按这里的结论操作 `<audio>` 与 `<video>`。
 */

import type { AudioPlacement } from "./preview-tracks";

/** 偏差在这个范围内视为同步，不做任何纠正。 */
export const DRIFT_DEADBAND = 0.03;
/** 偏差超过这个值直接跳到应在的位置；介于两者之间时微调播放速度追上，避免频繁跳动出现爆音。 */
export const DRIFT_RESEEK = 0.25;
/** 微调播放速度的上限：最多快或慢这么多。 */
export const MAX_RATE_NUDGE = 0.1;
const NUDGE_GAIN = 1;
/** 起点前这么久就把音频对到入点，开始播放时不必再跳。 */
export const AUDIO_LOOKAHEAD = 2;
/** 起点前这么久（秒）才开始缓冲；更远的音频先不加载，长时间线打开时不会一次拉取全部音频。 */
export const AUDIO_PRELOAD_AHEAD = 10;
/** 媒体持续等待数据超过这么久（毫秒）才算缓冲卡住，吸收跳转后短暂的数据不足。 */
export const STALL_GRACE_MS = 150;

const HAVE_FUTURE_DATA = 3;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** 全局时间 `t` 上这段音频的音量：区间外为 0，淡入淡出按线性包络。 */
export function placementGain(placement: AudioPlacement, t: number): number {
  if (t < placement.start || t >= placement.end) return 0;
  let envelope = 1;
  if (placement.fadeIn > 0) envelope = Math.min(envelope, (t - placement.start) / placement.fadeIn);
  if (placement.fadeOut > 0) envelope = Math.min(envelope, (placement.end - t) / placement.fadeOut);
  return clamp(placement.volume * envelope, 0, 1);
}

/** 全局时间 `t` 上这段音频的预载方式：从起点前 {@link AUDIO_PRELOAD_AHEAD} 秒到结束之间缓冲，其余时间不加载。 */
export function audioPreload(placement: AudioPlacement, t: number): "auto" | "none" {
  return t >= placement.start - AUDIO_PRELOAD_AHEAD && t < placement.end ? "auto" : "none";
}

export interface AudioElementState {
  currentTime: number;
  paused: boolean;
  seeking: boolean;
  /** 已放到源音频结尾。 */
  ended: boolean;
}

export interface AudioDirective {
  play: boolean;
  /** 需要跳到的源音频时间；不需要时为 null。 */
  seekTo: number | null;
  playbackRate: number;
  volume: number;
}

/**
 * 一段音频此刻该怎么做。`running` 表示全局时钟在走（播放中且没有卡住缓冲）。
 * 区间内：静止时偏差超出容差就先跳到位再播；播放中偏差大则跳，偏差小则按偏差微调速度，音频超前时放慢、落后时加快。
 * 区间外：不出声；即将开始时提前对到入点。跳转进行中不再下达新的跳转。
 * 源音频比区间先放完时停在结尾：对已结束的元素调用 play 会从头重放。
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
  /** 加载失败的媒体不再等待，按没有这段媒体继续。 */
  errored: boolean;
  /** 已放到结尾的媒体没有后续数据可等。 */
  ended: boolean;
}

/** 媒体是否在等待数据：正在跳转，或手头的数据不够往下放。 */
export function mediaWaiting(media: MediaReadiness): boolean {
  return !media.errored && !media.ended && (media.seeking || media.readyState < HAVE_FUTURE_DATA);
}

export interface StallState {
  /** 开始等待的时刻（毫秒）；没在等待时为 null。 */
  since: number | null;
  stalled: boolean;
}

/** 持续等待超过宽限期即判为卡住；任何一刻不再等待就解除。 */
export function trackStall(since: number | null, waiting: boolean, now: number): StallState {
  if (!waiting) return { since: null, stalled: false };
  const start = since ?? now;
  return { since: start, stalled: now - start >= STALL_GRACE_MS };
}
