import { describe, expect, it } from "vitest";

import {
  AUDIO_LOOKAHEAD,
  AUDIO_PRELOAD_AHEAD,
  DRIFT_RESEEK,
  MAX_RATE_NUDGE,
  STALL_GRACE_MS,
  audioPreload,
  mediaWaiting,
  placementGain,
  syncAudio,
  trackStall,
} from "./audio-sync";
import type { AudioPlacement } from "./preview-tracks";

// 旁白从全局 4s 放到 9s，源音频从 0 开始
const NARRATION: AudioPlacement = {
  id: "narration-c2",
  kind: "narration",
  sourceId: "E1U2",
  start: 4,
  end: 9,
  sourceIn: 0,
  volume: 1,
  fadeIn: 0,
  fadeOut: 0,
};

// BGM 从全局 2s 放到 12s，取源音频 10s 起，音量 0.25，淡入淡出各 1s
const BGM: AudioPlacement = {
  id: "bgm-b1",
  kind: "bgm",
  sourceId: "bgm-0001",
  start: 2,
  end: 12,
  sourceIn: 10,
  volume: 0.25,
  fadeIn: 1,
  fadeOut: 1,
};

function playingAt(currentTime: number) {
  return { currentTime, paused: false, seeking: false, ended: false };
}

function pausedAt(currentTime: number) {
  return { currentTime, paused: true, seeking: false, ended: false };
}

describe("placementGain", () => {
  it("is silent outside the placement", () => {
    expect(placementGain(BGM, 1.99)).toBe(0);
    expect(placementGain(BGM, 12)).toBe(0);
  });

  it("ramps linearly through the fade-in and fade-out", () => {
    expect(placementGain(BGM, 2)).toBe(0);
    expect(placementGain(BGM, 2.5)).toBeCloseTo(0.125);
    expect(placementGain(BGM, 6)).toBe(0.25);
    expect(placementGain(BGM, 11.5)).toBeCloseTo(0.125);
  });

  it("keeps the full volume without fades", () => {
    expect(placementGain(NARRATION, 4)).toBe(1);
    expect(placementGain(NARRATION, 8.99)).toBe(1);
  });
});

describe("syncAudio", () => {
  it("starts a paused element at the position the global clock implies", () => {
    expect(syncAudio(NARRATION, 5.5, true, pausedAt(0))).toEqual({
      play: true,
      seekTo: 1.5,
      playbackRate: 1,
      volume: 1,
    });
  });

  it("starts without seeking when the element already waits at the right spot", () => {
    expect(syncAudio(NARRATION, 4.01, true, pausedAt(0))).toMatchObject({ play: true, seekTo: null });
  });

  it("leaves an in-sync element alone", () => {
    expect(syncAudio(BGM, 6, true, playingAt(14.02))).toEqual({
      play: true,
      seekTo: null,
      playbackRate: 1,
      volume: 0.25,
    });
  });

  it("slows down an element that runs ahead and speeds up one that lags", () => {
    const ahead = syncAudio(NARRATION, 6, true, playingAt(2.05));
    const behind = syncAudio(NARRATION, 6, true, playingAt(1.95));

    expect(ahead.seekTo).toBeNull();
    expect(ahead.playbackRate).toBeCloseTo(0.95);
    expect(behind.playbackRate).toBeCloseTo(1.05);
  });

  it("caps the speed nudge", () => {
    const directive = syncAudio(NARRATION, 6, true, playingAt(2 + DRIFT_RESEEK - 0.01));

    expect(directive.playbackRate).toBeCloseTo(1 - MAX_RATE_NUDGE);
  });

  it("jumps when the drift is too large to catch up smoothly", () => {
    expect(syncAudio(NARRATION, 6, true, playingAt(2.5))).toEqual({
      play: true,
      seekTo: 2,
      playbackRate: 1,
      volume: 1,
    });
  });

  it("does not stack jumps while one is in flight", () => {
    expect(syncAudio(NARRATION, 6, true, { currentTime: 0, paused: false, seeking: true, ended: false })).toMatchObject({
      play: true,
      seekTo: null,
    });
  });

  it("pauses with the clock and keeps the position lined up for resuming", () => {
    expect(syncAudio(NARRATION, 6, false, playingAt(2))).toEqual({
      play: false,
      seekTo: null,
      playbackRate: 1,
      volume: 1,
    });
    expect(syncAudio(NARRATION, 6, false, pausedAt(1.2))).toMatchObject({ play: false, seekTo: 2 });
  });

  it("stays silent outside the placement and cues the in point shortly before it starts", () => {
    expect(syncAudio(NARRATION, 9, true, playingAt(5))).toEqual({
      play: false,
      seekTo: null,
      playbackRate: 1,
      volume: 0,
    });
    expect(syncAudio(NARRATION, 4 - AUDIO_LOOKAHEAD - 0.1, true, pausedAt(3)).seekTo).toBeNull();
    expect(syncAudio(NARRATION, 4 - AUDIO_LOOKAHEAD + 0.1, true, pausedAt(3)).seekTo).toBe(0);
    expect(syncAudio(BGM, 1, true, pausedAt(10)).seekTo).toBeNull();
  });

  it("holds audio that ran out before its placement ends instead of replaying it from the start", () => {
    const ended = { currentTime: 4.98, paused: true, seeking: false, ended: true };

    expect(syncAudio(NARRATION, 8.99, true, ended)).toEqual({ play: false, seekTo: null, playbackRate: 1, volume: 1 });
    // 跳回区间内更早的位置时照常对齐再播
    expect(syncAudio(NARRATION, 6, true, ended)).toMatchObject({ play: true, seekTo: 2 });
  });

  it("corrects drift that accumulates over a long placement", () => {
    // 墙钟走了 60s，元素自己的时钟慢了 0.2s：微调速度追，而不是一直放任
    const longBgm = { ...BGM, start: 0, end: 120, fadeIn: 0, fadeOut: 0 };

    const directive = syncAudio(longBgm, 60, true, playingAt(10 + 60 - 0.2));

    expect(directive.seekTo).toBeNull();
    expect(directive.playbackRate).toBeCloseTo(1 + MAX_RATE_NUDGE);
  });
});

