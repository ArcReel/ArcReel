import { describe, expect, it } from "vitest";

import type { EditClip, EditTimelinePreviewMedia, EditTimelineReadout } from "@/types/edit-timeline";

import { buildPlaybackPlan } from "./playback-schedule";
import {
  bgmPlacements,
  narrationPlacements,
  narrationSpans,
  placeSubtitles,
  subtitleAt,
  subtitleLayout,
} from "./preview-tracks";

function clip(overrides: Partial<EditClip> & Pick<EditClip, "id" | "unit_id" | "start" | "duration">): EditClip {
  return {
    status: "ready",
    video_version: 1,
    source_duration: 5,
    trim: null,
    source_volume: 1,
    hold: 0,
    carries_narration: false,
    narration: null,
    reason: null,
    transition_to_next: null,
    ...overrides,
  };
}

// c1 截取 1–4s，旁白 5s，延伸到 c2 上；c2 整段 4s，它的旁白挂在 c3 上（c3 复用 E1U2，从 7s 开始），旁白 2s；
// c4 的单元已删除；c5 的旁白还没有配音。总长 13s。
const READOUT: EditTimelineReadout = {
  timeline: { id: "tl-00000001", name: "初剪", episode: 1 },
  revision: 2,
  latest_revision: 2,
  duration: 13,
  clips: [
    clip({
      id: "c1",
      unit_id: "E1U1",
      start: 0,
      duration: 3,
      trim: { source_in: 1, source_out: 4, basis_version: 1 },
      carries_narration: true,
      narration: { start: 0, end: 5 },
    }),
    clip({ id: "c2", unit_id: "E1U2", start: 3, duration: 4 }),
    clip({
      id: "c3",
      unit_id: "E1U2",
      start: 7,
      duration: 3,
      carries_narration: true,
      trim: { source_in: 1, source_out: 4, basis_version: 1 },
      narration: { start: 7, end: 9 },
    }),
    clip({ id: "c4", unit_id: "E1U9", start: 10, duration: 0, status: "unit_deleted", video_version: null, source_duration: null }),
    clip({ id: "c5", unit_id: "E1U5", start: 10, duration: 3, carries_narration: true, narration: { start: 10, end: null } }),
  ],
  bgm: [
    { id: "b1", bgm_id: "bgm-0001", name: "雨夜", start: 0, end: 10, source_in: 5, source_out: 15, volume: 0.25, fade_in: 1, fade_out: 1 },
    { id: "b2", bgm_id: "bgm-0001", name: "雨夜", start: 10, end: 13, source_in: 0, source_out: 8, volume: 0.25, fade_in: 1, fade_out: 1 },
  ],
  issues: [],
};

const MEDIA: EditTimelinePreviewMedia = {
  timeline_id: "tl-00000001",
  revision: 2,
  narration: "with_narration",
  units: [
    {
      unit_id: "E1U1",
      provider_audio: true,
      narration_audio: { path: "audio/E1U1.mp3", version: 1 },
      subtitles_follow_narration: true,
      subtitles: [
        { start: 0, duration: 2.5, text: "第一句。" },
        { start: 2.5, duration: 2.5, text: "第二句。" },
      ],
    },
    {
      unit_id: "E1U2",
      provider_audio: true,
      narration_audio: { path: "audio/E1U2.mp3", version: 3 },
      subtitles_follow_narration: true,
      subtitles: [{ start: 0, duration: 2, text: "第三句。" }],
    },
    {
      unit_id: "E1U5",
      provider_audio: true,
      narration_audio: null,
      subtitles_follow_narration: false,
      subtitles: [
        { start: 0, duration: 1, text: "台词一" },
        { start: 1, duration: 4, text: "台词二" },
      ],
    },
  ],
  bgm: [{ bgm_id: "bgm-0001", path: "bgm/bgm-0001.mp3", gain: 1.6 }],
};

describe("narrationSpans", () => {
  it("shows narration at its actual span, including the part that runs over later clips", () => {
    expect(narrationSpans(READOUT, true)).toEqual([
      { clipId: "c1", unitId: "E1U1", start: 0, end: 5, missingAudio: false, postProduction: false, lane: 0 },
      { clipId: "c3", unitId: "E1U2", start: 7, end: 9, missingAudio: false, postProduction: false, lane: 0 },
      { clipId: "c5", unitId: "E1U5", start: 10, end: 13, missingAudio: true, postProduction: false, lane: 0 },
    ]);
  });

  it("moves overlapping narration to another lane and clips it at the timeline end", () => {
    const overlapping: EditTimelineReadout = {
      ...READOUT,
      clips: [
        clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 3, carries_narration: true, narration: { start: 0, end: 5 } }),
        clip({ id: "c2", unit_id: "E1U2", start: 3, duration: 4, carries_narration: true, narration: { start: 3, end: 15 } }),
      ],
      duration: 7,
    };

    expect(narrationSpans(overlapping, true).map((span) => [span.clipId, span.end, span.lane])).toEqual([
      ["c1", 5, 0],
      ["c2", 7, 1],
    ]);
  });
});

describe("narrationSpans in a post-production project", () => {
  it("never reports missing narration audio and marks every span as post-production", () => {
    const spans = narrationSpans(READOUT, false);

    expect(spans.map((span) => [span.clipId, span.end, span.missingAudio, span.postProduction])).toEqual([
      ["c1", 5, false, true],
      ["c3", 9, false, true],
      ["c5", 13, false, true],
    ]);
  });
});

