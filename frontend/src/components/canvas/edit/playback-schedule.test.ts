import { describe, expect, it } from "vitest";

import type { EditClip, EditTimelineReadout } from "@/types/edit-timeline";

import { advancePlayhead, buildPlaybackPlan, locate, preloadTarget } from "./playback-schedule";

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

function readout(clips: EditClip[], duration: number): EditTimelineReadout {
  return {
    timeline: { id: "tl-00000001", name: "初剪", episode: 1 },
    revision: 1,
    latest_revision: 1,
    duration,
    clips,
    bgm: [],
    issues: [],
  };
}

// c1 截取 1.2–4.0s；c2 的单元已从脚本删除；c3 整段 3s 并定格 1s；c4 还没有可用视频，按编排时长 2s 占位；
// c5 的截取依据 v1，current 已是 v2，整段使用 5s。
const SAMPLE = readout(
  [
    clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 2.8, trim: { source_in: 1.2, source_out: 4, basis_version: 1 } }),
    clip({ id: "c2", unit_id: "E1U2", start: 2.8, duration: 0, status: "unit_deleted", video_version: null, source_duration: null }),
    clip({ id: "c3", unit_id: "E1U3", start: 2.8, duration: 4, hold: 1, source_duration: 3 }),
    clip({ id: "c4", unit_id: "E1U4", start: 6.8, duration: 2, status: "video_missing", video_version: null, source_duration: null }),
    clip({
      id: "c5",
      unit_id: "E1U5",
      start: 8.8,
      duration: 5,
      video_version: 2,
      trim: { source_in: 0.5, source_out: 1.5, basis_version: 1 },
    }),
  ],
  13.8,
);

describe("buildPlaybackPlan", () => {
  it("skips clips whose unit was deleted and keeps the rest in play order", () => {
    const plan = buildPlaybackPlan(SAMPLE);

    expect(plan.segments.map((s) => s.clipId)).toEqual(["c1", "c3", "c4", "c5"]);
    expect(plan.duration).toBe(13.8);
  });

  it("drops clips that take no time", () => {
    const plan = buildPlaybackPlan(
      readout(
        [
          clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 0, trim: { source_in: 6, source_out: 7, basis_version: 1 } }),
          clip({ id: "c2", unit_id: "E1U2", start: 0, duration: 5 }),
        ],
        5,
      ),
    );

    expect(plan.segments.map((s) => s.clipId)).toEqual(["c2"]);
  });

  it("maps each clip to the source window actually played", () => {
    const plan = buildPlaybackPlan(SAMPLE);

    expect(plan.segments.map((s) => [s.clipId, s.start, s.end, s.sourceIn, s.sourceOut, s.hasVideo])).toEqual([
      ["c1", 0, 2.8, 1.2, 4, true],
      // 定格延长不占源素材：画面放到 3s 后停在末帧
      ["c3", 2.8, 6.8, 0, 3, true],
      ["c4", 6.8, 8.8, 0, 0, false],
      // 截取所依据的版本已不是 current：整段使用
      ["c5", 8.8, 13.8, 0, 5, true],
    ]);
  });
});

describe("locate", () => {
  const plan = buildPlaybackPlan(SAMPLE);

  it("turns a global time into the clip and its source offset", () => {
    expect(locate(plan, 1)).toEqual({ index: 0, sourceTime: 2.2, holding: false });
    expect(locate(plan, 9.3)).toEqual({ index: 3, sourceTime: 0.5, holding: false });
  });

  it("freezes on the last frame during a hold", () => {
    expect(locate(plan, 6.3)).toEqual({ index: 1, sourceTime: 3, holding: true });
  });

  it("has no source time inside a clip without usable video", () => {
    expect(locate(plan, 7)).toEqual({ index: 2, sourceTime: null, holding: false });
  });

  it("lands a cut on the following clip and clamps to the timeline ends", () => {
    expect(locate(plan, 2.8)).toEqual({ index: 1, sourceTime: 0, holding: false });
    expect(locate(plan, -3)).toEqual({ index: 0, sourceTime: 1.2, holding: false });
    expect(locate(plan, 99)).toEqual({ index: 3, sourceTime: 5, holding: false });
  });

  it("returns null when nothing is playable", () => {
    const empty = buildPlaybackPlan(
      readout([clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 0, status: "unit_deleted" })], 0),
    );

    expect(locate(empty, 0)).toBeNull();
  });
});