describe("audioPreload", () => {
  it("buffers from shortly before the start until the end and leaves distant audio unloaded", () => {
    const start = NARRATION.start + AUDIO_PRELOAD_AHEAD;
    const placement = { ...NARRATION, start, end: start + 5 };
    expect(audioPreload(placement, NARRATION.start - 0.1)).toBe("none");
    expect(audioPreload(placement, NARRATION.start)).toBe("auto");
    expect(audioPreload(placement, start + 4.9)).toBe("auto");
    expect(audioPreload(placement, start + 5)).toBe("none");
  });
});

describe("mediaWaiting", () => {
  it("waits while seeking or without enough data to keep playing", () => {
    expect(mediaWaiting({ readyState: 4, seeking: false, errored: false, ended: false })).toBe(false);
    expect(mediaWaiting({ readyState: 3, seeking: false, errored: false, ended: false })).toBe(false);
    expect(mediaWaiting({ readyState: 2, seeking: false, errored: false, ended: false })).toBe(true);
    expect(mediaWaiting({ readyState: 4, seeking: true, errored: false, ended: false })).toBe(true);
  });

  it("does not wait on media that failed to load or already reached its end", () => {
    expect(mediaWaiting({ readyState: 0, seeking: false, errored: true, ended: false })).toBe(false);
    expect(mediaWaiting({ readyState: 2, seeking: false, errored: false, ended: true })).toBe(false);
  });
});

describe("trackStall", () => {
  it("reports a stall only after waiting outlasts the grace period", () => {
    const first = trackStall(null, true, 1000);
    const during = trackStall(first.since, true, 1000 + STALL_GRACE_MS - 1);
    const after = trackStall(during.since, true, 1000 + STALL_GRACE_MS);

    expect(first).toEqual({ since: 1000, stalled: false });
    expect(during).toEqual({ since: 1000, stalled: false });
    expect(after).toEqual({ since: 1000, stalled: true });
  });

  it("clears as soon as the media has data again", () => {
    expect(trackStall(1000, false, 5000)).toEqual({ since: null, stalled: false });
    expect(trackStall(null, true, 6000)).toEqual({ since: 6000, stalled: false });
  });
});
