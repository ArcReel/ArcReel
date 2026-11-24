
import type { EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";
import type { PreviewAspect } from "@/utils/preview-aspect";

import type { PlaybackPlan } from "./playback-schedule";

export interface AudioPlacement {
  id: string;
  kind: "narration" | "bgm";
  sourceId: string;
  start: number;
  end: number;
  sourceIn: number;
  volume: number;
  fadeIn: number;
  fadeOut: number;
  name?: string | null;
}

export interface NarrationSpan {
  clipId: string;
  unitId: string;
  start: number;
  end: number;
  missingAudio: boolean;
  postProduction: boolean;
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

export function subtitleAt(subtitles: readonly PlacedSubtitle[], t: number): PlacedSubtitle | null {
  return subtitles.find((item) => t >= item.start && t < item.end) ?? null;
}

export interface SubtitleLayout {
  fontSizePercentOfShortSide: number;
  centerFromTopPercent: number;
  maxWidthPercent: number;
}

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
