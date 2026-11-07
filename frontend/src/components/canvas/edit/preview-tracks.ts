/**
 * Three non-video editing tracks: place readouts/preview media layers on global time. Pure functions.
 *
 * Subtitle placement matches Jianying drafts (lib/jianying_draft/placement.py): narration subtitles attach only to carrier clips;
 * others retain source-video time and show only within in/out points. One subtitle at a time, ending when the next starts.
 */

import type { EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";
import type { PreviewAspect } from "@/utils/preview-aspect";

import type { PlaybackPlan } from "./playback-schedule";

/** Globally scheduled audio: `[start, end)`, with `start` corresponding to source-audio `sourceIn`. */
export interface AudioPlacement {
  id: string;
  kind: "narration" | "bgm";
  /** Video-unit ID for narration, BGM ID for BGM. */
  sourceId: string;
  start: number;
  end: number;
  sourceIn: number;
  volume: number;
  fadeIn: number;
  fadeOut: number;
  /** BGM name, null if removed from project; absent for narration. */
  name?: string | null;
}

/**
 * Narration-track segment. Missing TTS audio uses carrier-duration placeholders with `missingAudio` true;
 * post-production narration is silent in previews, skips audio checks, always uses `postProduction` placeholders with `missingAudio` false.
 */
export interface NarrationSpan {
  clipId: string;
  unitId: string;
  start: number;
  end: number;
  missingAudio: boolean;
  postProduction: boolean;
  /** Move down one row if overlapping previous narration, zero-based. */
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
 * Show actual narration boundaries from readouts, possibly extending onto later clips; omit portions beyond timeline end.
 * `ttsNarration` indicates project TTS delivery, matching readout issues: post-production projects do not report missing audio.
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

/** Only narration with audio in with-narration variants plays; preserve actual overlaps. */
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
 * Place BGM using readout boundaries/fades: server computes truncation/fade shortening, sharing placement with final videos/Jianying drafts.
 * Volume is clip volume × BGM loudness gain; without preview BGM layers use clip volume, displaying tracks silently.
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

/** Place subtitles in global time, only for clips with playable video; unmaterializable unit presentations have no subtitle entries. */
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

/** Displayed subtitle at global `t`, null if none. */
export function subtitleAt(subtitles: readonly PlacedSubtitle[], t: number): PlacedSubtitle | null {
  return subtitles.find((item) => t >= item.start && t < item.end) ?? null;
}

export interface SubtitleLayout {
  /** Font size as percentage of picture short edge. */
  fontSizePercentOfShortSide: number;
  /** Subtitle-center distance from picture top, in percent. */
  centerFromTopPercent: number;
  /** Maximum line width as percentage of picture width. */
  maxWidthPercent: number;
}

/**
 * Approximate subtitle position/size from Jianying draft styles (lib/jianying_draft/archive.py `_subtitle_style`):
 * portrait size 12, landscape 8; Jianying size 1 approximates 5px on a 1080 short edge. Vertical offsets -0.75 / -0.8 use picture-center half-height units;
 * line widths 0.82 / 0.6 are picture-width proportions. Approximate fonts with system bold sans-serif.
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