describe("narrationPlacements", () => {
  it("sounds only narration with audio that the preview renders with narration", () => {
    expect(narrationPlacements(READOUT, MEDIA)).toEqual([
      { id: "narration-c1", kind: "narration", sourceId: "E1U1", start: 0, end: 5, sourceIn: 0, volume: 1, fadeIn: 0, fadeOut: 0 },
      { id: "narration-c3", kind: "narration", sourceId: "E1U2", start: 7, end: 9, sourceIn: 0, volume: 1, fadeIn: 0, fadeOut: 0 },
    ]);
  });

  it("stays silent before the preview media arrives or without narration", () => {
    expect(narrationPlacements(READOUT, null)).toEqual([]);
    expect(narrationPlacements(READOUT, { ...MEDIA, units: MEDIA.units.map((unit) => ({ ...unit, narration_audio: null })) })).toEqual([]);
  });
});

describe("bgmPlacements", () => {
  it("follows the readout's placed range and fades and scales the clip volume by the loudness gain", () => {
    expect(
      bgmPlacements(READOUT, MEDIA).map((item) => [item.id, item.name, item.start, item.end, item.sourceIn, item.volume, item.fadeOut]),
    ).toEqual([
      ["bgm-b1", "雨夜", 0, 10, 5, 0.4, 1],
      ["bgm-b2", "雨夜", 10, 13, 0, 0.4, 1],
    ]);
  });

  it("keeps the clip volume until the preview media lists the BGM, and drops clips that start past the end", () => {
    const readout: EditTimelineReadout = {
      ...READOUT,
      bgm: [
        { ...READOUT.bgm[0], name: null },
        { ...READOUT.bgm[1], start: 13, end: 13 },
      ],
    };
    expect(bgmPlacements(readout, null).map((item) => [item.id, item.name, item.volume])).toEqual([["bgm-b1", null, 0.25]]);
  });
});

describe("placeSubtitles", () => {
  it("hangs narration subtitles on the carrying clip and windows source-time subtitles to the trim", () => {
    const plan = buildPlaybackPlan(READOUT, null);

    expect(placeSubtitles(READOUT, plan, MEDIA)).toEqual([
      // 旁白字幕从 c1 起点算起，延伸到 c2 上
      { start: 0, end: 2.5, text: "第一句。" },
      { start: 2.5, end: 5, text: "第二句。" },
      // c2 复用 E1U2 但不承载旁白，没有字幕；c3 承载旁白
      { start: 7, end: 9, text: "第三句。" },
      // c5 的字幕按源素材时间，整段使用
      { start: 10, end: 11, text: "台词一" },
      { start: 11, end: 13, text: "台词二" },
    ]);
  });

  it("keeps only the part of a source-time subtitle inside the trim", () => {
    const readout: EditTimelineReadout = {
      ...READOUT,
      duration: 2,
      clips: [clip({ id: "c1", unit_id: "E1U5", start: 0, duration: 2, trim: { source_in: 0.5, source_out: 2.5, basis_version: 1 } })],
      bgm: [],
    };

    expect(placeSubtitles(readout, buildPlaybackPlan(readout, null), MEDIA)).toEqual([
      { start: 0, end: 0.5, text: "台词一" },
      { start: 0.5, end: 2, text: "台词二" },
    ]);
  });

  it("cuts a subtitle short when the next one starts", () => {
    const readout: EditTimelineReadout = {
      ...READOUT,
      duration: 4,
      clips: [
        clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 2, carries_narration: true, narration: { start: 0, end: 5 } }),
        clip({ id: "c2", unit_id: "E1U5", start: 2, duration: 2 }),
      ],
      bgm: [],
    };

    // 旁白字幕 0–2.5、2.5–5 与 c2 的台词 2–3、3–4 交错：按起点排列，每条在下一条开始时截止
    expect(placeSubtitles(readout, buildPlaybackPlan(readout, null), MEDIA)).toEqual([
      { start: 0, end: 2, text: "第一句。" },
      { start: 2, end: 2.5, text: "台词一" },
      { start: 2.5, end: 3, text: "第二句。" },
      { start: 3, end: 4, text: "台词二" },
    ]);
  });

  it("has nothing to show before the preview media arrives", () => {
    expect(placeSubtitles(READOUT, buildPlaybackPlan(READOUT, null), null)).toEqual([]);
  });
});

describe("subtitleAt", () => {
  it("finds the subtitle on screen at a moment", () => {
    const subtitles = [
      { start: 0, end: 2.5, text: "第一句。" },
      { start: 2.5, end: 5, text: "第二句。" },
    ];

    expect(subtitleAt(subtitles, 2.5)?.text).toBe("第二句。");
    expect(subtitleAt(subtitles, 5)).toBeNull();
  });
});

describe("subtitleLayout", () => {
  it("scales the Jianying subtitle style to the preview frame", () => {
    expect(subtitleLayout("9:16")).toEqual({
      fontSizePercentOfShortSide: 5.555556,
      centerFromTopPercent: 87.5,
      maxWidthPercent: 82,
    });
    expect(subtitleLayout("16:9")).toEqual({
      fontSizePercentOfShortSide: 3.703704,
      centerFromTopPercent: 90,
      maxWidthPercent: 60,
    });
  });
});
