/**
 * 剪辑时间线在浏览器里的播放调度：把读取结果排成可播放的段落，并在全局时间与源素材时间之间换算。
 *
 * 全部是纯函数；`<video>` 的装载、播放与切换由 useTimelinePlayback 按这里的结论执行。
 */

import type { EditClip, EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";

/** 一个参与播放的剪辑片段。全局区间为 `[start, end)`；源素材从 `sourceIn` 放到 `sourceOut`，其后是定格延长。 */
export interface PlaybackSegment {
  clipId: string;
  unitId: string;
  start: number;
  end: number;
  sourceIn: number;
  sourceOut: number;
  /** 播放音量：片段原声音量乘以生成时的供应商原声开关（关闭为 0）。 */
  sourceVolume: number;
  /** 视频单元的 current 视频版本；换版本后源素材随之变化。 */
  videoVersion: number | null;
  /** 没有可用视频的片段按编排时长占位，只走时钟，不装载视频。 */
  hasVideo: boolean;
  /** 切点上的转场各占两侧一半：片段开头淡入、结尾淡出的秒数，硬切为 0。 */
  fadeIn: number;
  fadeOut: number;
}

export interface PlaybackPlan {
  segments: PlaybackSegment[];
  duration: number;
}

export interface PlaybackLocation {
  index: number;
  /** 源素材里应显示的时间；片段没有可用视频时为 null。 */
  sourceTime: number | null;
  /** 画面已放到出点，正停在末帧上定格。 */
  holding: boolean;
}

export interface PlayheadState {
  index: number;
  t: number;
}

export interface PlayheadSample {
  /** 当前片段的视频正在推进时的 currentTime；定格、占位或视频已结束时为 null。 */
  videoTime: number | null;
  /** 距上一次采样经过的秒数。 */
  elapsed: number;
}

/** 出点前这么近即视为放完，吸收解码帧对齐造成的差额。 */
const OUT_POINT_TOLERANCE = 0.03;

function round(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

/** 截取只对其依据的视频版本有效；换版本后整段使用，与服务端的判定同口径。 */
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
 * 已从脚本删除的视频单元的片段不参与播放；其余片段沿用服务端算好的起点与时长。
 * 转场作用在到下一个参与播放的片段之间的切点上，最后一个片段上的转场没有效果。
 * 原声音量与成片同口径：片段音量乘以生成时的供应商原声开关；预览素材层还没到、或没有这个单元时按开启处理。
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

/** 转场用透明度渐变近似：切点前淡出、切点后淡入，切点上全黑。 */
export function transitionOpacity(segment: PlaybackSegment, t: number): number {
  let opacity = 1;
  if (segment.fadeIn > 0) opacity = Math.min(opacity, (t - segment.start) / segment.fadeIn);
  if (segment.fadeOut > 0) opacity = Math.min(opacity, (segment.end - t) / segment.fadeOut);
  return Math.min(Math.max(opacity, 0), 1);
}

function pictureLength(segment: PlaybackSegment): number {
  return round(segment.sourceOut - segment.sourceIn);
}

/** 全局时间 `t` 在片段内是否已放完画面、停在末帧上；偏移先规整，避免浮点误差把出点算进画面。 */
function holdingAt(segment: PlaybackSegment, t: number): boolean {
  return segment.hasVideo && round(t - segment.start) >= pictureLength(segment);
}

/** 全局时间落在哪个片段、对应源素材的哪一刻。切点属于后一个片段；超出两端时夹到起点或终点。 */
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

/** 空闲的那个 `<video>` 该预载什么：下一个有可用视频的片段的入点；后面没有时为 null。 */
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
 * 推进播放头一帧。画面部分跟随视频的 currentTime（视频卡住时播放头不动）；定格与占位片段按墙钟推进。
 * 到达片段终点时切到下一个片段的起点；最后一个片段放完即结束。
 * `holding` 表示推进后画面已到出点、应停在末帧上，此时视频即使仍在播放也不再跟随。
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