describe("preloadTarget", () => {
  const plan = buildPlaybackPlan(SAMPLE);

  it("preloads the in point of the next clip", () => {
    expect(preloadTarget(plan, 0)).toEqual({ index: 1, sourceTime: 0 });
  });

  it("looks past clips without usable video", () => {
    expect(preloadTarget(plan, 1)).toEqual({ index: 3, sourceTime: 0 });
  });

  it("has nothing to preload after the last clip", () => {
    expect(preloadTarget(plan, 3)).toBeNull();
  });
});

describe("advancePlayhead", () => {
  const plan = buildPlaybackPlan(SAMPLE);

  it("follows the playing video inside its source window", () => {
    expect(advancePlayhead(plan, { index: 0, t: 1 }, { videoTime: 2.5, elapsed: 0.016 })).toEqual({
      index: 0,
      t: 1.3,
      ended: false,
      holding: false,
    });
  });

  it("stands still while the video is stalled", () => {
    expect(advancePlayhead(plan, { index: 0, t: 1 }, { videoTime: 2.2, elapsed: 0.5 })).toEqual({
      index: 0,
      t: 1,
      ended: false,
      holding: false,
    });
  });

  it("runs on the wall clock through holds and clips without video", () => {
    expect(advancePlayhead(plan, { index: 1, t: 6 }, { videoTime: null, elapsed: 0.25 })).toEqual({
      index: 1,
      t: 6.25,
      ended: false,
      holding: true,
    });
    expect(advancePlayhead(plan, { index: 2, t: 7 }, { videoTime: null, elapsed: 0.5 })).toEqual({
      index: 2,
      t: 7.5,
      ended: false,
      holding: false,
    });
  });

  // 生产读取结果里的「初剪」c3：截取 0–3.2s 后定格 1s，起点 8.4s；11.6 - 8.4 在浮点下略小于 3.2
  const HOLD_AFTER_TRIM = buildPlaybackPlan(
    readout(
      [
        clip({ id: "c1", unit_id: "E1U1", start: 0, duration: 8.4 }),
        clip({
          id: "c3",
          unit_id: "E1U3",
          start: 8.4,
          duration: 4.2,
          hold: 1,
          source_duration: 8,
          trim: { source_in: 0, source_out: 3.2, basis_version: 1 },
        }),
      ],
      12.6,
    ),
  );

  it("freezes on the out point and moves on the wall clock even if the video keeps playing", () => {
    expect(advancePlayhead(HOLD_AFTER_TRIM, { index: 1, t: 11.6 }, { videoTime: 4.14, elapsed: 0.2 })).toEqual({
      index: 1,
      t: 11.8,
      ended: false,
      holding: true,
    });
    expect(locate(HOLD_AFTER_TRIM, 11.6)).toEqual({ index: 1, sourceTime: 3.2, holding: true });
  });

  it("reports the hold as soon as the video reaches the out point", () => {
    expect(advancePlayhead(HOLD_AFTER_TRIM, { index: 1, t: 11.55 }, { videoTime: 3.19, elapsed: 0.016 })).toEqual({
      index: 1,
      t: 11.6,
      ended: false,
      holding: true,
    });
  });

  it("cuts to the next clip at its start once the out point is reached", () => {
    expect(advancePlayhead(plan, { index: 0, t: 2.7 }, { videoTime: 4.02, elapsed: 0.016 })).toEqual({
      index: 1,
      t: 2.8,
      ended: false,
      holding: false,
    });
  });

  it("ends at the timeline duration after the last clip", () => {
    expect(advancePlayhead(plan, { index: 3, t: 13.7 }, { videoTime: 5, elapsed: 0.016 })).toEqual({
      index: 3,
      t: 13.8,
      ended: true,
      holding: false,
    });
  });
});
