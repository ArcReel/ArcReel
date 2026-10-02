/**
 * 剪辑视图里视频轨以外的三条轨道：把读取结果与预览素材层摆到全局时间上。全部是纯函数。
 *
 * 字幕的摆放与剪映草稿同口径（lib/jianying_draft/placement.py）：跟随旁白的字幕只挂在承载旁白的片段上，
 * 其余字幕按视频源素材时间保留、只显示落在入出点之内的部分；同一时刻只显示一条，后一条开始时前一条截止。
 */

import type { EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";
import type { PreviewAspect } from "@/utils/preview-aspect";

import type { PlaybackPlan } from "./playback-schedule";

/** 一段按全局时间播放的音频：全局区间 `[start, end)`，在 `start` 时刻对应源音频的 `sourceIn`。 */
export interface AudioPlacement {
  id: string;
  kind: "narration" | "bgm";
  /** 旁白为视频单元 ID，BGM 为 BGM ID。 */
  sourceId: string;
  start: number;
  end: number;
  sourceIn: number;
  volume: number;
  fadeIn: number;
  fadeOut: number;
  /** BGM 的名称，BGM 已不在项目里时为 null；旁白不带。 */
  name?: string | null;
}

/**
 * 旁白轨上的一段旁白。TTS 项目没有旁白配音时按承载片段的长度占位，`missingAudio` 为 true；
 * 后期配音项目的旁白不在预览里出声，也不检查配音，一律按 `postProduction` 占位，`missingAudio` 恒为 false。
 */
export interface NarrationSpan {
  clipId: string;
  unitId: string;
  start: number;
  end: number;
  missingAudio: boolean;
  postProduction: boolean;
  /** 与前面的旁白重叠时下移一行，0 起。 */
  lane: number;
}

export interface PlacedSubtitle {
  start: number;
  end: number;
  text: string;
}

function round(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}

function lanesFor<T extends { start: number; end: number }>(items: readonly T[]): number[] {
  const laneEnds: number[] = [];
  return items.map((item) => {
    let lane = laneEnds.findIndex((end) => end <= item.start);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = item.end;
    return lane;
  });
}

/**
 * 旁白按读取结果的实际起止显示，可以延伸到后续片段上；超出时间线末尾的部分不画。
 * `ttsNarration` 为项目的旁白交付方式是否为 TTS 配音，与读取结果的问题列表同口径：后期配音项目不报缺配音。
 */
export function narrationSpans(readout: EditTimelineReadout, ttsNarration: boolean): NarrationSpan[] {
  const spans = readout.clips.flatMap((clip) => {
    if (clip.status === "unit_deleted" || !clip.narration) return [];
    const start = clip.narration.start;
    const end = Math.min(clip.narration.end ?? clip.start + clip.duration, readout.duration);
    if (end <= start) return [];
    return [
      {
        clipId: clip.id,
        unitId: clip.unit_id,
        start,
        end,
        missingAudio: ttsNarration && clip.narration.end === null,
        postProduction: !ttsNarration,
      },
    ];
  });
  spans.sort((a, b) => a.start - b.start);
  const lanes = lanesFor(spans);
  return spans.map((span, index) => ({ ...span, lane: lanes[index] }));
}

function unitMediaOf(media: EditTimelinePreviewMedia | null) {
  return new Map((media?.units ?? []).map((unit) => [unit.unit_id, unit]));
}

/** 有旁白配音、且按带旁白版本呈现的旁白才出声；旁白之间重叠时如实叠放。 */
export function narrationPlacements(
  readout: EditTimelineReadout,
  media: EditTimelinePreviewMedia | null,
): AudioPlacement[] {
  const units = unitMediaOf(media);
  return readout.clips.flatMap((clip) => {
    const narration = clip.narration;
    if (clip.status === "unit_deleted" || !narration || narration.end === null) return [];
    if (!units.get(clip.unit_id)?.narration_audio) return [];
    const end = Math.min(narration.end, readout.duration);
    if (end <= narration.start) return [];
    return [
      {
        id: `narration-${clip.id}`,
        kind: "narration" as const,
        sourceId: clip.unit_id,
        start: narration.start,
        end,
        sourceIn: 0,
        volume: 1,
        fadeIn: 0,
        fadeOut: 0,
      },
    ];
  });
}

/**
 * BGM 片段按读取结果的实际起止与淡入淡出摆放（截断与淡入淡出的收缩由服务端算好，与成片、剪映草稿同一份摆放）。
 * 音量是片段音量乘以 BGM 的响度增益；预览素材层还没有这首 BGM 时按片段音量摆放，只显示在轨上、不出声。
 */
export function bgmPlacements(
  readout: EditTimelineReadout,
  media: EditTimelinePreviewMedia | null,
): AudioPlacement[] {
  const gains = new Map((media?.bgm ?? []).map((item) => [item.bgm_id, item.gain]));
  return readout.bgm.flatMap((item) => {
    if (item.end <= item.start) return [];
    return [
      {
        id: `bgm-${item.id}`,
        kind: "bgm" as const,
        sourceId: item.bgm_id,
        name: item.name,
        start: item.start,
        end: item.end,
        sourceIn: item.source_in,
        volume: round(item.volume * (gains.get(item.bgm_id) ?? 1)),
        fadeIn: item.fade_in,
        fadeOut: item.fade_out,
      },
    ];
  });
}

/** 字幕摆到全局时间。只有可播放视频的片段带字幕；呈现模型物化不出的单元没有字幕条目。 */
export function placeSubtitles(
  readout: EditTimelineReadout,
  plan: PlaybackPlan,
  media: EditTimelinePreviewMedia | null,
): PlacedSubtitle[] {
  const units = unitMediaOf(media);
  const clips = new Map(readout.clips.map((clip) => [clip.id, clip]));
  const placed: PlacedSubtitle[] = [];
  for (const segment of plan.segments) {
    const unit = units.get(segment.unitId);
    const clip = clips.get(segment.clipId);
    if (!segment.hasVideo || !unit || !clip) continue;
    if (unit.subtitles_follow_narration) {
      if (!clip.carries_narration) continue;
      for (const cue of unit.subtitles) {
        placed.push({ start: round(segment.start + cue.start), end: round(segment.start + cue.start + cue.duration), text: cue.text });
      }
      continue;
    }
    for (const cue of unit.subtitles) {
      const start = Math.max(cue.start, segment.sourceIn);
      const end = Math.min(cue.start + cue.duration, segment.sourceOut);
      if (end > start) {
        placed.push({
          start: round(segment.start + start - segment.sourceIn),
          end: round(segment.start + end - segment.sourceIn),
          text: cue.text,
        });
      }
    }
  }
  placed.sort((a, b) => a.start - b.start);
  return placed.flatMap((item, index) => {
    const limit = index + 1 < placed.length ? placed[index + 1].start : plan.duration;
    const end = Math.min(item.end, limit, plan.duration);
    return end > item.start ? [{ ...item, end }] : [];
  });
}

/** 全局时间 `t` 上显示的字幕；没有时为 null。 */
export function subtitleAt(subtitles: readonly PlacedSubtitle[], t: number): PlacedSubtitle | null {
  return subtitles.find((item) => t >= item.start && t < item.end) ?? null;
}

export interface SubtitleLayout {
  /** 字号，以画面短边的百分比计。 */
  fontSizePercentOfShortSide: number;
  /** 字幕中心距画面顶部的百分比。 */
  centerFromTopPercent: number;
  /** 最大行宽，以画面宽度的百分比计。 */
  maxWidthPercent: number;
}

/**
 * 字幕在预览画面上的近似位置与字号，按剪映草稿的字幕样式（lib/jianying_draft/archive.py `_subtitle_style`）换算：
 * 竖屏字号 12、横屏字号 8，剪映字号 1 约等于 1080 短边上的 5px；纵向偏移 -0.75 / -0.8 是相对画面中心、以半高为单位；
 * 行宽 0.82 / 0.6 是画面宽度的比例。字体用系统黑体粗体近似。
 */
export function subtitleLayout(aspect: PreviewAspect): SubtitleLayout {
  const portrait = aspect === "9:16";
  const size = portrait ? 12 : 8;
  const offset = portrait ? 0.75 : 0.8;
  return {
    fontSizePercentOfShortSide: round(((size * 5) / 1080) * 100),
    centerFromTopPercent: round(50 + offset * 50),
    maxWidthPercent: portrait ? 82 : 60,
  };
}
